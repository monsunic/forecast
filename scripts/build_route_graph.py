#!/usr/bin/env python3
"""Build the navigable graph used by Route Forecast.

Graph geometry is offline and committed: only the along-lane forecast sampling
refreshes per cycle. Sources:

``grid`` (default for ``auto``)
    Land-masked SEA sea lattice. Dynamic A* picks weather-aware tracks over
    open water without sailing across land.
``manual``
    Hand-digitized corridors from the ``routes:`` block in config.yaml.
``ais``
    Lane centerlines clustered from historical AIS tracks over the SEA AOI.
``auto``
    Prefer the sea grid; fall back to AIS then manual if a source fails.

Examples::

    python scripts/build_route_graph.py --source grid
    python scripts/build_route_graph.py --source manual --output /tmp/graph.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plotter.core.config_loader import get_route_corridors, get_sites  # noqa: E402
from plotter.core.route_graph import (  # noqa: E402
    GRAPH_PATH,
    build_graph,
    validate_graph,
)
from plotter.core.sea_grid import DEFAULT_AOI, DEFAULT_STEP_DEG, build_sea_grid_network  # noqa: E402

AIS_AOI = (-12.0, 92.0, 25.0, 128.0)  # lat_min, lon_min, lat_max, lon_max


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--source",
        choices=["auto", "grid", "manual", "ais"],
        default="auto",
        help="Graph source (default: auto = sea grid, then AIS, then manual)",
    )
    p.add_argument(
        "--step-deg",
        type=float,
        default=DEFAULT_STEP_DEG,
        help=f"Sea-grid spacing in degrees (default: {DEFAULT_STEP_DEG})",
    )
    p.add_argument(
        "--ais-days",
        type=int,
        default=3,
        help="Length of the AIS history window in days (default: 3, ~230 MB/day)",
    )
    p.add_argument(
        "--ais-max-tracks",
        type=int,
        default=400000,
        help="Cap on AIS positions pulled before clustering (default: 400000)",
    )
    p.add_argument(
        "--ais-min-count",
        type=int,
        default=None,
        help="Minimum AIS reports per grid cell for it to count as travelled",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=GRAPH_PATH,
        help=f"Output JSON path (default: {GRAPH_PATH})",
    )
    return p.parse_args()


def build_manual(sites) -> dict:
    corridors = get_route_corridors()
    if not corridors["waypoints"] or not corridors["edges"]:
        raise SystemExit(
            "[ERROR] config.yaml has no usable `routes:` block "
            "(needs both `waypoints:` and `edges:`)"
        )
    return build_graph(
        ports=sites,
        waypoints=corridors["waypoints"],
        edges=corridors["edges"],
        source="manual",
    )


def build_grid(sites, step_deg: float) -> dict:
    network = build_sea_grid_network(ports=sites, aoi=DEFAULT_AOI, step_deg=step_deg)
    meta = network.get("meta") or {}
    print(
        f"[INFO] Sea grid: step={meta.get('step_deg')}° "
        f"{meta.get('sea_cells')} wet cells, {meta.get('edges')} candidate edges",
        file=sys.stderr,
    )
    return build_graph(
        ports=sites,
        waypoints=network["waypoints"],
        edges=network["edges"],
        source="grid",
    )


def build_ais(sites, days: int, max_tracks: int, min_count: int | None) -> dict | None:
    """Return an AIS-derived graph, or None when the harvest is unusable."""
    try:
        from plotter.core.ais_lanes import harvest_lane_network
    except ImportError as exc:
        print(f"[WARN] AIS lane harvest unavailable: {exc}", file=sys.stderr)
        return None

    print(
        f"[INFO] Harvesting {days} day(s) of AIS (~230 MB/day, cached in .ais-cache). "
        "Use --source grid to skip.",
        file=sys.stderr,
    )
    try:
        network = harvest_lane_network(
            ports=sites,
            aoi=AIS_AOI,
            days=days,
            max_positions=max_tracks,
            min_cell_count=min_count,
        )
    except Exception as exc:  # noqa: BLE001 - any harvest failure falls back
        print(f"[WARN] AIS lane harvest failed: {exc}", file=sys.stderr)
        return None
    if not network:
        return None

    try:
        return build_graph(
            ports=sites,
            waypoints=network["waypoints"],
            edges=network["edges"],
            source="ais",
        )
    except ValueError as exc:
        print(f"[WARN] AIS lane graph rejected: {exc}", file=sys.stderr)
        return None


def main():
    args = parse_args()
    sites = get_sites()
    if not sites:
        raise SystemExit("[ERROR] No ports defined under `sites:` in config.yaml")

    graph = None
    if args.source == "grid":
        graph = build_grid(sites, args.step_deg)
    elif args.source == "manual":
        graph = build_manual(sites)
    elif args.source == "ais":
        graph = build_ais(
            sites, args.ais_days, args.ais_max_tracks, args.ais_min_count
        )
        if graph is None:
            raise SystemExit("[ERROR] AIS lane harvest produced no usable graph")
    else:
        # auto: grid → ais → manual
        try:
            graph = build_grid(sites, args.step_deg)
            problems = validate_graph(graph)
            if problems:
                print(
                    "[WARN] Sea grid validation failed: " + "; ".join(problems[:5]),
                    file=sys.stderr,
                )
                graph = None
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] Sea grid build failed: {exc}", file=sys.stderr)
            graph = None
        if graph is None:
            graph = build_ais(
                sites, args.ais_days, args.ais_max_tracks, args.ais_min_count
            )
            if graph is not None:
                problems = validate_graph(graph)
                if problems:
                    print(
                        "[WARN] AIS lane coverage insufficient: "
                        + "; ".join(problems[:5]),
                        file=sys.stderr,
                    )
                    graph = None
        if graph is None:
            graph = build_manual(sites)

    problems = validate_graph(graph)
    if problems:
        for problem in problems:
            print(f"[ERROR] {problem}", file=sys.stderr)
        raise SystemExit("[ERROR] Lane graph failed validation")

    graph["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if graph.get("source") == "grid":
        graph["note"] = (
            "Land-masked sea lattice for dynamic Route Forecast A*. Advisory only."
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(graph, indent=2) + "\n")

    total_nm = sum(e["distance_nm"] for e in graph["edges"])
    print(
        f"[INFO] Wrote {args.output} (source={graph['source']}, "
        f"{len(graph['nodes'])} nodes, {len(graph['edges'])} edges, "
        f"{total_nm:,.0f} nm of lanes, {len(graph['ports'])} ports)"
    )


if __name__ == "__main__":
    main()
