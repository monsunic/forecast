from ..core.utils import load_model_params, select_bbox, select_time
from ..core.base_handler import BaseHandler
from ..core.scalar_plot import plot_scalar_field
import cartopy.crs as ccrs


class SshHandler(BaseHandler):
    def load(self, ds):
        mapper = load_model_params(self.config.dataset)
        varnames = mapper["ssh"]
        dset = ds[varnames["var"]]
        dset = select_time(dset, self.config)
        dset = select_bbox(dset, self.config)
        return dset

    def plot(self, ax, ssh):
        im = plot_scalar_field(ax, ssh.lon, ssh.lat, ssh, self.config)
        ax.contour(
            ssh.lon,
            ssh.lat,
            ssh,
            levels=self.config.levels,
            colors="#0B2340",
            linewidths=0.4,
            alpha=0.55,
            transform=ccrs.PlateCarree(),
        )
        return im, None
