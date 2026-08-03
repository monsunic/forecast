"""Load GFS Wave / Atmosphere data from NOMADS HTTPS GRIB2 (OpenDAP retired Feb 2026)."""

import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import cfgrib
import xarray as xr

NOMADS_GFSWAVE_BASE = (
    "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"
)
NOMADS_GFSATMOS_FILTER = (
    "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
)
# Covers all configured plot regions (southeast_asia bbox).
GFSATMOS_BBOX = (90.0, 150.0, -20.0, 25.0)  # leftlon, rightlon, bottomlat, toplat

CACHE_DIR = Path(tempfile.gettempdir()) / "monsun_grib_cache"


def gfswave_grib_url(cycle: str, forecast_hour: int) -> str:
    """Return HTTPS URL for a single GFS Wave 0.25° global GRIB2 file."""
    y, m, d, h = cycle[:4], cycle[4:6], cycle[6:8], cycle[8:10]
    fname = f"gfswave.t{h}z.global.0p25.f{forecast_hour:03d}.grib2"
    return f"{NOMADS_GFSWAVE_BASE}/gfs.{y}{m}{d}/{h}/wave/gridded/{fname}"


def gfsatmos_grib_url(cycle: str, forecast_hour: int, bbox=None) -> str:
    """Return NOMADS Grib Filter URL for GFS 0.25° atmos fields over SE Asia."""
    y, m, d, h = cycle[:4], cycle[4:6], cycle[6:8], cycle[8:10]
    left, right, bottom, top = bbox or GFSATMOS_BBOX
    query = urlencode(
        {
            "file": f"gfs.t{h}z.pgrb2.0p25.f{forecast_hour:03d}",
            "lev_mean_sea_level": "on",
            "lev_10_m_above_ground": "on",
            "lev_2_m_above_ground": "on",
            "lev_surface": "on",
            # Mid-level moisture; the filter applies levels to every selected
            # var, so TMP/UGRD/VGRD also arrive at 700 hPa and are ignored.
            "lev_700_mb": "on",
            "var_PRATE": "on",
            "var_PRMSL": "on",
            "var_RH": "on",
            "var_TMP": "on",
            "var_UGRD": "on",
            "var_VGRD": "on",
            "subregion": "",
            "leftlon": f"{left:g}",
            "rightlon": f"{right:g}",
            "toplat": f"{top:g}",
            "bottomlat": f"{bottom:g}",
            "dir": f"/gfs.{y}{m}{d}/{h}/atmos",
        }
    )
    return f"{NOMADS_GFSATMOS_FILTER}?{query}"


