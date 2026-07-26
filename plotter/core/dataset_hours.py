"""Per-hour dataset loading shared by the Site and Route forecast extractors."""

from __future__ import annotations

import numpy as np
import pandas as pd


def load_dataset_hour(dataset: str, cycle: str, forecast_hour: int):
    """Open one forecast hour of a dataset as an xarray Dataset.

    Loader imports stay lazy so a run that only needs GFS never pulls in the
    HYCOM/NCSS stack.
    """
    if dataset == "gfswave":
        from plotter.core.grib_loader import load_gfswave_forecast

        return load_gfswave_forecast(cycle, forecast_hour)
    if dataset == "gfsatmos":
        from plotter.core.grib_loader import load_gfsatmos_forecast

        return load_gfsatmos_forecast(cycle, forecast_hour)
    if dataset == "hycom":
        from plotter.core.hycom_loader import load_hycom_forecast

        return load_hycom_forecast(cycle, forecast_hour)
    raise ValueError(f"Unsupported dataset for point extraction: {dataset}")


def valid_time_iso(ds) -> str | None:
    """UTC valid time of a loaded hour, as ``YYYY-MM-DDTHH:MM:SSZ``."""
    if "time" not in ds.coords:
        return None
    t = pd.Timestamp(np.asarray(ds["time"].values).reshape(-1)[0])
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")
