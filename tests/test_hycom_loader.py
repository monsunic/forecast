"""Unit tests for HYCOM ESPC surface NCSS loading and normalization."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
import xarray as xr


def test_hycom_ncss_url_shape():
    from plotter.core.hycom_loader import hycom_ncss_url

    url = hycom_ncss_url("2026072400", 3)
    assert "FMRC_ESPC-D-V02_ice" in url
    assert "var=sst" in url
    assert "var=sss" in url
    assert "var=ssu" in url
    assert "var=ssv" in url
    assert "west=90" in url
    assert "east=150" in url
    assert "south=-20" in url
    assert "north=25" in url
    assert "2026-07-24T03" in url or "2026-07-24T03%3A00%3A00Z" in url
    assert "accept=netcdf4" in url


def test_normalize_hycom_surface_names():
    from plotter.core.hycom_loader import normalize_hycom_dataset

    lon = np.array([100.0, 101.0])
    lat = np.array([0.0, 1.0])
    raw = xr.Dataset(
        {
            "sst": (("time", "lat", "lon"), np.full((1, 2, 2), 28.0)),
            "sss": (("time", "lat", "lon"), np.full((1, 2, 2), 34.0)),
            "ssu": (("time", "lat", "lon"), np.full((1, 2, 2), 0.5)),
            "ssv": (("time", "lat", "lon"), np.full((1, 2, 2), -0.2)),
        },
        coords={
            "time": [np.datetime64("2026-07-24T00:00:00")],
            "lat": lat,
            "lon": lon,
        },
    )

    ds = normalize_hycom_dataset(raw)
    assert set(ds.data_vars) == {"water_temp", "salinity", "water_u", "water_v"}
    assert np.allclose(ds["water_temp"].values, 28.0)
    assert np.allclose(ds["salinity"].values, 34.0)
    assert np.allclose(ds["water_u"].values, 0.5)
    assert np.allclose(ds["water_v"].values, -0.2)
    assert "time" in ds["water_temp"].dims


def test_normalize_hycom_rejects_empty():
    from plotter.core.hycom_loader import normalize_hycom_dataset

    with pytest.raises(ValueError, match="No recognized HYCOM"):
        normalize_hycom_dataset(xr.Dataset({"foo": (("x",), [1.0])}))


def test_pick_latest_hycom_cycle_format():
    from plotter.core.hycom_loader import pick_latest_hycom_cycle

    cycle = pick_latest_hycom_cycle()
    assert len(cycle) == 10
    dt = datetime.strptime(cycle, "%Y%m%d%H")
    assert dt <= datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)


def test_hycom_variable_map_has_source():
    from plotter.modelparams.hycom import VARIABLE_MAP

    assert VARIABLE_MAP["seatemp"]["var"] == "water_temp"
    assert VARIABLE_MAP["seasalt"]["var"] == "salinity"
    assert VARIABLE_MAP["seacurrent"]["u"] == "water_u"
    assert VARIABLE_MAP["seacurrent"]["v"] == "water_v"
    assert VARIABLE_MAP["source"]


def test_download_retries_then_succeeds(tmp_path, monkeypatch):
    from plotter.core import hycom_loader as mod

    dest = tmp_path / "f000_sfc.nc"
    calls = {"n": 0}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size=-1):
            return b"x" * 2000

    def fake_urlopen(url, timeout=None):
        calls["n"] += 1
        assert timeout == mod.DOWNLOAD_TIMEOUT_SEC
        if calls["n"] < 3:
            raise TimeoutError("simulated stall")
        return FakeResp()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(mod.shutil, "copyfileobj", lambda src, dst, length=None: dst.write(src.read()))

    path = mod._download("https://example.test/hycom", dest, retries=3)
    assert path == dest
    assert dest.is_file()
    assert dest.stat().st_size >= mod.MIN_DOWNLOAD_BYTES
    assert calls["n"] == 3
    assert not list(tmp_path.glob("*.partial"))


def test_download_fails_after_retries(tmp_path, monkeypatch):
    from plotter.core import hycom_loader as mod

    dest = tmp_path / "f000_sfc.nc"

    def always_timeout(url, timeout=None):
        raise TimeoutError("still stalled")

    monkeypatch.setattr(mod.urllib.request, "urlopen", always_timeout)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    with pytest.raises(RuntimeError, match="failed after 2"):
        mod._download("https://example.test/hycom", dest, retries=2)
    assert not dest.exists()
    assert not list(tmp_path.glob("*.partial"))


def test_download_rejects_tiny_payload(tmp_path, monkeypatch):
    from plotter.core import hycom_loader as mod

    dest = tmp_path / "f000_sfc.nc"

    class TinyResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size=-1):
            return b"err"

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: TinyResp())
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(mod.shutil, "copyfileobj", lambda src, dst, length=None: dst.write(src.read()))

    with pytest.raises(RuntimeError, match="failed after 1"):
        mod._download("https://example.test/hycom", dest, retries=1)
    assert not dest.exists()


def test_download_reuses_valid_cache(tmp_path, monkeypatch):
    from plotter.core import hycom_loader as mod

    dest = tmp_path / "f000_sfc.nc"
    dest.write_bytes(b"y" * 2000)

    def boom(*a, **k):
        raise AssertionError("should not hit the network")

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    assert mod._download("https://example.test/hycom", dest) == dest


def test_seacurrent_handler_scales_to_cms():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from plotter.handlers.seacurrent import SeacurrentHandler

    lon = np.array([100.0, 101.0])
    lat = np.array([-1.0, 0.0])
    u = SimpleNamespace(
        lon=SimpleNamespace(values=lon),
        lat=SimpleNamespace(values=lat),
        values=np.full((2, 2), 1.0),  # 1 m/s → 100 cm/s
    )
    v = SimpleNamespace(lon=u.lon, lat=u.lat, values=np.zeros((2, 2)))
    cfg = SimpleNamespace(
        scale=100.0,
        plot={"vector": {"method": "quiver"}, "scalar": {"method": "contourf"}},
        quiver={
            "skip": 1,
            "scale": 50,
            "skipfactor": 30,
            "scalefactor": 30,
            "width": 0.002,
            "headwidth": 5,
            "headlength": 5,
            "headaxislength": 3,
            "minlength": 0.1,
            "minshaft": 0.1,
            "pivot": "middle",
            "color": "black",
        },
        windbarb={},
        palette="nusawave_current",
        levels=[0, 50, 100, 200],
        extend="max",
    )
    handler = SeacurrentHandler(cfg)
    captured = {}

    def fake_scalar(ax, lon_x, lat_y, mag, config):
        captured["mag"] = np.asarray(mag)
        return MagicMock()

    def fake_vectors(ax, lon_x, lat_y, u_np, v_np, config, direction_only=True):
        captured["u"] = np.asarray(u_np)
        captured["v"] = np.asarray(v_np)
        return MagicMock()

    import plotter.handlers.seacurrent as mod

    monkey = pytest.MonkeyPatch()
    monkey.setattr(mod, "plot_scalar_field", fake_scalar)
    monkey.setattr(mod, "plot_vectors", fake_vectors)
    try:
        handler.plot(MagicMock(), (u, v))
    finally:
        monkey.undo()

    assert np.allclose(captured["mag"], 100.0)
    assert np.allclose(captured["u"], 100.0)
    assert np.allclose(captured["v"], 0.0)