def pick_latest_gfswave_cycle() -> str:
    """Pick latest likely-available GFS synoptic cycle (00/06/12/18 UTC)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    adjusted = now - timedelta(hours=5)
    hour = (adjusted.hour // 6) * 6
    cycle = adjusted.replace(hour=hour, minute=0, second=0, microsecond=0)
    return cycle.strftime("%Y%m%d%H")


pick_latest_gfs_cycle = pick_latest_gfswave_cycle


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


def _open_grib_file(path: Path) -> xr.Dataset:
    parts = cfgrib.open_datasets(str(path))
    merged = xr.merge(parts, compat="override")
    return merged


def _attach_time(result: xr.Dataset, raw: xr.Dataset) -> xr.Dataset:
    time_val = None
    if "valid_time" in raw.coords:
        time_val = raw["valid_time"]
    elif "time" in raw.coords and "step" in raw.coords:
        time_val = raw["time"] + raw["step"]
    elif "time" in raw.coords:
        time_val = raw["time"]

    if time_val is None:
        return result

    result = result.assign_coords(time=time_val)
    for name in result.data_vars:
        if "time" not in result[name].dims:
            result[name] = result[name].expand_dims("time")
    return result


def normalize_gfswave_dataset(ds: xr.Dataset) -> xr.Dataset:
    """Map GRIB shortNames to legacy OpenDAP variable names used by handlers."""
    out = {}

    if "u" in ds:
        out["ugrdsfc"] = _normalize_coords(ds["u"])
    if "v" in ds:
        out["vgrdsfc"] = _normalize_coords(ds["v"])
    if "swh" in ds:
        out["htsgwsfc"] = _normalize_coords(ds["swh"])
    if "dirpw" in ds:
        out["dirpwsfc"] = _normalize_coords(ds["dirpw"])

    if "shts" in ds and "orderedSequenceData" in ds["shts"].dims:
        swell_mag = ds["shts"].isel(orderedSequenceData=0)
        out["swell_1"] = _normalize_coords(swell_mag)
    if "swdir" in ds and "orderedSequenceData" in ds["swdir"].dims:
        swell_dir = ds["swdir"].isel(orderedSequenceData=0)
        out["swdir_1"] = _normalize_coords(swell_dir)

    if not out:
        raise ValueError("No recognized GFS Wave variables in GRIB dataset")

    return _attach_time(xr.Dataset(out), ds)


def normalize_gfsatmos_dataset(ds: xr.Dataset) -> xr.Dataset:
    """Map atmospheric GRIB fields to handler variable names with display units.

    - PRATE (kg/m²/s) kept as-is; RainrateHandler multiplies by 3600 → mm/hr
    - TMP 2 m converted Kelvin → °C
    - RH 2 m and RH 700 hPa kept as %
    - PRMSL kept as Pa; MslpHandler divides by 100 → hPa
    """
    out = {}

    # Precipitation rate (surface)
    if "prate" in ds:
        out["apcpsfc"] = _normalize_coords(ds["prate"])
    elif "tp" in ds:
        out["apcpsfc"] = _normalize_coords(ds["tp"])

    # 2 m temperature
    if "t2m" in ds:
        out["tmpsfc"] = _normalize_coords(ds["t2m"] - 273.15)
    elif "2t" in ds:
        out["tmpsfc"] = _normalize_coords(ds["2t"] - 273.15)

    # 2 m relative humidity
    if "r2" in ds:
        out["rh2msfc"] = _normalize_coords(ds["r2"])
    elif "rh" in ds:
        out["rh2msfc"] = _normalize_coords(ds["rh"])

    # 700 hPa relative humidity. Only accept a field carrying an isobaric
    # coordinate so a near-surface RH named "r" can never be mistaken for it.
    for name in ("r", "rh"):
        if name not in ds or "isobaricInhPa" not in ds[name].coords:
            continue
        rh700 = ds[name]
        if "isobaricInhPa" in rh700.dims:
            rh700 = rh700.sel(isobaricInhPa=700, method="nearest")
        out["rh700mb"] = _normalize_coords(
            rh700.drop_vars("isobaricInhPa", errors="ignore")
        )
        break

    # Mean sea level pressure
    if "prmsl" in ds:
        out["prmslmsl"] = _normalize_coords(ds["prmsl"])
    elif "msl" in ds:
        out["prmslmsl"] = _normalize_coords(ds["msl"])

    # 10 m wind components (cfgrib: u10/v10 or u/v)
    if "u10" in ds and "v10" in ds:
        out["ugrd10m"] = _normalize_coords(ds["u10"])
        out["vgrd10m"] = _normalize_coords(ds["v10"])
    elif "u" in ds and "v" in ds:
        # Prefer heightAboveGround == 10 when present
        u = ds["u"]
        v = ds["v"]
        if "heightAboveGround" in u.coords:
            try:
                u = u.sel(heightAboveGround=10, method="nearest")
                v = v.sel(heightAboveGround=10, method="nearest")
            except Exception:
                pass
        out["ugrd10m"] = _normalize_coords(u)
        out["vgrd10m"] = _normalize_coords(v)

    if not out:
        raise ValueError(
            "No recognized GFS Atmosphere variables in GRIB dataset "
            f"(found: {list(ds.data_vars)})"
        )

    return _attach_time(xr.Dataset(out), ds)


def load_gfswave_forecast(cycle: str, forecast_hour: int, cache: bool = True) -> xr.Dataset:
    """Load one GFS Wave forecast hour as handler-compatible xarray Dataset."""
    url = gfswave_grib_url(cycle, forecast_hour)
    if cache:
        cache_path = CACHE_DIR / "gfswave" / cycle / f"f{forecast_hour:03d}.grib2"
        path = _download(url, cache_path)
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".grib2", delete=False)
        urllib.request.urlretrieve(url, tmp.name)
        path = Path(tmp.name)

    raw = _open_grib_file(path)
    return normalize_gfswave_dataset(raw)


def load_gfsatmos_forecast(cycle: str, forecast_hour: int, cache: bool = True) -> xr.Dataset:
    """Load one GFS Atmosphere forecast hour as handler-compatible xarray Dataset."""
    url = gfsatmos_grib_url(cycle, forecast_hour)
    if cache:
        cache_path = (
            CACHE_DIR / "gfsatmos" / cycle / f"f{forecast_hour:03d}_uvmslprh700.grib2"
        )
        path = _download(url, cache_path)
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".grib2", delete=False)
        urllib.request.urlretrieve(url, tmp.name)
        path = Path(tmp.name)

    raw = _open_grib_file(path)
    return normalize_gfsatmos_dataset(raw)


def load_gfswave_cycle(cycle: str, max_hours: int, hour_step: int = 1) -> xr.Dataset:
    """Load consecutive forecasts into a single dataset with time dimension."""
    from plotter.core.config_loader import get_forecast_hours

    datasets = []
    for t in get_forecast_hours(max_hours=max_hours, hour_step=hour_step):
        try:
            ds = load_gfswave_forecast(cycle, t)
            datasets.append(ds)
        except Exception as exc:
            print(f"[WARN] Stopping at t+{t:03d}h: {exc}")
            break

    if not datasets:
        raise RuntimeError(f"No GFS Wave data loaded for cycle {cycle}")

    return xr.concat(datasets, dim="time")


def load_gfsatmos_cycle(cycle: str, max_hours: int, hour_step: int = 1) -> xr.Dataset:
    """Load consecutive GFS Atmosphere forecasts into one dataset."""
    from plotter.core.config_loader import get_forecast_hours

    datasets = []
    for t in get_forecast_hours(max_hours=max_hours, hour_step=hour_step):
        try:
            ds = load_gfsatmos_forecast(cycle, t)
            datasets.append(ds)
        except Exception as exc:
            print(f"[WARN] Stopping atmos at t+{t:03d}h: {exc}")
            break

    if not datasets:
        raise RuntimeError(f"No GFS Atmosphere data loaded for cycle {cycle}")

    return xr.concat(datasets, dim="time")
