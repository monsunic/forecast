#!/usr/bin/env python3
"""Sample forecast conditions along the lane graph for Route Forecast.

Writes ``assets/routes/forecast.json``: one condition set per lane edge per
forecast hour, on the same F000…F072 axis the maps and Site Forecast use. The
browser router turns that table into voyage times, so the heavy model reads
stay in the pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plotter.core.config_loader import (
    get_dataset_hour_step,
    get_default_max_hours,
    get_forecast_hours,
    get_hour_step,
)
from plotter.core.dataset_hours import load_dataset_hour, valid_time_iso
from plotter.core.route_extract import (
    ROUTE_SERIES_SPEC,
    aggregate_edge_hour,
    build_route_forecast_doc,
    edge_sample_points,
    merge_retained_route_series,
    node_sample_points,
)
from plotter.core.route_graph import initial_bearing_deg, load_route_graph
from plotter.core.site_extract import EXTRACTORS

ROUTES_ROOT = ROOT / "assets" / "routes"
VESSEL_PROFILES = ROOT / "plotter" / "data" / "vessel_profiles.json"


def parse_args():
    parser = argparse.ArgumentParser(description="Monsun Route Forecast extractor")
    parser.add_argument("--gfs-cycle", required=True, help="YYYYMMDDHH for GFS wave/atmos")
    parser.add_argument(
        "--hycom-cycle",
        default=None,
        help="YYYYMMDDHH for HYCOM (omit to skip current series)",
    )
    parser.add_argument(
        "--max-hours",
        type=int,
        default=None,
        help="Last forecast lead hour (default: config.yaml)",
    )
    parser.add_argument(
        "--hour-step",
        type=int,
        default=None,
        help="Lead-time step in hours (default: config.yaml)",
    )
    parser.add_argument(
        "--datasets",
        default="gfswave,hycom",
        help="Comma-separated datasets to sample (default: gfswave,hycom)",
    )
    parser.add_argument(
        "--graph",
        type=Path,
        default=None,
        help="Lane graph JSON (default: plotter/data/route_graph.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROUTES_ROOT / "forecast.json",
        help="Output JSON path (default: assets/routes/forecast.json)",
    )
    return parser.parse_args()


def _cycle_for(dataset: str, gfs_cycle: str, hycom_cycle: str | None) -> str | None:
    if dataset == "hycom":
        return hycom_cycle
    return gfs_cycle


def _round_or_none(v, ndigits=3):
    if v is None:
        return None
    try:
        return round(float(v), ndigits)
    except (TypeError, ValueError):
        return None


def _bearing(a: dict, b: dict) -> float:
    return initial_bearing_deg(a["lat"], a["lon"], b["lat"], b["lon"])


def run_route_forecast(
    gfs_cycle: str,
    hycom_cycle: str | None = None,
    max_hours: int | None = None,
    hour_step: int | None = None,
    datasets=("gfswave", "hycom"),
    graph_path: Path | None = None,
    output: Path | None = None,
) -> Path:
    max_hours = max_hours if max_hours is not None else get_default_max_hours()
    hour_step = hour_step if hour_step is not None else get_hour_step()
    datasets = [d for d in datasets if d in EXTRACTORS]
    if not datasets:
        raise SystemExit("[ERROR] No supported datasets requested for route forecast")

    graph = load_route_graph(graph_path)
    use_nodes = graph.get("source") == "grid"
    if use_nodes:
        node_points = node_sample_points(graph)
        if not node_points:
            raise SystemExit("[ERROR] Sea-grid graph has no sampleable nodes")
        print(
            f"[INFO] Route graph: {len(node_points)} nodes, "
            f"{len(graph.get('edges') or [])} edges (source=grid, node samples)"
        )
    else:
        edge_points = edge_sample_points(graph)
        if not edge_points:
            raise SystemExit("[ERROR] Lane graph has no sampleable edges")
        point_count = sum(len(p) for p in edge_points.values())
        print(
            f"[INFO] Route graph: {len(edge_points)} edges, {point_count} sample points "
            f"(source={graph.get('source')})"
        )

    cycles = {ds: _cycle_for(ds, gfs_cycle, hycom_cycle) for ds in datasets}
    # hour_label -> {"_valid": iso, "_order": lead, "edges"|"nodes": {...}}
    hour_state: dict[str, dict] = {}
    extracted: list[str] = []

    for dataset in datasets:
        cycle = cycles.get(dataset)
        if not cycle:
            print(f"[WARN] Skipping {dataset}: no cycle")
            continue
        extract = EXTRACTORS[dataset]
        ds_step = get_dataset_hour_step(dataset, hour_step)
        hours_done = 0

        for t in get_forecast_hours(max_hours=max_hours, hour_step=ds_step):
            try:
                ds = load_dataset_hour(dataset, cycle, t)
            except Exception as exc:
                print(f"[WARN] {dataset} no data at t+{t:03d}h, stopping: {exc}")
                break

            hour_label = f"F{t:03d}"
            bucket = hour_state.setdefault(
                hour_label,
                {
                    "_valid": valid_time_iso(ds),
                    "_order": t,
                    "edges": {},
                    "nodes": {},
                },
            )
            if not bucket["_valid"]:
                bucket["_valid"] = valid_time_iso(ds)

            if use_nodes:
                for node_id, (lat, lon) in node_points.items():
                    try:
                        vals = extract(ds, lat, lon)
                    except Exception:
                        continue
                    vals.pop("_grid", None)
                    if not vals:
                        continue
                    bucket["nodes"].setdefault(node_id, {}).update(
                        aggregate_edge_hour([vals])
                    )
                hours_done += 1
                print(
                    f"[INFO] Route extract {dataset} t+{t:03d}h "
                    f"({len(node_points)} nodes)"
                )
            else:
                for edge_id, points in edge_points.items():
                    point_vals = []
                    for lat, lon in points:
                        try:
                            vals = extract(ds, lat, lon)
                        except Exception:
                            continue
                        vals.pop("_grid", None)
                        point_vals.append(vals)
                    if not point_vals:
                        continue
                    bucket["edges"].setdefault(edge_id, {}).update(
                        aggregate_edge_hour(point_vals)
                    )
                hours_done += 1
                print(
                    f"[INFO] Route extract {dataset} t+{t:03d}h "
                    f"({len(edge_points)} edges)"
                )

        if hours_done:
            extracted.append(dataset)
        else:
            print(f"[WARN] No hours extracted for {dataset}")

    if not hour_state:
        raise SystemExit("[ERROR] No forecast hours sampled for route forecast")

    ordered_hours = sorted(hour_state, key=lambda h: hour_state[h]["_order"])
    valid_times = [hour_state[h]["_valid"] for h in ordered_hours]

    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    edges_out: dict[str, dict] = {}
    for edge in graph.get("edges", []):
        edge_id = edge["id"]
        entry = {
            "from": edge["from"],
            "to": edge["to"],
            "distance_nm": edge["distance_nm"],
            "bearing_deg": round(
                _bearing(nodes[edge["from"]], nodes[edge["to"]]), 1
            ),
        }
        if not use_nodes:
            series = {key: [] for key in ROUTE_SERIES_SPEC}
            for h in ordered_hours:
                vals = hour_state[h]["edges"].get(edge_id, {})
                for key in ROUTE_SERIES_SPEC:
                    series[key].append(_round_or_none(vals.get(key)))
            entry["series"] = series
        edges_out[edge_id] = entry

    node_samples = None
    if use_nodes:
        node_samples = {}
        for node_id in node_points:
            series = {key: [] for key in ROUTE_SERIES_SPEC}
            for h in ordered_hours:
                vals = hour_state[h]["nodes"].get(node_id, {})
                for key in ROUTE_SERIES_SPEC:
                    series[key].append(_round_or_none(vals.get(key)))
            node_samples[node_id] = {"series": series}

    doc = build_route_forecast_doc(
        graph=graph,
        cycles={k: v for k, v in cycles.items() if v},
        hours=ordered_hours,
        valid_times=valid_times,
        edges=edges_out,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        samples=node_samples,
    )

    out = Path(output) if output else ROUTES_ROOT / "forecast.json"
    previous = None
    if out.is_file():
        try:
            previous = json.loads(out.read_text())
        except (OSError, json.JSONDecodeError, TypeError):
            previous = None
    retained = merge_retained_route_series(doc, previous, refreshed_datasets=extracted)
    if retained:
        print(f"[INFO] Retained previous {', '.join(retained)} lane series")

    # Drop series nothing ever populated so the payload stays small.
    for edge in doc["edges"].values():
        if "series" not in edge:
            continue
        edge["series"] = {
            key: values
            for key, values in edge["series"].items()
            if any(v is not None for v in values)
        }
    for sample in (doc.get("samples") or {}).values():
        sample["series"] = {
            key: values
            for key, values in (sample.get("series") or {}).items()
            if any(v is not None for v in values)
        }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc) + "\n")
    size_kb = out.stat().st_size / 1024
    n_sample = len(doc.get("samples") or {})
    print(
        f"[INFO] Wrote {out} ({len(doc['edges'])} edges"
        + (f", {n_sample} node samples" if n_sample else "")
        + f" × {len(doc['hours'])} hours, {size_kb:.0f} kB)"
    )

    # Publish the vessel presets next to the samples so the frontend only ever
    # fetches from assets/.
    if VESSEL_PROFILES.is_file():
        (out.parent / "vessels.json").write_text(VESSEL_PROFILES.read_text())
    return out


def main():
    args = parse_args()
    datasets = [d.strip() for d in args.datasets.replace(" ", ",").split(",") if d.strip()]
    max_hours = args.max_hours if args.max_hours is not None else get_default_max_hours()
    hour_step = args.hour_step if args.hour_step is not None else get_hour_step()

    print(
        f"[INFO] Route forecast: GFS={args.gfs_cycle}, "
        f"HYCOM={args.hycom_cycle or 'skip'}, F000…F{max_hours:03d} step {hour_step}h, "
        f"datasets={datasets}"
    )
    run_route_forecast(
        gfs_cycle=args.gfs_cycle,
        hycom_cycle=args.hycom_cycle,
        max_hours=max_hours,
        hour_step=hour_step,
        datasets=datasets,
        graph_path=args.graph,
        output=args.output,
    )


if __name__ == "__main__":
    main()
