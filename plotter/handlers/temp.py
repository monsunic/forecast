from ..core.utils import load_model_params, select_bbox, select_time, select_level
from ..core.base_handler import BaseHandler
from ..core.scalar_plot import plot_scalar_field


class TempHandler(BaseHandler):
    def load(self, ds):
        mapper = load_model_params(self.config.dataset)
        varnames = mapper["temp"]
        dset = ds[varnames["var"]]
        dset = select_time(dset, self.config)
        dset = select_level(dset, self.config)
        dset = select_bbox(dset, self.config)
        return dset

    def plot(self, ax, tmp):
        im = plot_scalar_field(ax, tmp.lon, tmp.lat, tmp, self.config)
        return im, None
