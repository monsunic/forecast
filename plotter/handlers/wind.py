from ..core.base_handler import BaseHandler
from ..core.utils import (
    load_model_params,
    select_bbox,
    select_time,
    select_level,
    plot_vectors,
)
from ..core.scalar_plot import plot_scalar_field


class WindHandler(BaseHandler):
    def load(self, ds):
        mapper = load_model_params(self.config.dataset)
        varnames = mapper["wind"]

        u = ds[varnames["u"]]
        v = ds[varnames["v"]]

        u = select_time(u, self.config)
        u = select_level(u, self.config)
        u = select_bbox(u, self.config)
        v = select_time(v, self.config)
        v = select_level(v, self.config)
        v = select_bbox(v, self.config)

        return u, v

    def plot(self, ax, data):
        u, v = data
        lon = u.lon.values
        lat = u.lat.values
        # GRIB u/v are m/s; variables.wind.scale converts to product unit (knots).
        scale = float(getattr(self.config, "scale", 1.0))
        u_np = u.values * scale
        v_np = v.values * scale
        mag = (u_np ** 2 + v_np ** 2) ** 0.5

        im = plot_scalar_field(ax, u.lon, u.lat, mag, self.config)

        vector_method = self.config.plot.get("vector", {}).get("method", "quiver")
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
        return im, iq
