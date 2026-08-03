"""Shared scalar field rendering for map handlers."""

import matplotlib.patheffects as pe
import cartopy.crs as ccrs
import numpy as np

from .colormaps import build_cmap_norm


def plot_scalar_field(ax, lon, lat, data, config, *, method=None):
    """
    Render a shaded scalar layer using the Monsun discrete palette.
    Supports contourf and pcolormesh per product plot metadata.
    """
    plot_cfg = getattr(config, "plot", {}) or {}
    scalar_cfg = plot_cfg.get("scalar", {}) if isinstance(plot_cfg, dict) else {}
    method = method or scalar_cfg.get("method", "contourf")

    cmap, norm, levels = build_cmap_norm(config)
    extend = getattr(config, "extend", None)
    transform = ccrs.PlateCarree()

    if method == "pcolormesh":
        return ax.pcolormesh(
            lon,
            lat,
            data,
            cmap=cmap,
            norm=norm,
            shading="auto",
            transform=transform,
        )

    if method == "contourf":
        return ax.contourf(
            lon,
            lat,
            data,
            cmap=cmap,
            norm=norm,
            levels=levels,
            extend=extend,
            transform=transform,
        )

    raise ValueError(f"Unsupported scalar plot method: {method}")


def overlay_contour_lines(ax, lon, lat, data, config):
    """Draw thin labeled contours on top of a filled scalar field.

    Uses ``config.levels`` for line positions (or ``contour.levels`` when set).
    ``contour.label_stride`` controls how often labels appear (default 2 when
    there are many levels, else 1).
    """
    contour_cfg = getattr(config, "contour", {}) or {}
    if contour_cfg.get("enabled") is False:
        return None

    levels = contour_cfg.get("levels") or getattr(config, "levels", None)
    if isinstance(levels, dict):
        levels = list(
            range(levels["start"], levels["end"] + 1, levels["step"])
        )
    elif levels is not None:
        levels = list(levels)
    else:
        return None

    if len(levels) < 2:
        return None

    values = np.asarray(data)
    vmin = float(np.nanmin(values))
    vmax = float(np.nanmax(values))
    levels = [lv for lv in levels if vmin - 1e-6 <= float(lv) <= vmax + 1e-6]
    if len(levels) < 2:
        return None

    cs = ax.contour(
        lon,
        lat,
        data,
        levels=levels,
        colors=contour_cfg.get("colors", "#0B2340"),
        linewidths=contour_cfg.get("linewidth", 0.4),
        alpha=contour_cfg.get("alpha", 0.55),
        transform=ccrs.PlateCarree(),
        zorder=4,
    )

    if contour_cfg.get("labels", True):
        stride = contour_cfg.get("label_stride")
        if stride is None:
            stride = 2 if len(levels) >= 10 else 1
        stride = max(1, int(stride))
        label_levels = levels[::stride]
        fmt = contour_cfg.get("label_fmt", "%g")
        texts = ax.clabel(
            cs,
            levels=label_levels,
            fontsize=contour_cfg.get("label_size", 6),
            inline=True,
            inline_spacing=2,
            fmt=fmt,
        )
        if contour_cfg.get("label_halo", True):
            for label in texts or []:
                label.set_path_effects(
                    [pe.withStroke(linewidth=1.5, foreground="white")]
                )

    return cs
