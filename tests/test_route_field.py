"""Unit tests for the gridded Route Forecast field extractor."""

from __future__ import annotations

import math

import numpy as np
import pytest

xr = pytest.importorskip("xarray")

from plotter.core.route_field import (
    build_target_grid,
    extract_field_hour,
    flatten_round,
)


def test_build_target_grid_dimensions():
    grid = build_target_grid((98.0, 123.0, -9.0, 23.0), 0.5)
    assert grid["nlon"] == 51
    assert grid["nlat"] == 65
    assert grid["dlat"] == 0.5 and grid["dlon"] == 0.5
    assert grid["lat_min"] == -9.0 and grid["lon_min"] == 98.0


def _gfswave_dataset():
    """5×5 wave field with a land (NaN) block in the NE corner."""
    lat = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
    lon = np.array([100.0, 100.25, 100.5, 100.75, 101.0])
    swh = np.full((lat.size, lon.size), 2.0)
    # Land: everything at lat >= 0.75 and lon >= 100.75.
    swh[3:, 3:] = np.nan
    u = np.full((lat.size, lon.size), 5.0)  # eastward wind
    v = np.zeros((lat.size, lon.size))
    return xr.Dataset(
        {
            "htsgwsfc": (("lat", "lon"), swh),
            "ugrdsfc": (("lat", "lon"), u),
            "vgrdsfc": (("lat", "lon"), v),
        },
        coords={"lat": lat, "lon": lon},
    )


def test_extract_gfswave_grid_masks_land_and_computes_wind():
    grid = build_target_grid((100.0, 101.0, 0.0, 1.0), 0.5)  # 3×3
    fields = extract_field_hour(_gfswave_dataset(), "gfswave", grid)

    swh = fields["swh"]
    assert swh.size == grid["nlat"] * grid["nlon"] == 9

    # NE-most cell (lat 1.0, lon 101.0) is fed only by land natives → NaN.
    ne_index = (grid["nlat"] - 1) * grid["nlon"] + (grid["nlon"] - 1)
    assert not math.isfinite(swh[ne_index])
    # SW cell is open water.
    assert math.isfinite(swh[0]) and abs(swh[0] - 2.0) < 1e-6

    # Eastward 5 m/s wind → ~9.7 kt, meteorological FROM ≈ 270°.
    speed = fields["wind_speed"]
    direction = fields["wind_dir"]
    assert abs(speed[0] - 5.0 * 1.943844492440496) < 1e-6
    assert abs(direction[0] - 270.0) < 1e-6


def test_extract_hycom_grid_current_toward_direction():
    lat = np.array([0.0, 0.5, 1.0])
    lon = np.array([100.0, 100.5, 101.0])
    u = np.zeros((3, 3))
    v = np.full((3, 3), 1.0)  # northward current
    ds = xr.Dataset(
        {
            "water_u": (("lat", "lon"), u),
            "water_v": (("lat", "lon"), v),
        },
        coords={"lat": lat, "lon": lon},
    )
    grid = build_target_grid((100.0, 101.0, 0.0, 1.0), 0.5)
    fields = extract_field_hour(ds, "hycom", grid)

    # 1 m/s → ~1.94 kt, oceanographic TOWARD north ≈ 0°.
    assert abs(fields["current"][0] - 1.943844492440496) < 1e-6
    assert abs(fields["current_dir"][0] % 360.0) < 1e-6


def test_flatten_round_nulls_non_finite():
    arr = np.array([[1.234, np.nan], [np.inf, 2.0]])
    out = flatten_round(arr, 2)
    assert out == [1.23, None, None, 2.0]
    # Zero digits → integer directions.
    assert flatten_round(np.array([269.6, np.nan]), 0) == [270, None]
