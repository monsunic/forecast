"""Nearest-grid-point extraction for Site Forecast time series."""

from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np
import xarray as xr

from .utils import load_model_params

# m/s → knots (same as variables.wind.scale / mslp_wind.scale)
WIND_KT_SCALE = 1.943844492440496
# m/s → cm/s (same as variables.seacurrent.scale)
CURRENT_CMS_SCALE = 100.0
# kg/m²/s → mm/hr
RAIN_MMHR_SCALE = 3600.0

# Series keys written into forecast.json
SERIES_SPEC = {
    "wind_speed": {"unit": "kt", "has_dir": True, "dataset": "gfswave", "group": "waves"},
    "swh": {"unit": "m", "has_dir": True, "dataset": "gfswave", "group": "waves"},
    "swell": {"unit": "m", "has_dir": True, "dataset": "gfswave", "group": "waves"},
    "sst": {"unit": "degC", "has_dir": False, "dataset": "hycom", "group": "ocean"},
    "current": {"unit": "cm/s", "has_dir": True, "dataset": "hycom", "group": "ocean"},
    "rain": {"unit": "mm/hr", "has_dir": False, "dataset": "gfsatmos", "group": "weather"},
    "temp": {"unit": "degC", "has_dir": False, "dataset": "gfsatmos", "group": "weather"},
    "rh": {"unit": "%", "has_dir": False, "dataset": "gfsatmos", "group": "weather"},
}


def _lon_lat_names(da: xr.DataArray):
    lon_name = "lon" if "lon" in da.coords else ("longitude" if "longitude" in da.coords else None)
    lat_name = "lat" if "lat" in da.coords else ("latitude" if "latitude" in da.coords else None)
    if lon_name is None or lat_name is None:
        raise ValueError(f"DataArray missing lon/lat coords: {list(da.coords)}")
    return lon_name, lat_name


def _squeeze_point(da: xr.DataArray) -> xr.DataArray:
    """Drop leftover singleton dims after nearest selection."""
    drop = [d for d in da.dims if d not in ("time",) and da.sizes.get(d, 1) == 1]
    if drop:
        da = da.squeeze(drop, drop=True)
    return da


def sample_point(da: xr.DataArray, lat: float, lon: float) -> xr.DataArray:
    """Select the nearest grid point to ``(lat, lon)``.

    Handles ascending/descending latitude and longitudes in either
    ``[-180, 180]`` or ``[0, 360]``.
    """
    lon_name, lat_name = _lon_lat_names(da)
    lon_vals = np.asarray(da[lon_name].values, dtype=float)
    target_lon = float(lon)

    # Prefer the longitude convention already used by the grid.
    lon_min, lon_max = float(np.nanmin(lon_vals)), float(np.nanmax(lon_vals))
    if lon_min >= 0 and target_lon < 0:
        target_lon = target_lon % 360.0
    elif lon_max <= 180 and target_lon > 180:
        target_lon = ((target_lon + 180) % 360) - 180

    point = da.sel({lon_name: target_lon, lat_name: float(lat)}, method="nearest")
    return _squeeze_point(point)


def sample_valid_point(
    da: xr.DataArray,
    lat: float,
    lon: float,
    max_radius_deg: float = 1.5,
) -> xr.DataArray:
    """Select the nearest non-missing grid cell around a coastal site.

    Port coordinates often lie on land-masked wave/ocean cells. Try the exact
    nearest cell first, then search outward within ``max_radius_deg``. The
    returned DataArray preserves its time dimension.
    """
    point = sample_point(da, lat, lon)
    if _scalar(point.values) is not None:
        return point

    lon_name, lat_name = _lon_lat_names(da)
    lon_vals = np.asarray(da[lon_name].values, dtype=float)
    lat_vals = np.asarray(da[lat_name].values, dtype=float)
    if lon_vals.ndim != 1 or lat_vals.ndim != 1:
        return point

    target_lon = float(lon)
    if float(np.nanmin(lon_vals)) >= 0 and target_lon < 0:
        target_lon %= 360.0
    elif float(np.nanmax(lon_vals)) <= 180 and target_lon > 180:
        target_lon = ((target_lon + 180) % 360) - 180

    field = da
    for dim in list(field.dims):
        if dim not in (lat_name, lon_name):
            field = field.isel({dim: 0})
    try:
        values = np.asarray(field.transpose(lat_name, lon_name).values, dtype=float)
    except (ValueError, TypeError):
        return point

    lon_grid, lat_grid = np.meshgrid(lon_vals, lat_vals)
    lon_scale = math.cos(math.radians(float(lat)))
    distance = np.hypot((lon_grid - target_lon) * lon_scale, lat_grid - float(lat))
    valid = np.isfinite(values) & (distance <= float(max_radius_deg))
    if not np.any(valid):
        return point

    ranked = np.where(valid, distance, np.inf)
    iy, ix = np.unravel_index(np.argmin(ranked), ranked.shape)
    wet = da.sel({lat_name: lat_vals[iy], lon_name: lon_vals[ix]})
    return _squeeze_point(wet)


