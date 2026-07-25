from ..core.utils import load_model_params, select_bbox, select_time, plot_vectors
from ..core.base_handler import BaseHandler
from ..core.scalar_plot import plot_scalar_field
import numpy as np


class SwhHandler(BaseHandler):
    def load(self, ds):
        mapper = load_model_params(self.config.dataset)
        varnames = mapper["swh"]
        mag = ds[varnames["mag"]]
        dir = ds[varnames["dir"]]
        mag = select_time(mag, self.config)
        dir = select_time(dir, self.config)
        mag = select_bbox(mag, self.config)
        dir = select_bbox(dir, self.config)
        return mag, dir

    def plot(self, ax, data):
        mag, direction = data

        lon = mag.lon.values
        lat = mag.lat.values

        dir_rad = np.deg2rad(direction.values)
        u = -np.sin(dir_rad)
        v = -np.cos(dir_rad)

        im = plot_scalar_field(ax, mag.lon, mag.lat, mag, self.config)
        iq = plot_vectors(
            ax, lon, lat, u, v, self.config, direction_only=True
        )
        return im, iq
