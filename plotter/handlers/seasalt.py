from ..core.base_handler import BaseHandler
from ..core.utils import load_model_params, select_time, select_depth, select_bbox
from ..core.scalar_plot import plot_scalar_field


class SeasaltHandler(BaseHandler):
    def load(self, ds):
        mapper = load_model_params(self.config.dataset)
        varnames = mapper["seasalt"]
        dset = ds[varnames["var"]]
        dset = select_time(dset, self.config)
        dset = select_depth(dset, self.config)
        dset = select_bbox(dset, self.config)
        return dset

    def plot(self, ax, sss):
        im = plot_scalar_field(ax, sss.lon, sss.lat, sss, self.config)
        return im, None
