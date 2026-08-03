import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

HANDLERS = [
    ("wind", "WindHandler"),
    ("swh", "SwhHandler"),
    ("swell", "SwellHandler"),
    ("seatemp", "SeatempHandler"),
    ("rainrate", "RainrateHandler"),
    ("seacurrent", "SeacurrentHandler"),
    ("temp", "TempHandler"),
    ("relhum", "RelhumHandler"),
    ("mslp", "MslpHandler"),
    ("mslp_wind", "MslpWindHandler"),
    ("rain_rh700", "RainRh700Handler"),
]


@pytest.mark.parametrize("param,class_name", HANDLERS)
def test_handler_class_exists(param, class_name):
    module = importlib.import_module(f"plotter.handlers.{param}")
    assert hasattr(module, class_name), (
        f"Expected {class_name} in plotter.handlers.{param}"
    )


def test_gfswave_variable_map():
    from plotter.modelparams.gfswave import VARIABLE_MAP

    assert "wind" in VARIABLE_MAP
    assert "swh" in VARIABLE_MAP
    assert "swell" in VARIABLE_MAP
    assert VARIABLE_MAP["source"]


def test_product_catalog_has_all_handlers():
    from plotter.core.config_loader import get_products

    products = get_products()
    handler_slugs = {param for param, _ in HANDLERS}
    handler_slugs.update({"ssh", "seasalt"})
    assert handler_slugs.issubset(set(products.keys()))


def test_apply_product_config_merges_plot_metadata():
    from plotter.core.config_loader import apply_product_config
    from plotter.core.plot_config import PlotConfig

    cfg = PlotConfig()
    apply_product_config(cfg, "wind")
    assert cfg.plot["scalar"]["method"] == "contourf"
    assert cfg.plot["vector"]["method"] == "windbarb"
    assert cfg.var2display == "Surface Wind Speed and Direction"
    assert cfg.status == "production"


