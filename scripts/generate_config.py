#!/usr/bin/env python3
"""Scan generated map assets and write assets/config/config.json."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plotter.core.config_loader import get_default_max_hours

CONFIG_PATH = ROOT / "assets" / "config" / "config.json"
MAPS_ROOT = ROOT / "assets" / "maps"

# All frontend/plotter region slugs (matches config.yaml + main.js regionDefs)
ALL_REGIONS = [
    "malacca_strait",
    "south_china_sea",
    "philippines",
    "andaman_gulf_thailand",
    "java_nusa_tenggara",
    "western_indo",
    "eastern_indo",
    "indonesia",
    "southeast_asia",
]

UI_PARAMS = {
    "surface_wind": "wind",
    "swh": "swh",
    "swell": "swell",
}

BACKEND_TO_UI = {v: k for k, v in UI_PARAMS.items()}
UI_PARAM_ORDER = ["surface_wind", "swh", "swell"]

FILE_PATTERN = re.compile(r"^(?P<param>[a-z_]+)_(?P<hour>\d{3})\.webp$")


def empty_wind_waves(dataset: str):
    return {
        "parameters": {k: [] for k in UI_PARAM_ORDER},
        "models": ["GFS"],
        "timestamps": [],
        "dataset": dataset,
    }


def scan_dataset(dataset_dir: Path):
    """Return {region: {backend_param: [hour_suffixes]}}."""
    regions = {}
    if not dataset_dir.is_dir():
        return regions

    for region_dir in sorted(dataset_dir.iterdir()):
        if not region_dir.is_dir() or region_dir.name.startswith("."):
            continue
        params = {}
        for f in sorted(region_dir.glob("*.webp")):
            m = FILE_PATTERN.match(f.name)
            if not m:
                continue
            param = m.group("param")
            hour = m.group("hour")
            params.setdefault(param, []).append(hour)
        if params:
            for p in params:
                params[p] = sorted(set(params[p]))
            regions[region_dir.name] = params
    return regions


def canonical_hours(max_hours: Optional[int] = None):
    """Fixed forecast window shared by all regions (F000 … F{max-1})."""
    if max_hours is None:
        max_hours = get_default_max_hours()
    return [f"{h:03d}" for h in range(max_hours)]


def build_config(dataset: str, cycle: Optional[str] = None, max_hours: Optional[int] = None):
    if max_hours is None:
        max_hours = get_default_max_hours()
    scanned = scan_dataset(MAPS_ROOT / dataset)
    canon = canonical_hours(max_hours)
    regions = {"Select Region (or Click on Map)": {}}

    region_ids = ALL_REGIONS + [r for r in scanned if r not in ALL_REGIONS]

    for region in region_ids:
        backend_params = scanned.get(region, {})
        if not backend_params:
            regions[region] = {
                "forecast_types": {"Wind and Waves": empty_wind_waves(dataset)}
            }
            continue

        ui_params = {}
        per_param_hours = []
        for ui_key in UI_PARAM_ORDER:
            backend = UI_PARAMS[ui_key]
            available = set(backend_params.get(backend, []))
            hours = [h for h in canon if h in available]
            ui_params[ui_key] = [f"F{h}" for h in hours]
            per_param_hours.append(set(hours))

        if per_param_hours:
            common = set.intersection(*per_param_hours) if per_param_hours else set()
            timestamps = [f"F{h}" for h in canon if h in common]
        else:
            timestamps = []

        regions[region] = {
            "forecast_types": {
                "Wind and Waves": {
                    "parameters": ui_params,
                    "models": ["GFS"],
                    "timestamps": timestamps,
                    "dataset": dataset,
                }
            }
        }

    config = {"regions": regions}
    if cycle:
        config["cycle"] = cycle
        config["updated"] = cycle
    return config


def main():
    parser = argparse.ArgumentParser(description="Generate frontend config from map assets")
    parser.add_argument("--dataset", default="gfswave")
    parser.add_argument("--cycle", default=None, help="YYYYMMDDHH model cycle")
    parser.add_argument(
        "--max-hours",
        type=int,
        default=None,
        help="Forecast hours in config (default: forecast.max_hours from config.yaml)",
    )
    parser.add_argument("--output", default=str(CONFIG_PATH))
    args = parser.parse_args()

    max_hours = args.max_hours if args.max_hours is not None else get_default_max_hours()
    config = build_config(args.dataset, args.cycle, max_hours)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(config, indent=2) + "\n")

    with_data = sum(
        1
        for rid, r in config["regions"].items()
        if rid != "Select Region (or Click on Map)"
        and r.get("forecast_types", {})
        .get("Wind and Waves", {})
        .get("timestamps")
    )
    print(f"[INFO] Wrote {out} ({with_data}/{len(ALL_REGIONS)} regions with forecast data)")


if __name__ == "__main__":
    main()
