import pytest

from plotter.core.colormaps import (
    NUSAWAVE_PALETTES,
    build_cmap_norm,
    colorbar_ticks,
    resolve_palette,
)
from plotter.core.config_loader import apply_product_config
from plotter.core.plot_config import PlotConfig


@pytest.mark.parametrize("palette_key", sorted(NUSAWAVE_PALETTES))
def test_palette_has_colors(palette_key):
    colors = NUSAWAVE_PALETTES[palette_key]
    assert len(colors) >= 8
    assert all(c.startswith("#") and len(c) in (4, 7) for c in colors)


def test_resolve_palette_from_config():
    cfg = PlotConfig(palette="nusawave_wind")
    colors = resolve_palette(cfg)
    assert colors == NUSAWAVE_PALETTES["nusawave_wind"]


def test_build_cmap_norm_bins():
    cfg = PlotConfig(
        palette="nusawave_temp",
        levels=[20, 22, 24, 26, 28, 30, 32, 34, 35],
        extend="both",
    )
    cmap, norm, levels = build_cmap_norm(cfg)
    assert cmap.N == 10  # 8 intervals + 2 extension bins
    assert len(levels) == 9


def test_build_cmap_norm_extend_max():
    cfg = PlotConfig(
        palette="nusawave_wind",
        levels=[0, 2, 4, 6, 8, 10, 15, 20, 25, 30, 35, 40, 50, 60],
        extend="max",
    )
    cmap, norm, _ = build_cmap_norm(cfg)
    assert cmap.N == 14  # 13 intervals + max extension


def test_colorbar_ticks_subsample():
    levels = list(range(0, 101, 5))
    ticks = colorbar_ticks(levels, max_ticks=10)
    assert len(ticks) <= 10
    assert ticks[0] == 0
    assert ticks[-1] == 100


@pytest.mark.parametrize(
    "slug,palette",
    [
        ("wind", "nusawave_wind"),
        ("swh", "nusawave_wave"),
        ("swell", "nusawave_swell"),
        ("rainrate", "nusawave_rain"),
        ("temp", "nusawave_temp"),
        ("relhum", "nusawave_rh"),
        ("seatemp", "nusawave_sst"),
        ("seasalt", "nusawave_salinity"),
        ("ssh", "nusawave_ssh"),
        ("seacurrent", "nusawave_current"),
    ],
)
def test_product_palette_wiring(slug, palette):
    cfg = PlotConfig()
    apply_product_config(cfg, slug)
    assert cfg.palette == palette
    assert isinstance(cfg.levels, list)
    cmap, norm, _ = build_cmap_norm(cfg)
    assert cmap.N >= 1
    assert norm is not None
