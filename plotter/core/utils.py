import sys
import importlib
import cartopy.crs as ccrs
from pathlib import Path
import numpy as np

def _ensure_project_root():
    """
    Ensure that the project root (directory containing 'plotter/')
    is available in sys.path.
    """
    current_file = Path(__file__).resolve()

    root = current_file.parents[2]

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

def get_projection(name):
    if name == "mercator":
        return ccrs.Mercator()
    elif name == "plate":
        return ccrs.PlateCarree()
    elif name == "northpolar":
        return ccrs.NorthPolarStereo()
    return ccrs.PlateCarree()

def load_model_params(dataset):
    _ensure_project_root()

    module_name = f"plotter.modelparams.{dataset}"

    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        raise ValueError(
            f"[ERROR] Dataset selector '{dataset}' not found.\n"
            f"Expected at: plotter/modelparams/{dataset}.py"
        )

    if not hasattr(module, "VARIABLE_MAP"):
        raise ValueError(
            f"[ERROR] Dataset module '{module_name}' does not define VARIABLE_MAP"
        )

    return module.VARIABLE_MAP

def select_time(ds, cfg):
    if cfg.time_index is not None:
        return ds.isel(time=cfg.time_index)
    if cfg.time_value is not None:
        return ds.sel(time=cfg.time_value, method="nearest")
    return ds

def select_bbox(data, cfg):
    if not cfg.bbox:
        return data
    minlon, maxlon, minlat, maxlat = cfg.bbox

    lon_slice = slice(minlon, maxlon)
    if "lat" in data.coords:
        lat_vals = data["lat"].values
        if len(lat_vals) > 1 and lat_vals[0] > lat_vals[-1]:
            lat_slice = slice(maxlat, minlat)
        else:
            lat_slice = slice(minlat, maxlat)
        return data.sel(lon=lon_slice, lat=lat_slice)
    if "latitude" in data.coords:
        lat_vals = data["latitude"].values
        if len(lat_vals) > 1 and lat_vals[0] > lat_vals[-1]:
            lat_slice = slice(maxlat, minlat)
        else:
            lat_slice = slice(minlat, maxlat)
        return data.sel(longitude=lon_slice, latitude=lat_slice)
    return data

def select_level(data, cfg):
    if cfg.level is None:
        return data

    # generic logic
    for dim in ["isobaricInhPa", "level", "pressure"]:
        if dim in data.dims:
            return data.sel({dim: cfg.level}, method="nearest")

    return data

def select_depth(data, cfg):
    if cfg.depth is None:
        return data

    for dim in ["depth", "z"]:
        if dim in data.dims:
            return data.sel({dim: cfg.depth}, method="nearest")

    return data

def get_dataset_url(dataset, cycle):
    """Return data access URL or path hint. OpenDAP is retired; gfswave uses GRIB2."""
    y = cycle[:4]
    m = cycle[4:6]
    d = cycle[6:8]
    h = cycle[8:10]

    if dataset == "gfswave":
        from .grib_loader import gfswave_grib_url
        return gfswave_grib_url(cycle, 0)

    if dataset == "gfsatmos":
        from .grib_loader import gfsatmos_grib_url
        return gfsatmos_grib_url(cycle, 0)
    
    if dataset == "ecmwfatmos":
        return f"https://example.ecmwf.int/era5_{y}{m}{d}_{h}.nc"   # placeholder
    
    if dataset == "ecmwfwave":
        return f"https://example.ecmwf.int/era5_wave_{y}{m}{d}_{h}.nc"
    
    if dataset == "hycom":
        return f"https://hycom.org/dods/datasets/global_analysis_forecast/{y}{m}{d}.nc"
    
    if dataset == "cmems":
        return f"https://my.cmems-duacs.org/dods/global-analysis-forecast-phy/{y}{m}{d}.nc"

    raise ValueError("Unknown dataset source")


def open_dataset(dataset, cycle, max_hours=None):
    """Open a forecast dataset using the appropriate backend."""
    if dataset == "gfswave":
        from .grib_loader import load_gfswave_cycle, load_gfswave_forecast
        if max_hours is not None and max_hours == 1:
            return load_gfswave_forecast(cycle, 0)
        hours = max_hours or 72
        return load_gfswave_cycle(cycle, hours)

    if dataset == "gfsatmos":
        from .grib_loader import load_gfsatmos_cycle, load_gfsatmos_forecast
        if max_hours is not None and max_hours == 1:
            return load_gfsatmos_forecast(cycle, 0)
        hours = max_hours or 72
        return load_gfsatmos_cycle(cycle, hours)

    import xarray as xr
    url = get_dataset_url(dataset, cycle)
    return xr.open_dataset(url, engine="netcdf4")

def deep_update(base: dict, updates: dict):
    """
    Recursively merge two dictionaries.
    Values in updates override those in base.
    """
    for k, v in updates.items():
        if (
            k in base 
            and isinstance(base[k], dict) 
            and isinstance(v, dict)
        ):
            deep_update(base[k], v)
        else:
            base[k] = v
    return base

def _grid_shape(lat, lon):
    lat = np.asarray(lat)
    lon = np.asarray(lon)
    n_lon = int(lon.size if lon.ndim == 1 else lon.shape[-1])
    n_lat = int(lat.size if lat.ndim == 1 else lat.shape[0])
    return n_lat, n_lon


def _figure_size(cfg):
    figsize = getattr(cfg, "figsize", None) or (8, 6)
    return float(figsize[0]), float(figsize[1])