def _vector_config(method):
    return SimpleNamespace(
        plot={"vector": {"method": method}},
        quiver={
            "skip": 2,
            "scale": 80,
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
        windbarb={
            "skip": 2,
            "length": 7,
            "barbcolor": "black",
            "linewidth": 0.5,
            "pivot": "middle",
            "skipfactor": 30,
        },
    )


def test_plot_vectors_quiver():
    pytest.importorskip("cartopy")
    from plotter.core.utils import plot_vectors

    ax = MagicMock()
    lon = np.linspace(100, 110, 5)
    lat = np.linspace(-5, 5, 5)
    u = np.ones((5, 5))
    v = np.zeros((5, 5))
    cfg = _vector_config("quiver")

    plot_vectors(ax, lon, lat, u, v, cfg, direction_only=True)

    ax.quiver.assert_called_once()
    ax.barbs.assert_not_called()


def test_plot_vectors_windbarb():
    pytest.importorskip("cartopy")
    from plotter.core.utils import plot_vectors

    ax = MagicMock()
    lon = np.linspace(100, 110, 5)
    lat = np.linspace(-5, 5, 5)
    u = np.ones((5, 5)) * 10
    v = np.zeros((5, 5))
    cfg = _vector_config("windbarb")

    plot_vectors(ax, lon, lat, u, v, cfg, direction_only=False)

    ax.barbs.assert_called_once()
    ax.quiver.assert_not_called()


def test_wind_handler_converts_ms_to_knots():
    from plotter.handlers.wind import WindHandler

    lon = np.array([100.0, 101.0])
    lat = np.array([-1.0, 0.0])
    # 10 m/s along u → ~19.44 kt with variables.wind.scale
    u = SimpleNamespace(lon=SimpleNamespace(values=lon), lat=SimpleNamespace(values=lat), values=np.full((2, 2), 10.0))
    v = SimpleNamespace(lon=u.lon, lat=u.lat, values=np.zeros((2, 2)))
    scale = 3600.0 / 1852.0

    cfg = SimpleNamespace(
        scale=scale,
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
        palette="monsun_wind",
        levels=[0, 10, 20, 30],
        extend="max",
    )
    handler = WindHandler(cfg)

    captured = {}

    def fake_scalar(ax, lon_x, lat_y, mag, config):
        captured["mag"] = np.asarray(mag)
        return MagicMock()

    def fake_vectors(ax, lon_x, lat_y, u_np, v_np, config, direction_only=True):
        captured["u"] = np.asarray(u_np)
        captured["v"] = np.asarray(v_np)
        return MagicMock()

    import plotter.handlers.wind as wind_mod

    monkey_scalar = pytest.MonkeyPatch()
    monkey_scalar.setattr(wind_mod, "plot_scalar_field", fake_scalar)
    monkey_scalar.setattr(wind_mod, "plot_vectors", fake_vectors)
    try:
        handler.plot(MagicMock(), (u, v))
    finally:
        monkey_scalar.undo()

    expected = 10.0 * scale
    assert np.allclose(captured["mag"], expected)
    assert np.allclose(captured["u"], expected)
    assert np.allclose(captured["v"], 0.0)


def test_default_max_hours_from_yaml():
    from plotter.core.config_loader import (
        get_default_max_hours,
        get_forecast_hours,
        get_hour_step,
        load_param_config,
    )

    assert get_default_max_hours() == load_param_config()["forecast"]["max_hours"]
    assert get_default_max_hours() == 72
    assert get_hour_step() == 3
    hours = get_forecast_hours()
    assert hours[0] == 0
    assert hours[-1] == 72
    assert hours == list(range(0, 73, 3))
    assert get_forecast_hours(max_hours=4, hour_step=1) == [0, 1, 2, 3]


def test_clear_and_verify_param_maps(tmp_path):
    from plotter.core.map_assets import clear_param_maps, verify_param_maps

    maps_root = tmp_path / "gfswave"
    region = "indonesia"
    region_dir = maps_root / region
    region_dir.mkdir(parents=True)
    stale = region_dir / "wind_008.webp"
    stale.write_bytes(b"old")
    keep = region_dir / "wind_003.webp"
    keep.write_bytes(b"keep")
    (region_dir / "swh_000.webp").write_bytes(b"x")

    clear_param_maps(maps_root, [region], ["wind", "swh"], max_hours=1, purge_beyond=False)
    assert not (region_dir / "swh_000.webp").exists()
    assert keep.exists()
    assert stale.exists()

    clear_param_maps(maps_root, [region], ["wind"], max_hours=4, purge_beyond=True)
    assert not keep.exists()
    assert not stale.exists()

    (region_dir / "wind_000.webp").write_bytes(b"ok")
    (region_dir / "swh_000.webp").write_bytes(b"ok")
    verify_param_maps(maps_root, [region], ["wind", "swh"], 1)

    with pytest.raises(SystemExit):
        verify_param_maps(maps_root, [region], ["wind", "swh"], 2)

    (region_dir / "wind_000.webp").write_bytes(b"ok")
    (region_dir / "wind_003.webp").write_bytes(b"ok")
    (region_dir / "wind_001.webp").write_bytes(b"stale")
    # Explicit schedule + purge_beyond clears scheduled frames and off-schedule leftovers.
    clear_param_maps(
        maps_root, [region], ["wind"], forecast_hours=[0, 3], purge_beyond=True
    )
    assert not list(region_dir.glob("wind_*.webp"))

    (region_dir / "wind_000.webp").write_bytes(b"ok")
    (region_dir / "wind_003.webp").write_bytes(b"ok")
    (region_dir / "wind_001.webp").write_bytes(b"stale")
    clear_param_maps(
        maps_root, [region], ["wind"], forecast_hours=[0, 3], purge_beyond=False
    )
    assert not (region_dir / "wind_000.webp").exists()
    assert not (region_dir / "wind_003.webp").exists()
    assert (region_dir / "wind_001.webp").exists()

    (region_dir / "wind_000.webp").write_bytes(b"ok")
    (region_dir / "wind_003.webp").write_bytes(b"ok")
    verify_param_maps(maps_root, [region], ["wind"], [0, 3])
