#!/usr/bin/env python3
"""Scan generated map assets and write assets/config/config.json.

Discovers products from plotter/config/config.yaml and merges every dataset
directory under assets/maps/ so wave and atmosphere coexist in one config.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plotter.core.config_loader import (
    get_default_max_hours,
    get_forecast_hours,
    get_hour_step,
    get_products,
    load_param_config,
)

CONFIG_PATH = ROOT / "assets" / "config" / "config.json"
MAPS_ROOT = ROOT / "assets" / "maps"

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

# Param may contain digits (e.g. rain_rh700); the 3-digit hour suffix anchors the split.
FILE_PATTERN = re.compile(r"^(?P<param>[a-z0-9_]+)_(?P<hour>\d{3})\.webp$")

# Stable forecast-type order for the UI dropdown.
FORECAST_TYPE_ORDER = ["Wind and Waves", "Atmosphere", "Ocean"]


def product_catalog():
    """Return list of {slug, ui_key, dataset, model, forecast_type} from YAML."""
    products = get_products()
    catalog = []
    for slug, meta in products.items():
        ui_key = meta.get("ui_key")
        dataset = meta.get("dataset")
        if not ui_key or not dataset:
            continue
        catalog.append(
            {
                "slug": slug,
                "ui_key": ui_key,
                "dataset": dataset,
                "model": meta.get("model") or "GFS",
                "forecast_type": meta.get("forecast_type") or "Other",
            }
        )
    return catalog


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


def canonical_hours(max_hours: Optional[int] = None, hour_step: Optional[int] = None):
    """Forecast lead hours as zero-padded strings (e.g. 000, 003, …, 072)."""
    return [f"{h:03d}" for h in get_forecast_hours(max_hours=max_hours, hour_step=hour_step)]


def _datasets_on_disk():
    if not MAPS_ROOT.is_dir():
        return []
    return sorted(
        d.name for d in MAPS_ROOT.iterdir() if d.is_dir() and not d.name.startswith(".")
    )


def build_config(
    datasets=None,
    cycles=None,
    max_hours: Optional[int] = None,
    hour_step: Optional[int] = None,
):
    """Build frontend config for one or more datasets.

    Parameters
    ----------
    datasets : list[str] | None
        Dataset directories to scan. Default: every directory under assets/maps/
        that appears in the product catalog (plus any extra on disk).
    cycles : dict[str, str] | str | None
        Per-dataset cycle (YYYYMMDDHH), or a single cycle applied to all.
    max_hours : int | None
        Last forecast lead hour (inclusive when hour_step > 1).
    hour_step : int | None
        Lead-time stride in hours (default from config.yaml).
    """
    if max_hours is None:
        max_hours = get_default_max_hours()
    if hour_step is None:
        hour_step = get_hour_step()
    catalog = product_catalog()
    catalog_by_dataset = {}
    for entry in catalog:
        catalog_by_dataset.setdefault(entry["dataset"], []).append(entry)

    if datasets is None:
        known = set(catalog_by_dataset) | set(_datasets_on_disk())
        # Prefer catalog order, then extras on disk.
        preferred = []
        for ds in ("gfswave", "gfsatmos", "hycom", "cmems", "ecmwfatmos", "ecmwfwave"):
            if ds in known:
                preferred.append(ds)
                known.discard(ds)
        datasets = preferred + sorted(known)
    elif isinstance(datasets, str):
        datasets = [datasets]

    if cycles is None:
        cycles = {}
    elif isinstance(cycles, str):
        cycles = {ds: cycles for ds in datasets}

    canon = canonical_hours(max_hours, hour_step)
    scanned = {ds: scan_dataset(MAPS_ROOT / ds) for ds in datasets}

    region_ids = list(ALL_REGIONS)
    for ds_scan in scanned.values():
        for region in ds_scan:
            if region not in region_ids:
                region_ids.append(region)

    regions = {"Select Region (or Click on Map)": {}}

    for region in region_ids:
        forecast_types = {}

        for ds in datasets:
            products = catalog_by_dataset.get(ds, [])
            if not products:
                # Fall back: expose any scanned backend params under dataset name.
                backend_params = scanned.get(ds, {}).get(region, {})
                if not backend_params:
                    continue
                ui_params = {}
                hour_sets = []
                for slug, hours_avail in sorted(backend_params.items()):
                    hours = [h for h in canon if h in hours_avail]
                    if not hours:
                        continue
                    ui_params[slug] = [f"F{h}" for h in hours]
                    hour_sets.append(set(hours))
                if not ui_params:
                    continue
                common = set.intersection(*hour_sets) if hour_sets else set()
                ft_name = ds
                entry = {
                    "parameters": ui_params,
                    "models": ["GFS"],
                    "timestamps": [f"F{h}" for h in canon if h in common],
                    "dataset": ds,
                }
                if cycles.get(ds):
                    entry["cycle"] = cycles[ds]
                forecast_types[ft_name] = entry
                continue

            backend_params = scanned.get(ds, {}).get(region, {})
            # Group products by forecast_type for this dataset.
            by_type = {}
            for entry in products:
                by_type.setdefault(entry["forecast_type"], []).append(entry)

            for ft_name, entries in by_type.items():
                ui_params = {}
                hour_sets = []
                models = []
                for entry in entries:
                    available = set(backend_params.get(entry["slug"], []))
                    hours = [h for h in canon if h in available]
                    if not hours:
                        continue
                    ui_params[entry["ui_key"]] = [f"F{h}" for h in hours]
                    hour_sets.append(set(hours))
                    if entry["model"] and entry["model"] not in models:
                        models.append(entry["model"])

                if not ui_params:
                    continue

                common = set.intersection(*hour_sets) if hour_sets else set()
                ft_entry = {
                    "parameters": ui_params,
                    "models": models or ["GFS"],
                    "timestamps": [f"F{h}" for h in canon if h in common],
                    "dataset": ds,
                }
                if cycles.get(ds):
                    ft_entry["cycle"] = cycles[ds]
                forecast_types[ft_name] = ft_entry

        # Stable ordering of forecast types.
        ordered = {}
        for name in FORECAST_TYPE_ORDER:
            if name in forecast_types:
                ordered[name] = forecast_types.pop(name)
        ordered.update(forecast_types)

        regions[region] = {"forecast_types": ordered}

    config = {"regions": regions}

    # Top-level cycle: prefer gfswave, else first available (backward compatible).
    top_cycle = cycles.get("gfswave") or next(
        (cycles[ds] for ds in datasets if cycles.get(ds)), None
    )
    if top_cycle:
        config["cycle"] = top_cycle
        config["updated"] = top_cycle
    if cycles:
        config["cycles"] = {k: v for k, v in cycles.items() if v}

    return config


def main():
    parser = argparse.ArgumentParser(description="Generate frontend config from map assets")
    parser.add_argument(
        "--dataset",
        action="append",
        dest="datasets",
        default=None,
        help="Dataset to include (repeatable). Default: all datasets on disk / in catalog.",
    )
    parser.add_argument("--cycle", default=None, help="YYYYMMDDHH model cycle (all datasets)")
    parser.add_argument(
        "--cycle-for",
        action="append",
        default=None,
        metavar="DATASET=YYYYMMDDHH",
        help="Per-dataset cycle, e.g. --cycle-for gfsatmos=2026072500",
    )
    parser.add_argument(
        "--max-hours",
        type=int,
        default=None,
        help="Last forecast lead hour (default: forecast.max_hours from config.yaml)",
    )
    parser.add_argument(
        "--hour-step",
        type=int,
        default=None,
        help="Lead-time step in hours (default: forecast.hour_step from config.yaml)",
    )
    parser.add_argument("--output", default=str(CONFIG_PATH))
    args = parser.parse_args()

    # Ensure YAML is readable (also primes catalog).
    load_param_config()
    max_hours = args.max_hours if args.max_hours is not None else get_default_max_hours()
    hour_step = args.hour_step if args.hour_step is not None else get_hour_step()

    cycles = {}
    if args.cycle:
        # Applied after datasets are known.
        pass
    if args.cycle_for:
        for item in args.cycle_for:
            if "=" not in item:
                raise SystemExit(f"[ERROR] Invalid --cycle-for value: {item}")
            ds, cyc = item.split("=", 1)
            cycles[ds.strip()] = cyc.strip()

    datasets = args.datasets
    if datasets is None:
        # Default scan: everything present.
        datasets = None
    if args.cycle:
        # Fill later once dataset list is resolved.
        pass

    config = build_config(
        datasets=datasets,
        cycles=cycles or None,
        max_hours=max_hours,
        hour_step=hour_step,
    )

    # If a global --cycle was given, stamp every forecast_type that lacks one.
    if args.cycle:
        config["cycle"] = args.cycle
        config["updated"] = args.cycle
        config.setdefault("cycles", {})
        for region, meta in config["regions"].items():
            for ft_name, ft in (meta.get("forecast_types") or {}).items():
                ds = ft.get("dataset")
                if ds and "cycle" not in ft:
                    ft["cycle"] = args.cycle
                if ds:
                    config["cycles"].setdefault(ds, args.cycle)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(config, indent=2) + "\n")

    with_data = sum(
        1
        for rid, r in config["regions"].items()
        if rid != "Select Region (or Click on Map)"
        and any(
            (ft.get("timestamps") or ft.get("parameters"))
            for ft in (r.get("forecast_types") or {}).values()
        )
    )
    n_types = {
        ft
        for r in config["regions"].values()
        for ft in (r.get("forecast_types") or {})
    }
    print(
        f"[INFO] Wrote {out} ({with_data}/{len(ALL_REGIONS)} regions with data; "
        f"types={sorted(n_types)})"
    )


if __name__ == "__main__":
    main()
