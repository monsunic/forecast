"""
Nusawave-branded discrete colormaps for marine forecast products.

Palettes are tuned for Southeast Asian maritime use and anchored to site
brand colors (#0B2340 navy, #0B74DE blue). They intentionally differ from
BMKG-style rainbow wind/wave ramps.
"""

from __future__ import annotations

import matplotlib.colors as mcolors
import numpy as np

# Brand anchors (see src/style.css)
NW_ICE = "#E8F4FC"
NW_SKY = "#93C5FD"
NW_BLUE = "#0B74DE"
NW_NAVY = "#0B2340"
NW_CYAN = "#06B6D4"
NW_TEAL = "#14B8A6"
NW_MINT = "#6EE7B7"
NW_GOLD = "#F59E0B"
NW_CORAL = "#F97316"
NW_RED = "#DC2626"
NW_VIOLET = "#7C3AED"
NW_PLUM = "#581C87"

NUSAWAVE_PALETTES: dict[str, list[str]] = {
    # Surface wind (kt) — cool marine ramp into warm hazard tones
    "nusawave_wind": [
        NW_ICE, "#C3E4FA", NW_SKY, "#60A5FA", NW_BLUE, NW_CYAN,
        NW_TEAL, NW_MINT, "#A3E635", NW_GOLD, NW_CORAL, NW_RED,
        NW_VIOLET, NW_PLUM,
    ],
    # Significant wave height (m) — light coastal blue → tropical surf (readable low bins)
    "nusawave_wave": [
        "#DBEAFE", "#93C5FD", "#60A5FA", "#3B82F6", "#0EA5E9", NW_CYAN,
        NW_TEAL, "#34D399", "#A3E635", NW_GOLD, NW_CORAL, "#EA580C",
        NW_RED, "#DB2777",
    ],
    # Primary swell — same readable cool start, slightly cooler mid tones
    "nusawave_swell": [
        "#E0F2FE", "#7DD3FC", "#38BDF8", "#2563EB", "#0EA5E9", NW_CYAN,
        NW_TEAL, "#5EEAD4", "#86EFAC", "#FDE047", NW_GOLD, NW_CORAL,
        NW_RED, "#C026D3",
    ],
    # Rainfall rate (mm/hr)
    "nusawave_rain": [
        "#F8FAFC", "#E0F2FE", "#BAE6FD", "#7DD3FC", "#38BDF8", NW_BLUE,
        "#1D4ED8", "#3730A3", "#5B21B6", NW_VIOLET, NW_PLUM,
    ],
    # Air temperature (°C) — cool indigo to warm coral
    "nusawave_temp": [
        "#312E81", "#4338CA", NW_BLUE, "#94A3B8", "#CBD5E1",
        "#FDE68A", NW_GOLD, NW_CORAL, NW_RED,
    ],
    # Relative humidity (%)
    "nusawave_rh": [
        "#FFFBEB", "#FEF3C7", "#FDE68A", "#A7F3D0", NW_MINT,
        NW_TEAL, NW_CYAN, NW_BLUE, "#1E40AF", NW_NAVY,
    ],
    # Sea surface temperature (°C)
    "nusawave_sst": [
        "#1E1B4B", "#312E81", "#3730A3", NW_BLUE, "#3B82F6", NW_CYAN,
        NW_TEAL, NW_MINT, "#86EFAC", "#BBF7D0", "#FEF08A", NW_GOLD,
        NW_CORAL, "#FB7185", NW_RED, "#BE123C", "#881337", "#4C0519",
        NW_NAVY,
    ],
    # Sea salinity (PSU)
    "nusawave_salinity": [
        NW_ICE, "#BAE6FD", NW_SKY, NW_CYAN, NW_TEAL, NW_BLUE,
        "#1D4ED8", "#1E3A8A", NW_NAVY, "#0F172A", "#020617", "#000000",
    ],
    # Sea surface height anomaly (m) — diverging
    "nusawave_ssh": [
        "#1E3A8A", "#2563EB", NW_BLUE, NW_CYAN, NW_TEAL, NW_MINT,
        "#F8FAFC", "#FECACA", "#FCA5A5", NW_CORAL, NW_RED,
        "#BE123C", "#9F1239", "#881337", "#701A35", NW_PLUM,
    ],
    # Sea current speed (cm/s)
    "nusawave_current": [
        NW_ICE, NW_SKY, NW_CYAN, NW_TEAL, NW_MINT, "#4ADE80",
        NW_GOLD, NW_CORAL, NW_RED, NW_VIOLET, NW_PLUM, NW_NAVY,
    ],
}


def resolve_palette(config) -> list[str]:
    """Return hex color list from config.palette or config.cmap."""
    palette_key = getattr(config, "palette", None)
    if palette_key and palette_key in NUSAWAVE_PALETTES:
        return list(NUSAWAVE_PALETTES[palette_key])

    cmap = getattr(config, "cmap", None)
    if isinstance(cmap, list):
        return list(cmap)
    if isinstance(cmap, str) and cmap in NUSAWAVE_PALETTES:
        return list(NUSAWAVE_PALETTES[cmap])

    raise ValueError(
        f"Unknown palette for product; set config.palette to one of: "
        f"{', '.join(sorted(NUSAWAVE_PALETTES))}"
    )


def _ncolors_for_extend(n_intervals: int, extend) -> int:
    """Color count required by BoundaryNorm including extension bins."""
    extend = extend or "neither"
    if extend == "both":
        return n_intervals + 2
    if extend in ("max", "min"):
        return n_intervals + 1
    return n_intervals


def build_cmap_norm(config):
    """
    Build ListedColormap and BoundaryNorm from config levels + palette.
    """
    colors = resolve_palette(config)
    levels = getattr(config, "levels", None)
    extend = getattr(config, "extend", None) or "neither"

    if not isinstance(levels, list) or len(levels) < 2:
        raise ValueError("Discrete Nusawave colormaps require config.levels as a list")

    n_intervals = len(levels) - 1
    n_colors = _ncolors_for_extend(n_intervals, extend)
    colors = _resample_colors(colors, n_colors)

    cmap = mcolors.ListedColormap(colors)
    norm = mcolors.BoundaryNorm(
        boundaries=levels,
        ncolors=len(colors),
        extend=extend,
    )
    return cmap, norm, levels


def colorbar_ticks(levels, max_ticks: int = 14):
    """Subsample level ticks for readable colorbars."""
    if not isinstance(levels, list) or len(levels) <= max_ticks:
        return levels
    idx = np.linspace(0, len(levels) - 1, max_ticks, dtype=int)
    return [levels[i] for i in idx]


def _resample_colors(colors: list[str], n: int) -> list[str]:
    """Stretch or compress a palette to exactly n colors."""
    if n <= 0:
        return colors
    if len(colors) == n:
        return colors
    idx = np.linspace(0, len(colors) - 1, n)
    return [colors[int(round(i))] for i in idx]
