"""Load HYCOM ESPC-D-V02 surface fields from public NCSS (no auth).

Uses the FMRC Best Time Series for the ice/surface product, which provides
1-hourly SST, SSS, and surface currents (ssu/ssv) — the same fields the
Ocean handlers expect after renaming to water_temp / salinity / water_u / water_v.
"""

from __future__ import annotations

import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import xarray as xr

NCSS_ICE_BEST = (
    "https://ncss.hycom.org/thredds/ncss/grid/"
    "FMRC_ESPC-D-V02_ice/FMRC_ESPC-D-V02_ice_best.ncd"
)
# Covers all configured plot regions (southeast_asia bbox).
HYCOM_BBOX = (90.0, 150.0, -20.0, 25.0)  # west, east, south, north

CACHE_DIR = Path(tempfile.gettempdir()) / "nusawave_hycom_cache"


def pick_latest_hycom_cycle() -> str:
    """Pick a recent hourly cycle likely present on the HYCOM Best series.

    ESPC surface fields typically lag wall-clock by about a day.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    adjusted = now - timedelta(hours=30)
    cycle = adjusted.replace(minute=0, second=0, microsecond=0)
    return cycle.strftime("%Y%m%d%H")


def hycom_ncss_url(cycle: str, forecast_hour: int, bbox=None) -> str:
    """Return NCSS URL for one valid time over the SE Asia bbox."""
    west, east, south, north = bbox or HYCOM_BBOX
    base = datetime.strptime(cycle, "%Y%m%d%H")
    valid = base + timedelta(hours=int(forecast_hour))
    params = [
        ("var", "sst"),
        ("var", "sss"),
        ("var", "ssu"),
        ("var", "ssv"),
        ("north", f"{north:g}"),
        ("south", f"{south:g}"),
        ("east", f"{east:g}"),
        ("west", f"{west:g}"),
        ("time", valid.strftime("%Y-%m-%dT%H:%M:%SZ")),
        ("accept", "netcdf4"),
    ]
    return f"{NCSS_ICE_BEST}?{urlencode(params)}"


def _download(url: str, cache_path: Path) -> Path:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if not cache_path.exists():
        print(f"[INFO] Downloading {url}")
        urllib.request.urlretrieve(url, cache_path)
        if cache_path.stat().st_size < 1000:
            cache_path.unlink(missing_ok=True)
            raise RuntimeError(f"Downloaded file too small (likely error page): {url}")
    return cache_path


def _normalize_coords(da: xr.DataArray) -> xr.DataArray:
    rename = {}
    if "longitude" in da.coords:
        rename["longitude"] = "lon"
    if "latitude" in da.coords:
        rename["latitude"] = "lat"
    if rename:
        da = da.rename(rename)
    return da


def normalize_hycom_dataset(ds: xr.Dataset) -> xr.Dataset:
    """Map ESPC surface names to handler variable names (lon/lat coords)."""
    out = {}

    # Prefer surface ice product names; fall back to 3z names if present.
    temp = None
    for name in ("sst", "water_temp"):
        if name in ds:
            temp = ds[name]
            break
    if temp is not None:
        out["water_temp"] = _normalize_coords(temp)

    salt = None
    for name in ("sss", "salinity"):
        if name in ds:
            salt = ds[name]
            break
    if salt is not None:
        out["salinity"] = _normalize_coords(salt)

    u = None
    v = None
    for uname, vname in (("ssu", "ssv"), ("water_u", "water_v")):
        if uname in ds and vname in ds:
            u, v = ds[uname], ds[vname]
            break
    if u is not None and v is not None:
        # Surface product has no depth; 3z would still carry depth — take surface.
        if "depth" in u.dims:
            u = u.sel(depth=0, method="nearest")
            v = v.sel(depth=0, method="nearest")
        out["water_u"] = _normalize_coords(u)
        out["water_v"] = _normalize_coords(v)

    if not out:
        raise ValueError(
            "No recognized HYCOM variables in dataset "
            f"(found: {list(ds.data_vars)})"
        )

    result = xr.Dataset(out)

    # Ensure a time dimension for plot.py / select_time.
    if "time" in ds.coords and "time" not in result.dims:
        result = result.assign_coords(time=ds["time"])
        for name in list(result.data_vars):
            if "time" not in result[name].dims:
                result[name] = result[name].expand_dims("time")

    return result


def load_hycom_forecast(cycle: str, forecast_hour: int, cache: bool = True) -> xr.Dataset:
    """Load one HYCOM surface forecast hour as a handler-compatible Dataset."""
    url = hycom_ncss_url(cycle, forecast_hour)
    if cache:
        cache_path = (
            CACHE_DIR / cycle / f"f{forecast_hour:03d}_sfc.nc"
        )
        path = _download(url, cache_path)
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".nc", delete=False)
        urllib.request.urlretrieve(url, tmp.name)
        path = Path(tmp.name)

    raw = xr.open_dataset(path)
    try:
        return normalize_hycom_dataset(raw)
    finally:
        raw.close()


def load_hycom_cycle(cycle: str, max_hours: int, hour_step: int = 1) -> xr.Dataset:
    """Load consecutive HYCOM surface forecasts into one dataset."""
    from plotter.core.config_loader import get_forecast_hours

    datasets = []
    for t in get_forecast_hours(max_hours=max_hours, hour_step=hour_step):
        try:
            ds = load_hycom_forecast(cycle, t)
            datasets.append(ds)
        except Exception as exc:
            print(f"[WARN] Stopping hycom at t+{t:03d}h: {exc}")
            break

    if not datasets:
        raise RuntimeError(f"No HYCOM data loaded for cycle {cycle}")

    return xr.concat(datasets, dim="time")