def _scalar(val) -> Optional[float]:
    if val is None:
        return None
    try:
        arr = np.asarray(val, dtype=float)
        if arr.size == 0:
            return None
        num = float(arr.reshape(-1)[0])
    except (TypeError, ValueError):
        return None
    if math.isnan(num) or math.isinf(num):
        return None
    return num


def _uv_speed_dir(u, v, speed_scale: float = 1.0, meteorological: bool = True):
    """Return (speed, direction_deg).

    Meteorological wind: direction FROM which the wind blows.
    Oceanographic current: direction TO which the current flows.
    """
    uu = _scalar(u)
    vv = _scalar(v)
    if uu is None or vv is None:
        return None, None
    speed = math.hypot(uu, vv) * speed_scale
    if meteorological:
        # From-direction (meteo): atan2(-u, -v)
        deg = (math.degrees(math.atan2(-uu, -vv)) + 360.0) % 360.0
    else:
        # To-direction (ocean): atan2(u, v)
        deg = (math.degrees(math.atan2(uu, vv)) + 360.0) % 360.0
    return speed, deg


def _sampled_coords(da: xr.DataArray) -> tuple[Optional[float], Optional[float]]:
    lon_name, lat_name = _lon_lat_names(da)
    return _scalar(da[lat_name].values), _scalar(da[lon_name].values)


def extract_gfswave_hour(ds: xr.Dataset, lat: float, lon: float) -> dict[str, Any]:
    """Extract wind / SWH / swell at one site for one loaded wave hour."""
    mapper = load_model_params("gfswave")
    out: dict[str, Any] = {}
    grid: dict[str, Any] = {}

    wind = mapper["wind"]
    u = sample_valid_point(ds[wind["u"]], lat, lon)
    u_lat, u_lon = _sampled_coords(u)
    v = sample_point(
        ds[wind["v"]],
        u_lat if u_lat is not None else lat,
        u_lon if u_lon is not None else lon,
    )
    speed, direction = _uv_speed_dir(u.values, v.values, WIND_KT_SCALE, meteorological=True)
    out["wind_speed"] = speed
    out["wind_dir"] = direction
    glat, glon = _sampled_coords(u)
    grid["gfswave"] = {"lat": glat, "lon": glon}

    swh_map = mapper["swh"]
    swh = sample_valid_point(ds[swh_map["mag"]], lat, lon)
    swh_lat, swh_lon = _sampled_coords(swh)
    swh_dir = sample_point(
        ds[swh_map["dir"]],
        swh_lat if swh_lat is not None else lat,
        swh_lon if swh_lon is not None else lon,
    )
    out["swh"] = _scalar(swh.values)
    out["swh_dir"] = _scalar(swh_dir.values)

    swell_map = mapper["swell"]
    swell = sample_valid_point(ds[swell_map["mag"]], lat, lon)
    swell_lat, swell_lon = _sampled_coords(swell)
    swell_dir = sample_point(
        ds[swell_map["dir"]],
        swell_lat if swell_lat is not None else lat,
        swell_lon if swell_lon is not None else lon,
    )
    out["swell"] = _scalar(swell.values)
    out["swell_dir"] = _scalar(swell_dir.values)

    out["_grid"] = grid
    return out


