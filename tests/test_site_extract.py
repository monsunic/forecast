"""Tests for site forecast nearest-point extraction."""

import numpy as np
import xarray as xr


def test_sample_point_nearest():
    from plotter.core.site_extract import sample_point

    lon = np.array([103.5, 103.75, 104.0])
    lat = np.array([1.0, 1.25, 1.5])
    data = np.arange(9, dtype=float).reshape(3, 3)
    da = xr.DataArray(data, coords={"lat": lat, "lon": lon}, dims=("lat", "lon"))

    pt = sample_point(da, lat=1.28, lon=103.76)
    assert float(pt.values) == float(da.sel(lat=1.25, lon=103.75).values)
    assert float(pt.lat) == 1.25
    assert float(pt.lon) == 103.75


def test_sample_point_descending_lat():
    from plotter.core.site_extract import sample_point

    lon = np.array([100.0, 101.0])
    lat = np.array([2.0, 1.0])  # descending
    data = np.array([[10.0, 20.0], [30.0, 40.0]])
    da = xr.DataArray(data, coords={"lat": lat, "lon": lon}, dims=("lat", "lon"))
    pt = sample_point(da, lat=1.1, lon=100.2)
    assert float(pt.lat) == 1.0
    assert float(pt.lon) == 100.0
    assert float(pt.values) == 30.0


def test_sample_valid_point_avoids_land_mask():
    from plotter.core.site_extract import sample_valid_point

    lon = np.array([103.5, 103.75, 104.0])
    lat = np.array([1.0, 1.25, 1.5])
    data = np.full((3, 3), np.nan)
    data[1, 2] = 1.4  # nearest wet cell east of the masked port cell
    da = xr.DataArray(data, coords={"lat": lat, "lon": lon}, dims=("lat", "lon"))

    pt = sample_valid_point(da, lat=1.26, lon=103.76)
    assert float(pt.values) == 1.4
    assert float(pt.lat) == 1.25
    assert float(pt.lon) == 104.0


def test_uv_speed_dir_wind_and_current():
    from plotter.core.site_extract import _uv_speed_dir, WIND_KT_SCALE, CURRENT_CMS_SCALE

    # Pure +v (northward) wind in m/s → meteo "from" is south (180°)
    speed, direction = _uv_speed_dir(0.0, 1.0, WIND_KT_SCALE, meteorological=True)
    assert abs(speed - WIND_KT_SCALE) < 1e-6
    assert abs(direction - 180.0) < 1e-6

    # Pure +u (eastward) current → ocean "to" is east (90°)
    speed, direction = _uv_speed_dir(1.0, 0.0, CURRENT_CMS_SCALE, meteorological=False)
    assert abs(speed - CURRENT_CMS_SCALE) < 1e-6
    assert abs(direction - 90.0) < 1e-6


def test_append_hour_to_series_pads_missing_keys():
    from plotter.core.site_extract import append_hour_to_series, empty_series_shell

    series = empty_series_shell()
    # Wave-only hour: ocean/weather keys must still get None slots.
    append_hour_to_series(series, {"wind_speed": 12.5, "wind_dir": 90.0, "swh": 1.2, "swh_dir": 180.0, "swell": 0.8, "swell_dir": 200.0})
    append_hour_to_series(series, {"sst": 29.1, "current": 35.0, "current_dir": 45.0})
    assert series["wind_speed"]["values"] == [12.5, None]
    assert series["sst"]["values"] == [None, 29.1]
    assert len(series["rain"]["values"]) == 2
    assert series["rain"]["values"] == [None, None]


def test_merge_retained_series_keeps_ocean_when_hycom_missing():
    from plotter.core.site_extract import merge_retained_series

    doc = {
        "cycles": {"gfswave": "2026072600", "gfsatmos": "2026072600"},
        "hours": ["F000", "F003", "F006", "F009"],
        "valid_times": ["t0", "t3", "t6", "t9"],
        "series": {
            "wind_speed": {"unit": "kt", "values": [10, 11, 12, 13], "dir_deg": [1, 2, 3, 4]},
        },
        "grid_points": {"gfswave": {"lat": 1.0, "lon": 103.0}},
    }
    previous = {
        "cycles": {"gfswave": "2026072518", "hycom": "2026072421"},
        "hours": ["F000", "F006"],
        "series": {
            "sst": {"unit": "degC", "values": [29.1, 29.4]},
            "current": {"unit": "cm/s", "values": [40.0, 42.0], "dir_deg": [80.0, 90.0]},
        },
        "grid_points": {"hycom": {"lat": 1.2, "lon": 103.8}},
    }

    retained = merge_retained_series(
        doc, previous, refreshed_datasets=["gfswave", "gfsatmos"]
    )
    assert retained == ["hycom"]
    assert doc["cycles"]["hycom"] == "2026072421"
    assert doc["grid_points"]["hycom"] == {"lat": 1.2, "lon": 103.8}
    assert doc["hours"] == ["F000", "F003", "F006", "F009"]
    assert doc["series"]["sst"]["values"] == [29.1, None, 29.4, None]
    assert doc["series"]["current"]["dir_deg"] == [80.0, None, 90.0, None]


