from ..core.base_handler import BaseHandler
from ..core.utils import (
    load_model_params,
    select_time,
    select_depth,
    select_bbox,
    plot_vectors,
)
from ..core.scalar_plot import plot_scalar_field
import numpy as np


class SeacurrentHandler(BaseHandler):
    def load(self, ds):
        mapper = load_model_params(self.config.dataset)
        varnames = mapper["seacurrent"]
        u = ds[varnames["u"]]
        v = ds[varnames["v"]]
        u = select_time(u, self.config)
        v = select_time(v, self.config)
        u = select_depth(u, self.config)
        v = select_depth(v, self.config)
        u = select_bbox(u, self.config)
        v = select_bbox(v, self.config)
        return u, v

    def plot(self, ax, data):
        u, v = data
        scale = float(getattr(self.config, "scale", 1.0))
        u_np = u.values * scale
        v_np = v.values * scale
        mag = np.sqrt(u_np ** 2 + v_np ** 2)
        im = plot_scalar_field(ax, u.lon, u.lat, mag, self.config)
        iq = plot_vectors(
            ax,
            u.lon.values,
            u.lat.values,
            u_np,
            v_np,
            self.config,
            direction_only=True,
        )
        return im, iq
