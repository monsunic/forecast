from ..core.base_handler import BaseHandler
from ..core.utils import load_model_params, select_bbox, select_time, select_depth
from ..core.scalar_plot import plot_scalar_field, overlay_contour_lines


class SeatempHandler(BaseHandler):
    def load(self, ds):
        mapper = load_model_params(self.config.dataset)
        varnames = mapper["seatemp"]
        dset = ds[varnames["var"]]
        dset = select_time(dset, self.config)
        dset = select_depth(dset, self.config)
        dset = select_bbox(dset, self.config)
        return dset

    def plot(self, ax, sst):
        im = plot_scalar_field(ax, sst.lon, sst.lat, sst, self.config)
        overlay_contour_lines(ax, sst.lon, sst.lat, sst, self.config)
        return im, None
