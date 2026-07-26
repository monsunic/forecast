"""Gridded metocean field extraction for the dynamic Route Forecast.

Instead of sampling a hand-drawn lane graph, the router works over a regular
lat/lon grid of the actual model fields. Cells where the wave model has no data
are land (or otherwise un-navigable), so the browser A* naturally routes around
coastlines without any manually digitised corridors.

The native model grids (GFS Wave 0.25deg, HYCOM ~0.08deg) are down-sampled onto
a coarser routing grid by area-binning: every native cell is dropped into the
target cell that contains it and averaged. A target cell counts as sea when at
least one wet native cell falls inside it, which keeps narrow straits passable.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import xarray as xr

from .site_extract import WIND_KT_SCALE
from .utils import load_model_params

# m/s → kt (HYCOM currents are published in m/s natively)
CURRENT_KT_FROM_MS = 1.943844492440496

# var -> (dataset, rounding). swh drives both the speed model and the land mask.
FIELD_VARS = {
    "swh": ("gfswave", 2),
    "wind_speed": ("gfswave", 1),
    "wind_dir": ("gfswave", 0),
    "current": ("hycom", 2),
    "current_dir": ("hycom", 0),
}

FIELD_UNITS = {
    "swh": "m",
    "wind_speed": "kt",
    "wind_dir": "deg",
    "current": "kt",
    "current_dir": "deg",
}

# Datasets that contribute at least one field variable.
FIELD_DATASETS = ("gfswave", "hycom")


def build_target_grid(bbox: tuple[float, float, float, float], resolution: float) -> dict:
    """Return the routing grid spec for ``bbox = (lon_min, lon_max, lat_min, lat_max)``.

    Cell ``c = iy * nlon + ix`` has centre
    ``(lat_min + iy*dlat, lon_min + ix*dlon)``.
    """
    lon_min, lon_max, lat_min, lat_max = (float(v) for v in bbox)
    step = float(resolution)
    nlon = int(round((lon_max - lon_min) / step)) + 1
    nlat = int(round((lat_max - lat_min) / step)) + 1
    return {
        "lat_min": round(lat_min, 4),
        "lon_min": round(lon_min, 4),
        "dlat": step,
        "dlon": step,
        "nlat": nlat,
        "nlon": nlon,
    }


def _latlon_names(da: xr.DataArray) -> tuple[str, str]:
    lat = "lat" if "lat" in da.coords else ("latitude" if "latitude" in da.coords else None)
    lon = "lon" if "lon" in da.coords else ("longitude" if "longitude" in da.coords else None)
    if lat is None or lon is None:
        raise ValueError(f"DataArray missing lat/lon coords: {list(da.coords)}")
    return lat, lon


def _as_2d(ds: xr.Dataset, name: str) -> xr.DataArray:
    """Collapse a variable to a 2-D lat/lon field (surface, first time step)."""
    da = ds[name]
    lat_name, lon_name = _latlon_names(da)
    for dim in list(da.dims):
        if dim not in (lat_name, lon_name):
            da = da.isel({dim: 0})
    return da


def _bin_to_grid(da: xr.DataArray, grid: dict) -> tuple[np.ndarray, np.ndarray]:
    """Area-bin a native field onto the routing grid.

    Returns ``(sum, count)`` flattened arrays of length ``nlat*nlon`` so callers
    can build means (``sum/count``) and a wet mask (``count > 0``).
    """
    lat_name, lon_name = _latlon_names(da)
    lat = np.asarray(da[lat_name].values, dtype=float)
    lon = np.asarray(da[lon_name].values, dtype=float)
    values = np.asarray(da.transpose(lat_name, lon_name).values, dtype=float)

    lon_min = grid["lon_min"]
    lon_max = lon_min + grid["dlon"] * (grid["nlon"] - 1)
    lon_adj = lon.copy()
    if float(np.nanmax(lon)) > 180 and lon_max <= 180:
        lon_adj = ((lon_adj + 180.0) % 360.0) - 180.0
    elif float(np.nanmin(lon)) < 0 and lon_min >= 0:
        lon_adj = lon_adj % 360.0

    lon_grid, lat_grid = np.meshgrid(lon_adj, lat)
    ix = np.floor((lon_grid - grid["lon_min"]) / grid["dlon"] + 0.5).astype(int)
    iy = np.floor((lat_grid - grid["lat_min"]) / grid["dlat"] + 0.5).astype(int)

    ncell = grid["nlat"] * grid["nlon"]
    inside = (
        (ix >= 0)
        & (ix < grid["nlon"])
        & (iy >= 0)
        & (iy < grid["nlat"])
        & np.isfinite(values)
    )
    flat = (iy * grid["nlon"] + ix)[inside]
    vals = values[inside]

    total = np.zeros(ncell, dtype=float)
    count = np.zeros(ncell, dtype=float)
    np.add.at(total, flat, vals)
    np.add.at(count, flat, 1.0)
    return total, count


def _mean(total: np.ndarray, count: np.ndarray) -> np.ndarray:
    out = np.full_like(total, np.nan)
    wet = count > 0
    out[wet] = total[wet] / count[wet]
    return out


def extract_field_hour(ds: xr.Dataset, dataset: str, grid: dict) -> dict[str, np.ndarray]:
    """Return the flattened routing-grid fields for one loaded model hour.

    Directions are averaged as vectors (via binned u/v) so bearings survive the
    down-sample without 0/360 wrap artefacts.
    """
    mapper = load_model_params(dataset)
    out: dict[str, np.ndarray] = {}

    if dataset == "gfswave":
        swh_total, swh_count = _bin_to_grid(_as_2d(ds, mapper["swh"]["mag"]), grid)
        out["swh"] = _mean(swh_total, swh_count)

        u = _mean(*_bin_to_grid(_as_2d(ds, mapper["wind"]["u"]), grid))
        v = _mean(*_bin_to_grid(_as_2d(ds, mapper["wind"]["v"]), grid))
        speed = np.hypot(u, v) * WIND_KT_SCALE
        # Meteorological FROM-direction.
        direction = (np.degrees(np.arctan2(-u, -v)) + 360.0) % 360.0
        out["wind_speed"] = speed
        out["wind_dir"] = np.where(np.isfinite(speed), direction, np.nan)

    elif dataset == "hycom":
        cur = mapper["seacurrent"]
        u = _mean(*_bin_to_grid(_as_2d(ds, cur["u"]), grid))
        v = _mean(*_bin_to_grid(_as_2d(ds, cur["v"]), grid))
        speed = np.hypot(u, v) * CURRENT_KT_FROM_MS
        # Oceanographic TOWARD-direction.
        direction = (np.degrees(np.arctan2(u, v)) + 360.0) % 360.0
        out["current"] = speed
        out["current_dir"] = np.where(np.isfinite(speed), direction, np.nan)

    return out


def flatten_round(arr: np.ndarray, ndigits: int) -> list[Optional[float]]:
    """Flatten to a JSON-friendly list, ``None`` for non-finite (land) cells."""
    flat = np.asarray(arr, dtype=float).ravel()
    if ndigits <= 0:
        return [None if not math.isfinite(x) else int(round(x)) for x in flat]
    return [None if not math.isfinite(x) else round(float(x), ndigits) for x in flat]


def build_field_doc(
    grid: dict,
    cycles: dict[str, str],
    hours: list[str],
    valid_times: list[Optional[str]],
    sea_mask: list[int],
    vars_by_key: dict[str, list[list]],
    ports: list[dict],
    generated_at: str,
) -> dict:
    """Assemble the ``assets/routes/field.json`` document."""
    return {
        "generated_at": generated_at,
        "kind": "grid_field",
        "cycles": {k: v for k, v in cycles.items() if v},
        "grid": grid,
        "hours": hours,
        "valid_times": valid_times,
        "units": dict(FIELD_UNITS),
        "sea_mask": sea_mask,
        "vars": vars_by_key,
        "ports": [
            {"id": p["id"], "name": p["name"], "lat": p["lat"], "lon": p["lon"]}
            for p in ports
        ],
    }