def _is_landscape(cfg):
    """Wide domains (indonesia, java, malacca) whose width dominates the height."""
    fig_w, fig_h = _figure_size(cfg)
    threshold = float(cfg.quiver.get("landscape_aspect", 1.5))
    return (fig_w / max(fig_h, 1e-6)) >= threshold


def compute_vector_skip(lat, lon, cfg):
    """Skip for ~per_inch arrows along each figure edge (screen-consistent density).

    Portrait / near-square maps pack arrows denser; wide landscape maps keep a
    looser spacing so the longer edge doesn't turn into a wall of arrows.
    """
    method = get_vector_method(cfg)
    section = cfg.windbarb if method == "windbarb" else cfg.quiver
    if section.get("skip") is not None:
        return int(section["skip"])

    if _is_landscape(cfg):
        per_inch = float(section.get("per_inch_landscape", section.get("per_inch", 1.9)))
    else:
        per_inch = float(section.get("per_inch_portrait", section.get("per_inch", 2.6)))
    n_lat, n_lon = _grid_shape(lat, lon)
    fig_w, fig_h = _figure_size(cfg)
    target_x = max(8.0, fig_w * per_inch)
    target_y = max(8.0, fig_h * per_inch)
    return max(1, int(round(max(n_lon / target_x, n_lat / target_y))))


def get_vector_method(cfg):
    plot = getattr(cfg, "plot", None) or {}
    vector = plot.get("vector", {}) if isinstance(plot, dict) else {}
    return vector.get("method", "quiver")


def plot_vectors(ax, lon, lat, u, v, cfg, *, direction_only=False):
    """
    Plot vector field using quiver or windbarb based on product config.
    Returns the matplotlib artist (quiver or barbs) or None.
    """
    import cartopy.crs as ccrs

    method = get_vector_method(cfg)
    if method in (None, "none"):
        return None

    u_np = np.asarray(u)
    v_np = np.asarray(v)
    skip = compute_vector_skip(lat, lon, cfg)

    if method == "windbarb":
        # Barbs on wide landscape maps get the longer edge as reference so they
        # stay large; portrait maps track the shorter side for a tighter look.
        fig_w, fig_h = _figure_size(cfg)
        base = float(cfg.windbarb.get("length", 5.0))
        ref = max(fig_w, fig_h) if _is_landscape(cfg) else min(fig_w, fig_h)
        length = float(np.clip(base * (ref / 8.0), 4.5, 8.0))
        barb_kwargs = dict(
            transform=ccrs.PlateCarree(),
            length=length,
            barbcolor=cfg.windbarb.get("barbcolor", "black"),
            linewidth=cfg.windbarb.get("linewidth", 0.5),
            pivot=cfg.windbarb.get("pivot", "middle"),
            fill_empty=cfg.windbarb.get("fill_empty", False),
            zorder=4,
        )
        sizes = cfg.windbarb.get("sizes")
        if sizes is not None:
            barb_kwargs["sizes"] = sizes
        return ax.barbs(
            lon[::skip],
            lat[::skip],
            u_np[::skip, ::skip],
            v_np[::skip, ::skip],
            **barb_kwargs,
        )

    mag = np.sqrt(u_np ** 2 + v_np ** 2)
    safe_mag = np.where(mag > 0, mag, 1.0)

    quiver_kwargs = dict(
        transform=ccrs.PlateCarree(),
        width=cfg.quiver.get("width"),
        headwidth=cfg.quiver.get("headwidth"),
        headlength=cfg.quiver.get("headlength"),
        headaxislength=cfg.quiver.get("headaxislength"),
        minlength=cfg.quiver.get("minlength"),
        minshaft=cfg.quiver.get("minshaft"),
        pivot=cfg.quiver.get("pivot"),
        color=cfg.quiver.get("color", "black"),
        zorder=4,
    )

    if direction_only:
        # Screen-consistent size: arrow length = arrow_frac × shorter projected side.
        # Cartopy keeps eastward/northward magnitude, so scale_units='xy' + scale=1
        # makes the displacement equal that magnitude in projection metres.
        if cfg.quiver.get("scale") is not None:
            # Explicit override keeps legacy data-unit scaling.
            u_plot = u_np / safe_mag
            v_plot = v_np / safe_mag
            quiver_kwargs["scale"] = float(cfg.quiver["scale"])
            if cfg.quiver.get("scale_units"):
                quiver_kwargs["scale_units"] = cfg.quiver["scale_units"]
        else:
            x0, x1 = ax.get_xlim()
            y0, y1 = ax.get_ylim()
            shorter = max(min(abs(x1 - x0), abs(y1 - y0)), 1e-6)
            if _is_landscape(cfg):
                # Wide maps have a short vertical span, so bump the fraction to
                # keep arrows physically large.
                arrow_frac = float(cfg.quiver.get("arrow_frac_landscape", 0.050))
            else:
                arrow_frac = float(cfg.quiver.get("arrow_frac", 0.024))
            target = arrow_frac * shorter
            u_plot = (u_np / safe_mag) * target
            v_plot = (v_np / safe_mag) * target
            quiver_kwargs["scale"] = 1.0
            quiver_kwargs["scale_units"] = "xy"
            quiver_kwargs["angles"] = "xy"
    else:
        u_plot = u_np
        v_plot = v_np
        if cfg.quiver.get("scale") is not None:
            quiver_kwargs["scale"] = float(cfg.quiver["scale"])
        if cfg.quiver.get("scale_units"):
            quiver_kwargs["scale_units"] = cfg.quiver["scale_units"]

    return ax.quiver(
        lon[::skip],
        lat[::skip],
        u_plot[::skip, ::skip],
        v_plot[::skip, ::skip],
        **quiver_kwargs,
    )