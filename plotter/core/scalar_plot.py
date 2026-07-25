"""Shared scalar field rendering for map handlers."""

import cartopy.crs as ccrs

from .colormaps import build_cmap_norm


def plot_scalar_field(ax, lon, lat, data, config, *, method=None):
    """
    Render a shaded scalar layer using the Nusawave discrete palette.
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
