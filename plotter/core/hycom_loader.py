"""Load HYCOM ESPC-D-V02 surface fields from public NCSS (no auth).

Uses the FMRC Best Time Series for the ice/surface product, which provides
1-hourly SST, SSS, and surface currents (ssu/ssv) — the same fields the
Ocean handlers expect after renaming to water_temp / salinity / water_u / water_v.
"""

from __future__ import annotations

import shutil
import tempfile
import time
import urllib.error
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

# NCSS occasionally accepts a connection and then stalls forever (the failure
# mode that burned the first 3-hourly production run). Bound each attempt and
# retry a few times before giving up on that forecast hour.
DOWNLOAD_TIMEOUT_SEC = 180
DOWNLOAD_RETRIES = 3
DOWNLOAD_RETRY_BACKOFF_SEC = 10
MIN_DOWNLOAD_BYTES = 1000


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


def _download(
    url: str,
    cache_path: Path,
    *,
    timeout: float = DOWNLOAD_TIMEOUT_SEC,
    retries: int = DOWNLOAD_RETRIES,
) -> Path:
    """Fetch ``url`` into ``cache_path`` with a socket timeout and retries.

    Writes to a sibling ``.partial`` file first, then renames, so a killed or
    timed-out attempt never leaves a corrupt cache entry that later runs would
    happily reopen.
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists() and cache_path.stat().st_size >= MIN_DOWNLOAD_BYTES:
        return cache_path

    tmp_path = cache_path.with_suffix(cache_path.suffix + ".partial")
    last_error: Exception | None = None
    attempts = max(1, int(retries))

    for attempt in range(1, attempts + 1):
        tmp_path.unlink(missing_ok=True)
        print(f"[INFO] Downloading {url} (attempt {attempt}/{attempts})")
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp, open(
                tmp_path, "wb"
            ) as out:
                shutil.copyfileobj(resp, out, length=1024 * 1024)

            size = tmp_path.stat().st_size if tmp_path.exists() else 0
            if size < MIN_DOWNLOAD_BYTES:
                tmp_path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"Downloaded file too small ({size} bytes, likely error page): {url}"
                )

            tmp_path.replace(cache_path)
            return cache_path
        except (
            TimeoutError,
            urllib.error.URLError,
            urllib.error.HTTPError,
            OSError,
            RuntimeError,
        ) as exc:
            last_error = exc
            tmp_path.unlink(missing_ok=True)
            print(f"[WARN] HYCOM download failed (attempt {attempt}/{attempts}): {exc}")
            if attempt < attempts:
                time.sleep(DOWNLOAD_RETRY_BACKOFF_SEC * attempt)

    raise RuntimeError(
        f"HYCOM download failed after {attempts} attempt(s): {url}"
    ) from last_error


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
        cache_path = CACHE_DIR / cycle / f"f{forecast_hour:03d}_sfc.nc"
    else:
        cache_path = Path(
            tempfile.NamedTemporaryFile(suffix=".nc", delete=False).name
        )
    path = _download(url, cache_path)

    raw = xr.open_dataset(path)
    try:
        return normalize_hycom_dataset(raw)
    finally:
        raw.close()
        if not cache:
            path.unlink(missing_ok=True)


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
