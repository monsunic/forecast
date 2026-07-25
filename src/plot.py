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
from plotter.core.config_loader import get_default_max_hours, load_param_config
from plotter.core.map_assets import clear_param_maps, verify_param_maps
from plotter.core.utils import get_dataset_url, load_model_params

GFSWAVE_PARAMS = ("wind", "swh", "swell")
GFSATMOS_PARAMS = ("temp", "relhum", "mslp_wind", "rain_rh700")
DATASET_PARAMS = {
    "gfswave": GFSWAVE_PARAMS,
    "gfsatmos": GFSATMOS_PARAMS,
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
    parser.add_argument(
        "--max-hours",
        type=int,
        default=default_hours,
        help=f"Forecast length in hours (default: {default_hours} from config.yaml)",
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


def main():
    args = parse_args()
    baserun = datetime.strptime(args.cycle, "%Y%m%d%H")

    url = get_dataset_url(args.dataset, args.cycle)
    print(f"[INFO] Loading dataset: {url}")

    max_t = args.MAXFORECAST * 24 if args.MAXFORECAST is not None else args.max_hours

    yaml_cfg = load_param_config()
    yaml_defaults = yaml_cfg.get("defaults", {})
    yaml_params = yaml_cfg.get("variables", {})
    yaml_regions = yaml_cfg.get("regions", {})

    regions = [args.region] if args.region != "all" else list(yaml_regions.keys())
    params = params_for_dataset(args.dataset)
    params_load = load_model_params(args.dataset)

    maps_root = ROOT / "assets" / "maps" / args.dataset
    plot_params = [p for p in params if p in yaml_params]
    clear_param_maps(maps_root, regions, plot_params, max_hours=max_t)

    if args.dataset in ("gfswave", "gfsatmos"):
        if args.dataset == "gfswave":
            from plotter.core.grib_loader import load_gfswave_forecast as load_forecast
        else:
            from plotter.core.grib_loader import load_gfsatmos_forecast as load_forecast

        hours_completed = 0
        for t in range(max_t):
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
                        **yaml_defaults,
                        **yaml_params[param],
                    )

                    cfg.outfile = str(maps_root / region / f"{param}_{t:03d}")
                    cfg.baserun = baserun
                    cfg.datasource = params_load.get("source", args.dataset)

                    plotter = Plotter(cfg)
                    plotter.plot_map(ds, param)
            hours_completed = t + 1

        if hours_completed == 0:
            raise SystemExit("[ERROR] No forecast hours rendered")
        verify_param_maps(maps_root, regions, plot_params, hours_completed)
        print(f"[INFO] Rendered {hours_completed} hour(s) × {len(regions)} region(s)")
        return

    import xarray as xr
    ds = xr.open_dataset(url, engine="netcdf4")
    max_t = min(max_t, ds.dims["time"])
    time_dim = params_load.get("time", "time")

    for region in regions:
        for t in range(max_t):
            tforecast = pd.to_datetime(ds.isel({time_dim: t})["time"].values)
            for param in plot_params:
                print(f"[INFO] Plotting {param} for region {region} at t+{t:03d}h")

                cfg = PlotConfig(
                    dataset=args.dataset,
                    region=region,
                    time_index=t,
                    time_value=tforecast,
                    forecast_hour=t,
                    **yaml_defaults,
                    **yaml_params[param],
                )

                cfg.outfile = str(maps_root / region / f"{param}_{t:03d}")
                cfg.baserun = baserun
                cfg.datasource = params_load.get("source", args.dataset)

                plotter = Plotter(cfg)
                plotter.plot_map(ds, param)

    verify_param_maps(maps_root, regions, plot_params, max_t)
    print(f"[INFO] Rendered {max_t} hour(s) × {len(regions)} region(s)")


if __name__ == "__main__":
    main()
