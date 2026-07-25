import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

# Non-interactive backend for headless/CI runs
os.environ.setdefault("MPLBACKEND", "Agg")

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plotter.core.plotter import Plotter
from plotter.core.plot_config import PlotConfig
from plotter.core.config_loader import (
    get_default_max_hours,
    get_forecast_hours,
    get_hour_step,
    load_param_config,
)
from plotter.core.map_assets import clear_param_maps, verify_param_maps
from plotter.core.utils import get_dataset_url, load_model_params

GFSWAVE_PARAMS = ("wind", "swh", "swell")
GFSATMOS_PARAMS = ("temp", "relhum", "mslp_wind", "rain_rh700")
HYCOM_PARAMS = ("seatemp", "seasalt", "seacurrent")
DATASET_PARAMS = {
    "gfswave": GFSWAVE_PARAMS,
    "gfsatmos": GFSATMOS_PARAMS,
    "hycom": HYCOM_PARAMS,
}


def parse_args():
    parser = argparse.ArgumentParser(description="NusaWave Plotting Engine")
    parser.add_argument(
        "--dataset",
        required=True,
        choices=["gfsatmos", "gfswave", "ecmwfatmos", "ecmwfwave", "hycom", "cmems"],
    )
    parser.add_argument("--cycle", required=True, help="YYYYMMDDHH model cycle")
    default_hours = get_default_max_hours()
    default_step = get_hour_step()
    parser.add_argument(
        "--max-hours",
        type=int,
        default=default_hours,
        help=f"Last forecast lead time in hours (default: {default_hours} from config.yaml)",
    )
    parser.add_argument(
        "--hour-step",
        type=int,
        default=default_step,
        help=f"Lead-time step in hours (default: {default_step} from config.yaml)",
    )
    parser.add_argument(
        "--MAXFORECAST",
        type=int,
        default=None,
        help="Legacy: forecast length in days (overrides --max-hours if set)",
    )
    parser.add_argument("--region", default="all", help="Region name or 'all'")
    return parser.parse_args()


def params_for_dataset(dataset):
    if dataset in DATASET_PARAMS:
        return DATASET_PARAMS[dataset]
    mapper = load_model_params(dataset)
    skip = {"time", "lon", "lat", "source"}
    return [p for p in mapper.keys() if p not in skip]


def _plot_kwargs(yaml_defaults, yaml_param):
    """Merge defaults + variable settings without duplicate nested kwargs."""
    defaults = {
        k: v for k, v in yaml_defaults.items() if k not in ("quiver", "windbarb")
    }
    return {**defaults, **yaml_param}


def main():
    args = parse_args()
    baserun = datetime.strptime(args.cycle, "%Y%m%d%H")

    url = get_dataset_url(args.dataset, args.cycle)
    print(f"[INFO] Loading dataset: {url}")

    max_lead = args.MAXFORECAST * 24 if args.MAXFORECAST is not None else args.max_hours
    hour_step = args.hour_step
    forecast_hours = get_forecast_hours(max_hours=max_lead, hour_step=hour_step)
    print(
        f"[INFO] Forecast hours ({len(forecast_hours)}): "
        f"F{forecast_hours[0]:03d} … F{forecast_hours[-1]:03d} step {hour_step}h"
    )

    yaml_cfg = load_param_config()
    yaml_defaults = yaml_cfg.get("defaults", {})
    yaml_params = yaml_cfg.get("variables", {})
    yaml_regions = yaml_cfg.get("regions", {})

    regions = [args.region] if args.region != "all" else list(yaml_regions.keys())
    params = params_for_dataset(args.dataset)
    params_load = load_model_params(args.dataset)

    maps_root = ROOT / "assets" / "maps" / args.dataset
    plot_params = [p for p in params if p in yaml_params]
    clear_param_maps(
        maps_root, regions, plot_params, forecast_hours=forecast_hours, purge_beyond=True
    )

    if args.dataset in ("gfswave", "gfsatmos", "hycom"):
        if args.dataset == "gfswave":
            from plotter.core.grib_loader import load_gfswave_forecast as load_forecast
        elif args.dataset == "gfsatmos":
            from plotter.core.grib_loader import load_gfsatmos_forecast as load_forecast
        else:
            from plotter.core.hycom_loader import load_hycom_forecast as load_forecast

        hours_done = []
        for t in forecast_hours:
            try:
                ds = load_forecast(args.cycle, t)
            except Exception as exc:
                print(f"[WARN] No data at t+{t:03d}h, stopping: {exc}")
                break

            tforecast = pd.Timestamp(ds["time"].values[0])

            for region in regions:
                for param in plot_params:
                    print(f"[INFO] Plotting {param} for region {region} at t+{t:03d}h")

                    cfg = PlotConfig(
                        dataset=args.dataset,
                        region=region,
                        time_index=None,
                        time_value=pd.Timestamp(tforecast),
                        forecast_hour=t,
                        **_plot_kwargs(yaml_defaults, yaml_params[param]),
                    )
                    if "quiver" in yaml_defaults:
                        cfg.quiver.update(yaml_defaults["quiver"])
                    if "windbarb" in yaml_defaults:
                        cfg.windbarb.update(yaml_defaults["windbarb"])

                    cfg.outfile = str(maps_root / region / f"{param}_{t:03d}")
                    cfg.baserun = baserun
                    cfg.datasource = params_load.get("source", args.dataset)

                    plotter = Plotter(cfg)
                    plotter.plot_map(ds, param)
            hours_done.append(t)

        if not hours_done:
            raise SystemExit("[ERROR] No forecast hours rendered")
        verify_param_maps(maps_root, regions, plot_params, hours_done)
        print(
            f"[INFO] Rendered {len(hours_done)} hour(s) × {len(regions)} region(s) "
            f"× {len(plot_params)} param(s)"
        )
        return

    import xarray as xr
    ds = xr.open_dataset(url, engine="netcdf4")
    time_dim = params_load.get("time", "time")
    n_times = ds.dims[time_dim]
    hours_done = [t for t in forecast_hours if t < n_times]

    for region in regions:
        for t in hours_done:
            tforecast = pd.to_datetime(ds.isel({time_dim: t})["time"].values)
            for param in plot_params:
                print(f"[INFO] Plotting {param} for region {region} at t+{t:03d}h")

                cfg = PlotConfig(
                    dataset=args.dataset,
                    region=region,
                    time_index=t,
                    time_value=tforecast,
                    forecast_hour=t,
                    **_plot_kwargs(yaml_defaults, yaml_params[param]),
                )
                if "quiver" in yaml_defaults:
                    cfg.quiver.update(yaml_defaults["quiver"])
                if "windbarb" in yaml_defaults:
                    cfg.windbarb.update(yaml_defaults["windbarb"])

                cfg.outfile = str(maps_root / region / f"{param}_{t:03d}")
                cfg.baserun = baserun
                cfg.datasource = params_load.get("source", args.dataset)

                plotter = Plotter(cfg)
                plotter.plot_map(ds, param)

    verify_param_maps(maps_root, regions, plot_params, hours_done)
    print(f"[INFO] Rendered {len(hours_done)} hour(s) × {len(regions)} region(s)")


if __name__ == "__main__":
    main()