def test_merge_retained_series_keeps_gfs_when_only_ocean_refreshed():
    """A HYCOM-only re-extract must not drop wave/weather series."""
    from plotter.core.site_extract import merge_retained_series

    doc = {
        "cycles": {"hycom": "2026072600"},
        "hours": ["F000", "F006"],
        "valid_times": ["t0", "t6"],
        "series": {"sst": {"unit": "degC", "values": [29.8, 29.9]}},
        "grid_points": {"hycom": {"lat": 1.2, "lon": 103.8}},
    }
    previous = {
        "cycles": {"gfswave": "2026072518", "gfsatmos": "2026072518"},
        "hours": ["F000", "F003", "F006"],
        "valid_times": ["p0", "p3", "p6"],
        "series": {
            "wind_speed": {"unit": "kt", "values": [10, 11, 12], "dir_deg": [1, 2, 3]},
            "temp": {"unit": "degC", "values": [27.0, 27.5, 28.0]},
        },
        "grid_points": {"gfswave": {"lat": 1.0, "lon": 103.0}},
    }

    retained = merge_retained_series(doc, previous, refreshed_datasets=["hycom"])
    assert set(retained) == {"gfswave", "gfsatmos"}
    # Union axis keeps the finer GFS stride alongside 6-hourly ocean values.
    assert doc["hours"] == ["F000", "F003", "F006"]
    assert doc["valid_times"] == ["t0", "p3", "t6"]
    assert doc["series"]["sst"]["values"] == [29.8, None, 29.9]
    assert doc["series"]["wind_speed"]["values"] == [10, 11, 12]
    assert doc["series"]["temp"]["values"] == [27.0, 27.5, 28.0]
    assert doc["cycles"]["gfswave"] == "2026072518"


def test_merge_retained_series_skips_refreshed_datasets():
    from plotter.core.site_extract import merge_retained_series

    doc = {
        "cycles": {"hycom": "2026072600"},
        "hours": ["F000"],
        "valid_times": ["t0"],
        "series": {"sst": {"unit": "degC", "values": [30.0]}},
    }
    previous = {
        "cycles": {"hycom": "2026072421"},
        "hours": ["F000"],
        "series": {"sst": {"unit": "degC", "values": [29.0]}},
    }
    assert merge_retained_series(doc, previous, refreshed_datasets=["hycom"]) == []
    assert doc["series"]["sst"]["values"] == [30.0]


def test_build_site_forecast_doc_schema():
    from plotter.core.site_extract import build_site_forecast_doc, empty_series_shell

    series = empty_series_shell()
    series["wind_speed"]["values"] = [10.0, 11.0]
    series["wind_speed"]["dir_deg"] = [90.0, 100.0]
    doc = build_site_forecast_doc(
        site={"id": "singapore", "name": "Port of Singapore", "lat": 1.2788, "lon": 103.7566},
        cycles={"gfswave": "2026072506"},
        hours=["F000", "F003"],
        valid_times=["2026-07-25T06:00:00Z", "2026-07-25T09:00:00Z"],
        series=series,
        grid_points={"gfswave": {"lat": 1.25, "lon": 103.75}},
        generated_at="2026-07-26T02:00:00Z",
    )
    assert doc["site"]["id"] == "singapore"
    assert doc["hours"] == ["F000", "F003"]
    assert doc["series"]["wind_speed"]["unit"] == "kt"
    assert len(doc["series"]["wind_speed"]["values"]) == 2


def test_extract_gfswave_hour_from_synthetic_ds():
    from plotter.core.site_extract import extract_gfswave_hour

    lon = np.array([103.5, 103.75, 104.0])
    lat = np.array([1.0, 1.25, 1.5])
    shape = (1, 3, 3)
    ones = np.ones(shape, dtype=float)

    ds = xr.Dataset(
        {
            "ugrdsfc": (("time", "lat", "lon"), ones * 0.0),
            "vgrdsfc": (("time", "lat", "lon"), ones * 5.0),  # 5 m/s northward
            "htsgwsfc": (("time", "lat", "lon"), ones * 1.5),
            "dirpwsfc": (("time", "lat", "lon"), ones * 210.0),
            "swell_1": (("time", "lat", "lon"), ones * 0.9),
            "swdir_1": (("time", "lat", "lon"), ones * 200.0),
        },
        coords={
            "time": [np.datetime64("2026-07-25T06:00")],
            "lat": lat,
            "lon": lon,
        },
    )
    out = extract_gfswave_hour(ds, lat=1.2788, lon=103.7566)
    assert out["swh"] == 1.5
    assert out["swell"] == 0.9
    assert out["wind_speed"] is not None
    assert abs(out["wind_dir"] - 180.0) < 1e-6
    assert "gfswave" in out["_grid"]
