from ..core.base_handler import BaseHandler
from ..core.utils import load_model_params, select_bbox, select_time
from ..core.scalar_plot import plot_scalar_field


class RainrateHandler(BaseHandler):
    def load(self, ds):
        mapper = load_model_params(self.config.dataset)
        varnames = mapper["rainrate"]
        dset = ds[varnames["var"]]
        dset = select_time(dset, self.config)
        dset = select_bbox(dset, self.config)
        dset = dset * 3600
        return dset

    def plot(self, ax, rain):
        im = plot_scalar_field(ax, rain.lon, rain.lat, rain, self.config)
        return im, None
