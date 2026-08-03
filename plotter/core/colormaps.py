"""
Monsun-branded discrete colormaps for marine forecast products.

Palettes are tuned for Southeast Asian maritime use and anchored to site
brand colors (#0B2340 navy, #0B74DE blue). They intentionally differ from
BMKG-style rainbow wind/wave ramps.
"""

from __future__ import annotations

import matplotlib.colors as mcolors
import numpy as np

# Brand anchors (see src/style.css)
MS_ICE = "#E8F4FC"
MS_SKY = "#93C5FD"
MS_BLUE = "#0B74DE"
MS_NAVY = "#0B2340"
MS_CYAN = "#06B6D4"
MS_TEAL = "#14B8A6"
MS_MINT = "#6EE7B7"
MS_GOLD = "#F59E0B"
MS_CORAL = "#F97316"
MS_RED = "#DC2626"
MS_VIOLET = "#7C3AED"
MS_PLUM = "#581C87"

MONSUN_PALETTES: dict[str, list[str]] = {
    # Surface wind (kt) — cool marine ramp into warm hazard tones
    "monsun_wind": [
        MS_ICE, "#C3E4FA", MS_SKY, "#60A5FA", MS_BLUE, MS_CYAN,
        MS_TEAL, MS_MINT, "#A3E635", MS_GOLD, MS_CORAL, MS_RED,
        MS_VIOLET, MS_PLUM,
    ],
    # Significant wave height (m) — light coastal blue → tropical surf (readable low bins)
    "monsun_wave": [
        "#DBEAFE", "#93C5FD", "#60A5FA", "#3B82F6", "#0EA5E9", MS_CYAN,
        MS_TEAL, "#34D399", "#A3E635", MS_GOLD, MS_CORAL, "#EA580C",
        MS_RED, "#DB2777",
    ],
    # Primary swell — same readable cool start, slightly cooler mid tones
    "monsun_swell": [
        "#E0F2FE", "#7DD3FC", "#38BDF8", "#2563EB", "#0EA5E9", MS_CYAN,
        MS_TEAL, "#5EEAD4", "#86EFAC", "#FDE047", MS_GOLD, MS_CORAL,
        MS_RED, "#C026D3",
    ],
    # Rainfall rate (mm/hr)
    "monsun_rain": [
        "#F8FAFC", "#E0F2FE", "#BAE6FD", "#7DD3FC", "#38BDF8", MS_BLUE,
        "#1D4ED8", "#3730A3", "#5B21B6", MS_VIOLET, MS_PLUM,
    ],
    # Air temperature (°C) — smooth indigo → blue → teal → green → gold → red (no grey)
    "monsun_temp": [
        "#312E81", "#3538A8", "#3B4FC4", "#2563EB", "#0EA5E9",
        "#06B6D4", "#0891B2", "#14B8A6", "#10B981", "#34D399",
        "#6EE7B7", "#A3E635", "#D9F99D", "#FDE047", "#FACC15",
        MS_GOLD, "#FB923C", MS_CORAL, "#EF4444", MS_RED, "#991B1B",
    ],
    # Relative humidity (%)
    "monsun_rh": [
        "#FFFBEB", "#FEF3C7", "#FDE68A", "#A7F3D0", MS_MINT,
        MS_TEAL, MS_CYAN, MS_BLUE, "#1E40AF", MS_NAVY,
    ],
    # Sea surface temperature (°C)
    "monsun_sst": [
        "#1E1B4B", "#312E81", "#3730A3", MS_BLUE, "#3B82F6", MS_CYAN,
        MS_TEAL, MS_MINT, "#86EFAC", "#BBF7D0", "#FEF08A", MS_GOLD,
        MS_CORAL, "#FB7185", MS_RED, "#BE123C", "#881337", "#4C0519",
        MS_NAVY,
    ],
    # Sea salinity (PSU) — light ice → sky → teal → soft blue → gold (no near-black)
    "monsun_salinity": [
        "#F0F9FF", "#E0F2FE", "#BAE6FD", "#7DD3FC", "#38BDF8",
        "#22D3EE", "#2DD4BF", "#34D399", "#A3E635", "#FDE047",
        "#FBBF24", "#FB923C", "#F87171",
    ],
    # Sea surface height anomaly (m) — diverging
    "monsun_ssh": [
        "#1E3A8A", "#2563EB", MS_BLUE, MS_CYAN, MS_TEAL, MS_MINT,
        "#F8FAFC", "#FECACA", "#FCA5A5", MS_CORAL, MS_RED,
        "#BE123C", "#9F1239", "#881337", "#701A35", MS_PLUM,
    ],
    # Sea current speed (cm/s)
    "monsun_current": [
        MS_ICE, MS_SKY, MS_CYAN, MS_TEAL, MS_MINT, "#4ADE80",
        MS_GOLD, MS_CORAL, MS_RED, MS_VIOLET, MS_PLUM, MS_NAVY,
    ],
}


def resolve_palette(config) -> list[str]:
    """Return hex color list from config.palette or config.cmap."""
    palette_key = getattr(config, "palette", None)
    if palette_key and palette_key in MONSUN_PALETTES:
        return list(MONSUN_PALETTES[palette_key])

    cmap = getattr(config, "cmap", None)
    if isinstance(cmap, list):
        return list(cmap)
    if isinstance(cmap, str) and cmap in MONSUN_PALETTES:
        return list(MONSUN_PALETTES[cmap])

    raise ValueError(
        f"Unknown palette for product; set config.palette to one of: "
        f"{', '.join(sorted(MONSUN_PALETTES))}"
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
        raise ValueError("Discrete Monsun colormaps require config.levels as a list")

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


def colorbar_ticks(levels, max_ticks: int = 20):
    """Subsample level ticks for readable colorbars.

    Prefers an even integer stride so labels stay regularly spaced
    (e.g. 18,20,22,… instead of irregular skips).
    """
    if not isinstance(levels, list) or len(levels) <= max_ticks:
        return levels

    n = len(levels)
    # Choose smallest stride that keeps tick count <= max_ticks.
    stride = 1
    while (n + stride - 1) // stride > max_ticks:
        stride += 1

    ticks = levels[::stride]
    if ticks[-1] != levels[-1]:
        ticks = list(ticks) + [levels[-1]]
    return ticks


def _resample_colors(colors: list[str], n: int) -> list[str]:
    """Stretch or compress a palette to exactly n colors."""
    if n <= 0:
        return colors
    if len(colors) == n:
        return colors
    idx = np.linspace(0, len(colors) - 1, n)
    return [colors[int(round(i))] for i in idx]
