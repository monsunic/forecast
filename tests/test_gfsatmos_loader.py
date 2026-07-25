"""Unit tests for GFS Atmosphere GRIB URL building and normalization."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
import xarray as xr


def test_gfsatmos_grib_url_shape():
    from plotter.core.grib_loader import gfsatmos_grib_url

    url = gfsatmos_grib_url("2026072500", 3)
    assert "filter_gfs_0p25.pl" in url
    assert "gfs.t00z.pgrb2.0p25.f003" in url
    assert "var_PRATE=on" in url
    assert "var_TMP=on" in url
    assert "var_RH=on" in url
    assert "var_PRMSL=on" in url
    assert "var_UGRD=on" in url
    assert "var_VGRD=on" in url
    assert "lev_10_m_above_ground=on" in url
    assert "lev_2_m_above_ground=on" in url
    assert "lev_700_mb=on" in url
    assert "dir=%2Fgfs.20260725%2F00%2Fatmos" in url or "dir=/gfs.20260725/00/atmos" in url
    assert "leftlon=90" in url
    assert "rightlon=150" in url
    assert "toplat=25" in url
    assert "bottomlat=-20" in url


def test_normalize_gfsatmos_units_and_names():
    from plotter.core.grib_loader import normalize_gfsatmos_dataset

    lon = np.array([100.0, 101.0])
    lat = np.array([0.0, 1.0])
    valid = np.datetime64("2026-07-25T03:00:00")

    raw = xr.Dataset(
        {
            "prate": (("latitude", "longitude"), np.full((2, 2), 1e-4)),
            "t2m": (("latitude", "longitude"), np.full((2, 2), 300.15)),
            "r2": (("latitude", "longitude"), np.full((2, 2), 80.0)),
            "prmsl": (("latitude", "longitude"), np.full((2, 2), 101325.0)),
            "u10": (("latitude", "longitude"), np.full((2, 2), 5.0)),
            "v10": (("latitude", "longitude"), np.full((2, 2), 0.0)),
        },
        coords={
            "longitude": lon,
            "latitude": lat,
            "valid_time": valid,
        },
    )

    ds = normalize_gfsatmos_dataset(raw)
    assert set(ds.data_vars) == {
        "apcpsfc", "tmpsfc", "rh2msfc", "prmslmsl", "ugrd10m", "vgrd10m",
    }
    assert "lon" in ds["tmpsfc"].coords
    assert "lat" in ds["tmpsfc"].coords
    assert "time" in ds.dims
    # Kelvin → °C
    assert np.allclose(ds["tmpsfc"].values, 27.0)
    # PRATE left as kg/m²/s for RainrateHandler (*3600)
    assert np.allclose(ds["apcpsfc"].values, 1e-4)
    assert np.allclose(ds["rh2msfc"].values, 80.0)
    assert np.allclose(ds["prmslmsl"].values, 101325.0)
    assert np.allclose(ds["ugrd10m"].values, 5.0)
    assert np.allclose(ds["vgrd10m"].values, 0.0)


def test_normalize_gfsatmos_rejects_empty():
    from plotter.core.grib_loader import normalize_gfsatmos_dataset

    with pytest.raises(ValueError, match="No recognized GFS Atmosphere"):
        normalize_gfsatmos_dataset(xr.Dataset({"foo": (("x",), [1.0])}))


def test_pick_latest_gfs_cycle_format():
    from plotter.core.grib_loader import pick_latest_gfs_cycle

    cycle = pick_latest_gfs_cycle()
    assert len(cycle) == 10
    dt = datetime.strptime(cycle, "%Y%m%d%H")
    assert dt.hour in (0, 6, 12, 18)
    assert dt <= datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)


def test_gfsatmos_variable_map_has_source():
    from plotter.modelparams.gfsatmos import VARIABLE_MAP

    assert VARIABLE_MAP["rainrate"]["var"] == "apcpsfc"
    assert VARIABLE_MAP["temp"]["var"] == "tmpsfc"
    assert VARIABLE_MAP["relhum"]["var"] == "rh2msfc"
    assert VARIABLE_MAP["mslp"]["var"] == "prmslmsl"
    assert VARIABLE_MAP["mslp_wind"]["u"] == "ugrd10m"
    assert VARIABLE_MAP["mslp_wind"]["v"] == "vgrd10m"
    assert VARIABLE_MAP["rain_rh700"]["rain"] == "apcpsfc"
    assert VARIABLE_MAP["rain_rh700"]["rh"] == "rh700mb"
    assert VARIABLE_MAP["source"]


def test_mslp_wind_handler_class_name():
    from plotter.core.plotter import Plotter
    from plotter.core.plot_config import PlotConfig

    plotter = Plotter(PlotConfig(dataset="gfsatmos"))
    handler = plotter._load_handler("mslp_wind")
    assert handler.__class__.__name__ == "MslpWindHandler"


def test_rain_rh700_handler_class_name():
    from plotter.core.plotter import Plotter
    from plotter.core.plot_config import PlotConfig

    plotter = Plotter(PlotConfig(dataset="gfsatmos"))
    handler = plotter._load_handler("rain_rh700")
    assert handler.__class__.__name__ == "RainRh700Handler"


def test_normalize_gfsatmos_extracts_rh700_not_2m():
    """700 hPa RH must come from the isobaric field, never from 2 m RH."""
    from plotter.core.grib_loader import normalize_gfsatmos_dataset

    lon = np.array([100.0, 101.0])
    lat = np.array([0.0, 1.0])

    # cfgrib merges level types, so every var carries both scalar coords.
    raw = xr.Dataset(
        {
            "prate": (("latitude", "longitude"), np.full((2, 2), 1e-4)),
            "r2": (("latitude", "longitude"), np.full((2, 2), 82.0)),
            "r": (("latitude", "longitude"), np.full((2, 2), 35.0)),
        },
        coords={
            "longitude": lon,
            "latitude": lat,
            "valid_time": np.datetime64("2026-07-25T00:00:00"),
            "isobaricInhPa": 700.0,
        },
    )

    ds = normalize_gfsatmos_dataset(raw)
    assert np.allclose(ds["rh700mb"].values, 35.0)
    assert np.allclose(ds["rh2msfc"].values, 82.0)
    # Must be a plain lat/lon field — no level dimension left to plot over.
    assert "isobaricInhPa" not in ds["rh700mb"].dims


def test_normalize_gfsatmos_without_rh700_is_optional():
    """Older cached GRIBs lacking 700 hPa RH must still normalize."""
    from plotter.core.grib_loader import normalize_gfsatmos_dataset

    raw = xr.Dataset(
        {"prate": (("latitude", "longitude"), np.full((2, 2), 1e-4))},
        coords={
            "longitude": np.array([100.0, 101.0]),
            "latitude": np.array([0.0, 1.0]),
            "valid_time": np.datetime64("2026-07-25T00:00:00"),
        },
    )

    ds = normalize_gfsatmos_dataset(raw)
    assert "rh700mb" not in ds.data_vars
    assert "apcpsfc" in ds.data_vars
