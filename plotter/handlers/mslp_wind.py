"""Surface wind speed/direction with MSLP isobar overlay (analysis-style chart)."""

import cartopy.crs as ccrs

from ..core.base_handler import BaseHandler
from ..core.utils import (
    load_model_params,
    select_bbox,
    select_time,
    select_level,
    plot_vectors,
)

class MslpWindHandler(BaseHandler):
    def load(self, ds):
        mapper = load_model_params(self.config.dataset)
        wind_names = mapper["mslp_wind"]
        mslp_names = mapper["mslp"]

        u = ds[wind_names["u"]]
        v = ds[wind_names["v"]]
        mslp = ds[mslp_names["var"]]

        u = select_time(u, self.config)
        u = select_level(u, self.config)
        u = select_bbox(u, self.config)
        v = select_time(v, self.config)
        v = select_level(v, self.config)
        v = select_bbox(v, self.config)
        mslp = select_time(mslp, self.config)
        mslp = select_level(mslp, self.config)
        mslp = select_bbox(mslp, self.config)

        return u, v, mslp

    def plot(self, ax, data):
        u, v, mslp = data
        lon = u.lon.values
        lat = u.lat.values
        scale = float(getattr(self.config, "scale", 1.0))
        u_np = u.values * scale
        v_np = v.values * scale

        vector_method = self.config.plot.get("vector", {}).get("method", "windbarb")
        direction_only = vector_method != "windbarb"
        iq = plot_vectors(
            ax,
            lon,
            lat,
            u_np,
            v_np,
            self.config,
            direction_only=direction_only,
        )

        levels_cfg = getattr(self.config, "mslp_levels", None) or getattr(
            self.config, "contour_levels", None
        )
        if isinstance(levels_cfg, dict):
            levels = range(
                levels_cfg["start"],
                levels_cfg["end"] + 1,
                levels_cfg["step"],
            )
        else:
            levels = range(980, 1051, 2)

        contour_cfg = getattr(self.config, "contour", {}) or {}
        cs = ax.contour(
            mslp.lon,
            mslp.lat,
            mslp / 100.0,  # Pa → hPa
            levels=levels,
            colors=contour_cfg.get("colors", "#0B2340"),
            linewidths=contour_cfg.get("linewidth", 0.7),
            transform=ccrs.PlateCarree(),
            zorder=4,
        )
        ax.clabel(cs, fontsize=6, inline=True, inline_spacing=2)

        return None, iq
