class PlotConfig:
    """
    Flexible config container.
    Explicit args for stability.
    **kwargs for future extensions or YAML overrides.
    """

    def __init__(
        self,
        # Styling and figure behavior
        proj="mercator",
        figsize=(8, 6),
        dpi=80,
        cmap="viridis",
        levels=None,
        extend=None,
        clims=None,
        outfile=None,
        var2display=None,
        region=None,
        baserun=None,
        datasource=None,

        # Scientific dimension selections
        time_index=None,      # time index (int)
        time_value=None,      # actual timestamp
        forecast_hour=None,   # hours since baserun (0 = analysis)
        bbox=None,            # [min_lon, max_lon, min_lat, max_lat]
        level=None,           # pressure level (hPa)
        depth=None,           # depth selection (meters)
        dataset=None,         # gfs, ecmwf, hycom

        **kwargs,
    ):
        # Explicit stable API
        self.proj = proj
        self.figsize = figsize
        self.dpi = dpi
        self.cmap = cmap
        self.palette = kwargs.pop("palette", None)
        self.levels = levels
        self.extend = extend
        self.clims = clims
        self.bbox = bbox
        self.outfile = outfile
        self.var2display = var2display
        self.baserun = baserun
        self.datasource = datasource

        # Flexible scientific configuration
        self.time_index = time_index
        self.time_value = time_value
        self.forecast_hour = forecast_hour
        self.level = level
        self.depth = depth
        self.dataset = dataset
        self.region = region

        self.quiver = {
            "skip": None,
            "scale": None,
            "width": 0.002,
            "color": "black",
            "landscape_aspect": 1.5,
            "per_inch_landscape": 1.9,
            "per_inch_portrait": 2.6,
            "arrow_frac": 0.024,
            "arrow_frac_landscape": 0.050,
        }

        self.windbarb = {
            "skip": None,
            "length": 5.0,
            "barbcolor": "black",
            "linewidth": 0.5,
            "pivot": "middle",
            "per_inch_landscape": 1.9,
            "per_inch_portrait": 2.6,
        }

        self.plot = {
            "scalar": {"method": None, "colormap_type": None},
            "vector": {"method": "none", "overlay": False},
            "colorbar": {
                "enabled": True,
                "orientation": "horizontal",
                "extend": None,
            },
        }

        self.contour = {
            "interval": 2,
            "linewidth": 0.7,
            "colors": "black",
        }

        # Dynamic config overrides (from YAML)
        for k, v in kwargs.items():
            setattr(self, k, v)