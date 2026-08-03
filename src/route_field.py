#!/usr/bin/env python3
"""Publish the gridded metocean field for the dynamic Route Forecast.

Writes ``assets/routes/field.json``: a coarse lat/lon grid of SWH, wind, and
surface current on the F000…F072 axis, plus a static sea mask (cells the wave
model leaves empty are land). The browser router runs a time-dependent A* over
that grid, so any origin/destination pair is planned dynamically against the
forecast each leg will actually meet — no predefined corridors.

Also refreshes ``assets/routes/vessels.json`` from the vessel profile presets.
"""

from __future__ import annotations

import argparse
import json
import shutil
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
    get_sites,
)
from plotter.core.dataset_hours import load_dataset_hour, valid_time_iso
from plotter.core.route_field import (
    FIELD_DATASETS,
    FIELD_VARS,
    build_field_doc,
    build_target_grid,
    extract_field_hour,
    flatten_round,
)

import numpy as np

ROUTES_ROOT = ROOT / "assets" / "routes"
VESSEL_PROFILES = ROOT / "plotter" / "data" / "vessel_profiles.json"

# Routing domain: covers every configured port with sea margin. Kept fixed so
# the grid indexing is stable between runs.
DEFAULT_BBOX = (98.0, 123.0, -9.0, 23.0)  # lon_min, lon_max, lat_min, lat_max
DEFAULT_RESOLUTION = 0.5  # degrees


def parse_args():
    parser = argparse.ArgumentParser(description="Monsun Route Forecast field extractor")
    parser.add_argument("--gfs-cycle", required=True, help="YYYYMMDDHH for GFS wave")
    parser.add_argument("--hycom-cycle", default=None, help="YYYYMMDDHH for HYCOM (optional)")
    parser.add_argument("--max-hours", type=int, default=None)
    parser.add_argument("--hour-step", type=int, default=None)
    parser.add_argument("--resolution", type=float, default=DEFAULT_RESOLUTION)
    parser.add_argument(
        "--datasets",
        default="gfswave,hycom",
        help="Comma-separated subset of gfswave,hycom",
    )
    parser.add_argument("--output", type=Path, default=ROUTES_ROOT / "field.json")
    return parser.parse_args()


def _cycle_for(dataset: str, gfs_cycle: str, hycom_cycle: str | None) -> str | None:
    return hycom_cycle if dataset == "hycom" else gfs_cycle


def _publish_vessels() -> None:
    if VESSEL_PROFILES.is_file():
        ROUTES_ROOT.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(VESSEL_PROFILES, ROUTES_ROOT / "vessels.json")


def run_route_field(
    gfs_cycle: str,
    hycom_cycle: str | None = None,
    max_hours: int | None = None,
    hour_step: int | None = None,
    resolution: float = DEFAULT_RESOLUTION,
    datasets=FIELD_DATASETS,
    bbox=DEFAULT_BBOX,
    output: Path | None = None,
) -> Path:
    max_hours = max_hours if max_hours is not None else get_default_max_hours()
    hour_step = hour_step if hour_step is not None else get_hour_step()
    datasets = [d for d in datasets if d in FIELD_DATASETS]
    if not datasets:
        raise SystemExit("[ERROR] No supported datasets requested for route field")

    grid = build_target_grid(bbox, resolution)
    ncell = grid["nlat"] * grid["nlon"]
    print(
        f"[INFO] Route field grid: {grid['nlon']}×{grid['nlat']} "
        f"({ncell} cells) at {resolution}° over {bbox}"
    )

    cycles = {ds: _cycle_for(ds, gfs_cycle, hycom_cycle) for ds in datasets}
    # hour_label -> {"_valid": iso, "_order": t, var_key: np.ndarray}
    hour_state: dict[str, dict] = {}
    sea_count = np.zeros(ncell, dtype=float)
    extracted: list[str] = []

    for dataset in datasets:
        cycle = cycles.get(dataset)
        if not cycle:
            print(f"[WARN] Skipping {dataset}: no cycle")
            continue
        ds_step = get_dataset_hour_step(dataset, hour_step)
        hours_done = 0

        for t in get_forecast_hours(max_hours=max_hours, hour_step=ds_step):
            try:
                ds = load_dataset_hour(dataset, cycle, t)
            except Exception as exc:
                print(f"[WARN] {dataset} no data at t+{t:03d}h, stopping: {exc}")
                break

            try:
                fields = extract_field_hour(ds, dataset, grid)
            except Exception as exc:
                print(f"[WARN] {dataset} field extract failed at t+{t:03d}h: {exc}")
                continue

            hour_label = f"F{t:03d}"
            bucket = hour_state.setdefault(
                hour_label, {"_valid": valid_time_iso(ds), "_order": t}
            )
            if not bucket["_valid"]:
                bucket["_valid"] = valid_time_iso(ds)
            for key, arr in fields.items():
                bucket[key] = arr
            if "swh" in fields:
                sea_count += np.isfinite(fields["swh"]).astype(float)

            hours_done += 1
            print(f"[INFO] Route field {dataset} t+{t:03d}h")

        if hours_done:
            extracted.append(dataset)
        else:
            print(f"[WARN] No hours extracted for {dataset}")

    if not hour_state:
        raise SystemExit("[ERROR] No forecast hours sampled for route field")

    ordered_hours = sorted(hour_state, key=lambda h: hour_state[h]["_order"])
    valid_times = [hour_state[h]["_valid"] for h in ordered_hours]

    # A cell is navigable if any hour has a valid wave height there.
    sea_mask = [1 if sea_count[c] > 0 else 0 for c in range(ncell)]

    empty = [None] * ncell
    vars_by_key: dict[str, list[list]] = {}
    for key, (dataset, ndigits) in FIELD_VARS.items():
        series = []
        for h in ordered_hours:
            arr = hour_state[h].get(key)
            series.append(empty if arr is None else flatten_round(arr, ndigits))
        vars_by_key[key] = series

    doc = build_field_doc(
        grid=grid,
        cycles={k: v for k, v in cycles.items() if v},
        hours=ordered_hours,
        valid_times=valid_times,
        sea_mask=sea_mask,
        vars_by_key=vars_by_key,
        ports=get_sites(),
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    out = Path(output) if output else ROUTES_ROOT / "field.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, separators=(",", ":"))
    out.write_text(text)
    _publish_vessels()

    wet = sum(sea_mask)
    print(
        f"[INFO] Wrote {out} ({grid['nlon']}×{grid['nlat']} grid, {wet} sea cells, "
        f"{len(ordered_hours)} hours, {len(text) // 1024} kB, datasets={extracted})"
    )
    return out


def main():
    args = parse_args()
    datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]
    run_route_field(
        gfs_cycle=args.gfs_cycle,
        hycom_cycle=args.hycom_cycle,
        max_hours=args.max_hours,
        hour_step=args.hour_step,
        resolution=args.resolution,
        datasets=datasets or FIELD_DATASETS,
        output=args.output,
    )


if __name__ == "__main__":
    main()