def extract_gfsatmos_hour(ds: xr.Dataset, lat: float, lon: float) -> dict[str, Any]:
    """Extract rain / temp / RH at one site for one loaded atmos hour."""
    mapper = load_model_params("gfsatmos")
    out: dict[str, Any] = {}
    grid: dict[str, Any] = {}

    rain = sample_point(ds[mapper["rainrate"]["var"]], lat, lon)
    out["rain"] = None if _scalar(rain.values) is None else _scalar(rain.values) * RAIN_MMHR_SCALE

    temp = sample_point(ds[mapper["temp"]["var"]], lat, lon)
    out["temp"] = _scalar(temp.values)

    rh = sample_point(ds[mapper["relhum"]["var"]], lat, lon)
    out["rh"] = _scalar(rh.values)

    glat, glon = _sampled_coords(temp)
    grid["gfsatmos"] = {"lat": glat, "lon": glon}
    out["_grid"] = grid
    return out


def extract_hycom_hour(ds: xr.Dataset, lat: float, lon: float) -> dict[str, Any]:
    """Extract SST / current at one site for one loaded HYCOM hour."""
    mapper = load_model_params("hycom")
    out: dict[str, Any] = {}
    grid: dict[str, Any] = {}

    sst = sample_valid_point(ds[mapper["seatemp"]["var"]], lat, lon)
    out["sst"] = _scalar(sst.values)

    cur = mapper["seacurrent"]
    u = sample_valid_point(ds[cur["u"]], lat, lon)
    u_lat, u_lon = _sampled_coords(u)
    v = sample_point(
        ds[cur["v"]],
        u_lat if u_lat is not None else lat,
        u_lon if u_lon is not None else lon,
    )
    speed, direction = _uv_speed_dir(
        u.values, v.values, CURRENT_CMS_SCALE, meteorological=False
    )
    out["current"] = speed
    out["current_dir"] = direction

    glat, glon = _sampled_coords(sst)
    grid["hycom"] = {"lat": glat, "lon": glon}
    out["_grid"] = grid
    return out


EXTRACTORS = {
    "gfswave": extract_gfswave_hour,
    "gfsatmos": extract_gfsatmos_hour,
    "hycom": extract_hycom_hour,
}


def empty_series_shell() -> dict[str, dict]:
    """Return empty series containers matching the forecast.json schema."""
    series = {}
    for key, meta in SERIES_SPEC.items():
        entry: dict[str, Any] = {"unit": meta["unit"], "values": []}
        if meta["has_dir"]:
            entry["dir_deg"] = []
        series[key] = entry
    return series


def append_hour_to_series(series: dict, hour_vals: dict[str, Any]) -> None:
    """Append one hour of extracted values onto accumulating series lists.

    Always appends a slot for every known series key so all series stay
    aligned with the shared ``hours`` axis (missing datasets → ``None``).
    """
    mapping = {
        "wind_speed": ("wind_speed", "wind_dir"),
        "swh": ("swh", "swh_dir"),
        "swell": ("swell", "swell_dir"),
        "sst": ("sst", None),
        "current": ("current", "current_dir"),
        "rain": ("rain", None),
        "temp": ("temp", None),
        "rh": ("rh", None),
    }
    for series_key, (val_key, dir_key) in mapping.items():
        if series_key not in series:
            continue
        series[series_key]["values"].append(hour_vals.get(val_key))
        if dir_key and "dir_deg" in series[series_key]:
            series[series_key]["dir_deg"].append(hour_vals.get(dir_key))


def build_site_forecast_doc(
    site: dict,
    cycles: dict[str, str],
    hours: list[str],
    valid_times: list[str],
    series: dict,
    grid_points: dict[str, dict],
    generated_at: str,
) -> dict:
    """Assemble the forecast.json document for one site."""
    return {
        "site": {
            "id": site["id"],
            "name": site["name"],
            "lat": site["lat"],
            "lon": site["lon"],
        },
        "cycles": {k: v for k, v in cycles.items() if v},
        "generated_at": generated_at,
        "hours": hours,
        "valid_times": valid_times,
        "grid_points": grid_points,
        "series": series,
    }
