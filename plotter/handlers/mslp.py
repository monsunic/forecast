from ..core.base_handler import BaseHandler
from ..core.utils import load_model_params, select_time, select_level, select_bbox
import cartopy.crs as ccrs

class MslpHandler(BaseHandler):
    def load(self, ds):
        mapper = load_model_params(self.config.dataset)
        varnames = mapper["mslp"]
        dset = ds[varnames["var"]]
        dset = select_time(dset, self.config)
        dset = select_level(dset, self.config)
        dset = select_bbox(dset, self.config)
        return dset

    def plot(self, ax, mslp):
        cs = ax.contour(
            mslp.lon, mslp.lat, mslp / 100,  # hPa
            levels=range(
                self.config.levels["start"],
                self.config.levels["end"] + 1,
                self.config.levels["step"],
            ),
            colors=self.config.contour.get("colors", "#0B2340"),
            linewidths=self.config.contour["linewidth"],
            transform=ccrs.PlateCarree(),
        )
        ax.clabel(cs, fontsize=6)
        return None, None  # no colorbar
