from ..core.base_handler import BaseHandler
from ..core.utils import load_model_params, select_time, select_level, select_bbox
from ..core.scalar_plot import plot_scalar_field, overlay_contour_lines


class RelhumHandler(BaseHandler):
    def load(self, ds):
        mapper = load_model_params(self.config.dataset)
        varnames = mapper["relhum"]
        dset = ds[varnames["var"]]
        dset = select_time(dset, self.config)
        dset = select_level(dset, self.config)
        dset = select_bbox(dset, self.config)
        return dset

    def plot(self, ax, rh):
        im = plot_scalar_field(ax, rh.lon, rh.lat, rh, self.config)
        overlay_contour_lines(ax, rh.lon, rh.lat, rh, self.config)
        return im, None
