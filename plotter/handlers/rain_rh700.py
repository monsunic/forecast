"""Shaded rainfall rate with 700 hPa relative humidity contour overlay.

Mid-level RH shows the moist envelope the rain sits inside, and the dry
intrusions that suppress convection — structure that 2 m RH lacks over ocean.
"""

import cartopy.crs as ccrs
import matplotlib.patheffects as pe

from ..core.base_handler import BaseHandler
from ..core.scalar_plot import plot_scalar_field
from ..core.utils import load_model_params, select_bbox, select_time

DEFAULT_RH_LEVELS = [50, 70, 90]
# Below this the contour is dashed to read as dry air rather than moisture.
DRY_THRESHOLD = 60
# Raw 0.25° mid-level RH contours into unreadable spaghetti; a light rolling
# mean keeps the synoptic moisture boundaries and drops the grid-scale noise.
DEFAULT_SMOOTH_WINDOW = 5


class RainRh700Handler(BaseHandler):
    def load(self, ds):
        mapper = load_model_params(self.config.dataset)
        names = mapper["rain_rh700"]

        rain = ds[names["rain"]]
        rain = select_time(rain, self.config)
        rain = select_bbox(rain, self.config)
        rain = rain * 3600  # kg/m²/s → mm/hr

        rh = ds[names["rh"]]
        rh = select_time(rh, self.config)
        rh = select_bbox(rh, self.config)

        window = int(getattr(self.config, "smooth", None) or DEFAULT_SMOOTH_WINDOW)
        if window > 1:
            rh = rh.rolling(
                lat=window, lon=window, center=True, min_periods=1
            ).mean()

        return rain, rh

    def plot(self, ax, data):
        rain, rh = data
        im = plot_scalar_field(ax, rain.lon, rain.lat, rain, self.config)

        contour_cfg = getattr(self.config, "contour", {}) or {}
        levels = list(getattr(self.config, "rh_levels", None) or DEFAULT_RH_LEVELS)

        cs = ax.contour(
            rh.lon,
            rh.lat,
            rh,
            levels=levels,
            colors=contour_cfg.get("colors", "#EA580C"),
            linewidths=contour_cfg.get("linewidth", 0.8),
            linestyles=[
                "dashed" if lv < DRY_THRESHOLD else "solid" for lv in levels
            ],
            alpha=contour_cfg.get("alpha", 0.85),
            transform=ccrs.PlateCarree(),
            zorder=4,
        )

        # Warm lines cross both the white and deep-violet ends of the rain
        # palette, so labels need a halo to stay legible.
        for label in ax.clabel(cs, fontsize=6, inline=True, inline_spacing=2, fmt="%d"):
            label.set_path_effects(
                [pe.withStroke(linewidth=1.6, foreground="white")]
            )

        return im, None
