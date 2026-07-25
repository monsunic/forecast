import importlib
import math
import os
import sys
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from cartopy.mpl.gridliner import LONGITUDE_FORMATTER, LATITUDE_FORMATTER
from .utils import get_projection, deep_update
from .config_loader import load_param_config, apply_product_config
from pathlib import Path
from matplotlib.patches import FancyBboxPatch
from matplotlib.ticker import FuncFormatter
from mpl_toolkits.axes_grid1.inset_locator import inset_axes
from datetime import datetime
import pandas as pd
from .colormaps import colorbar_ticks

def _ensure_project_root():
    """
    Ensure the project root (containing the 'plotter' package)
    is available in sys.path, regardless of where the script is executed.
    """
    current_file = Path(__file__).resolve()

    root = current_file.parents[2]

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

class Plotter:
    """Main engine to plot any parameter using plugin handlers."""

    def __init__(self, config):
        self.config = config
        self.yaml_cfg  = load_param_config()

    def __format_tick__(self, x, pos):
        return f'{x:g}'

    def _apply_param_config(self, param):
        defaults = self.yaml_cfg.get("defaults", {})
        for key, value in defaults.items():
            if key in ("quiver", "windbarb", "contour") and isinstance(value, dict):
                deep_update(getattr(self.config, key), value)
            elif hasattr(self.config, key) and isinstance(getattr(self.config, key), dict) and isinstance(value, dict):
                deep_update(getattr(self.config, key), value)
            elif key not in ("proj", "dpi", "figsize", "fileformat"):
                setattr(self.config, key, value)

        apply_product_config(self.config, param, self.yaml_cfg)

    def _apply_region_config(self, region):
        settings = self.yaml_cfg.get("regions", {}).get(region, {})
        for key, value in settings.items():
            if hasattr(self.config, key) and isinstance(getattr(self.config, key), dict):
                deep_update(getattr(self.config, key), value)
            else:
                setattr(self.config, key, value)

    def _load_handler(self, param):
        
        _ensure_project_root()
        module_name = f"plotter.handlers.{param}"

        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as e:
            raise ModuleNotFoundError(f"[ERROR] Handler '{module_name}' not found: {e}")

        class_name = param.capitalize() + "Handler"

        if not hasattr(module, class_name):
            raise ImportError(f"[ERROR] Class '{class_name}' not found in '{module_name}'")

        handler_class = getattr(module, class_name)
        return handler_class(self.config)

    def _region_label(self):
        region = self.config.region or ""
        return region.replace("_", " ").title()

    def _forecast_hour(self):
        if self.config.forecast_hour is not None:
            return int(self.config.forecast_hour)
        if self.config.baserun is not None and self.config.time_value is not None:
            delta = (
                pd.Timestamp(self.config.time_value) - pd.Timestamp(self.config.baserun)
            ).total_seconds() / 3600
            return int(round(delta))
        return 0

    def _valid_time(self):
        if self.config.baserun is not None and self.config.forecast_hour is not None:
            return pd.Timestamp(self.config.baserun) + pd.Timedelta(hours=self.config.forecast_hour)
        if self.config.time_value is not None:
            return pd.Timestamp(self.config.time_value)
        return pd.Timestamp(self.config.baserun)

    def _time_annotation_lines(self):
        baserun = pd.Timestamp(self.config.baserun)
        valid = self._valid_time()
        fh = self._forecast_hour()
        initime = f"Initial: {baserun.strftime('%b %d, %Y - %HUTC')}"
        if fh == 0:
            valid_line = f"Analysis: {valid.strftime('%b %d, %Y - %HUTC')} (t+000h)"
        else:
            valid_line = f"Forecast: {valid.strftime('%b %d, %Y - %HUTC')} (t+{fh:03d}h)"
        return initime, valid_line

    def _map_aspect(self):
        """Height/width ratio of the geographic domain in Mercator-ish screen space."""
        bbox = self.config.bbox or [90, 150, -20, 25]
        lon_span = max(bbox[1] - bbox[0], 1e-6)
        lat_span = max(bbox[3] - bbox[2], 1e-6)
        lat_mid = (bbox[2] + bbox[3]) / 2
        map_w = max(lon_span * math.cos(math.radians(lat_mid)), 1e-6)
        return lat_span / map_w, lon_span, lat_span

    def _resolve_figsize(self):
        """
        Size the canvas to geographic aspect, with absolute min width/height so
        in-map chrome does not crush on extreme landscape or portrait regions.
        """
        map_aspect, _, _ = self._map_aspect()
        portrait = map_aspect > 1.05

        if portrait:
            # Tall domains need enough width for legend + colorbar + credit.
            height = min(10.5, max(7.2, 5.8 * map_aspect))
            width = height / map_aspect
            if width < 7.4:
                width = 7.4
                height = width * map_aspect
        else:
            # Wide domains need enough height for title + bottom panel.
            width = min(12.0, max(8.5, 8.2 / max(map_aspect, 0.28)))
            height = width * map_aspect
            if height < 5.8:
                height = 5.8
                width = height / map_aspect
        return (width, height), portrait

    def _panel(self, facecolor="white", alpha=0.9, pad=2.2):
        return dict(facecolor=facecolor, edgecolor="none", alpha=alpha, pad=pad)

    def _grey_text_panel(self, pad=0.22):
        """Grey badge that hugs its text — padding scales with the font, not the figure."""
        return dict(
            boxstyle=f"round,pad={pad}",
            facecolor="#D1D5DB",
            edgecolor="#9CA3AF",
            linewidth=0.35,
            alpha=0.88,
        )

    def _chrome_layout(self, figsize, portrait, has_vector):
        """Bottom chrome geometry measured in inches, returned as axes fractions.

        Sizing from the text metrics keeps the grey panel hugging its contents at
        any figure aspect instead of leaving slack that grows with the map width.
        """
        ax_w = figsize[0] * 0.98
        ax_h = figsize[1] * 0.98
        # Wide domains render on physically larger canvases, so grow the type with
        # them to keep the chrome legible once the viewer scales the image down.
        scale = min(1.45, max(1.0, ax_w / 9.5))
        legend_size = 7.0 * scale
        tick_size = 6.5 * scale
        unit_size = 7.0 * scale
        source_size = 6.0 * scale
        credit_size = 7.0 * scale
        pad = 0.05 * scale

        def fx(inches):
            return inches / ax_w

        def fy(inches):
            return inches / ax_h

        def text_w(text, size):
            return len(str(text)) * size * 0.60 / 72.0

        bar_h = 0.09 * scale
        source_h = source_size * 1.35 / 72.0
        tick_h = tick_size * 1.35 / 72.0
        panel_x, panel_y = 0.06, 0.05
        content_x = panel_x + pad
        bar_y = panel_y + pad + source_h + 0.02 * scale + tick_h
        panel_h = (bar_y - panel_y) + bar_h + pad

        legend_label = getattr(self.config, "arrlabel", "Direction") or ""
        symbol_w = (0.34 if self._vector_method() == "windbarb" else 0.26) * scale
        label_x = content_x + symbol_w + 0.05 * scale if has_vector else content_x
        bar_x = label_x + (text_w(legend_label, legend_size) + 0.14 if has_vector else 0.0)

        bar_w = max(2.6, min(0.40 * ax_w, 5.6))
        # Matplotlib draws the extend triangle past the axes edge, ~5% of the bar length.
        unit_gap = 0.06 * bar_w + 0.06
        unit_w = text_w(getattr(self.config, "unit", "") or "", unit_size)
        panel_w = (bar_x + bar_w + unit_gap + unit_w + pad) - panel_x

        return {
            "left_panel": (fx(panel_x), fy(panel_y), fx(panel_w), fy(panel_h)),
            "legend_anchor": (fx(content_x), fy(bar_y), 1, 1),
            "legend_size_in": (symbol_w, bar_h),
            "legend_label_xy": (fx(label_x), fy(bar_y + bar_h / 2)),
            "cbar_anchor": (fx(bar_x), fy(bar_y), 1, 1),
            "cbar_size_in": (bar_w, bar_h),
            "unit_x": 1.0 + unit_gap / bar_w,
            "tick_size": tick_size,
            "unit_size": unit_size,
            "legend_size": legend_size,
            "source_size": source_size,
            "credit_size": credit_size,
            "source_xy": (fx(content_x), fy(panel_y + pad)),
            "credit_xy": (1.0 - fx(0.06), fy(panel_y)),
            "panel_top": fy(panel_y + panel_h),
            "title_size": 9.5 * scale,
            "time_size": 8.5 * scale,
            "grid_size": 5.5 * scale,
            "symbol_scale": scale,
        }

    def _vector_method(self):
        plot_cfg = getattr(self.config, "plot", {}) or {}
        vector_cfg = plot_cfg.get("vector", {}) if isinstance(plot_cfg, dict) else {}
        return (vector_cfg or {}).get("method", "none")

    def _add_bottom_panels(self, ax, chrome):
        """Grey strip behind the legend + colorbar + source group."""
        lx, ly, lw, lh = chrome["left_panel"]
        ax.add_patch(
            FancyBboxPatch(
                (lx, ly),
                lw,
                lh,
                boxstyle="round,pad=0.0015",
                linewidth=0.35,
                edgecolor="#9CA3AF",
                facecolor="#D1D5DB",
                alpha=0.88,
                transform=ax.transAxes,
                clip_on=False,
                zorder=8,
            )
        )

    def _add_vector_legend(self, ax, chrome):
        """Direction legend on the left of the colorbar row."""
        vector_method = self._vector_method()
        label = getattr(self.config, "arrlabel", "Direction")
        lx, ly = chrome["legend_label_xy"]
        label_size = chrome["legend_size"]
        sym_w, sym_h = chrome["legend_size_in"]

        if vector_method == "windbarb":
            barb_ax = inset_axes(
                ax,
                width=sym_w,
                height=sym_h,
                loc="lower left",
                bbox_to_anchor=chrome["legend_anchor"],
                bbox_transform=ax.transAxes,
                borderpad=0,
            )
            barb_ax.set_facecolor("none")
            for spine in barb_ax.spines.values():
                spine.set_visible(False)
            barb_ax.set_xticks([])
            barb_ax.set_yticks([])
            barb_ax.barbs(
                [0.4],
                [0.5],
                [25],
                [0],
                length=4.8 * chrome["symbol_scale"],
                barbcolor="black",
                linewidth=0.5 * chrome["symbol_scale"],
                pivot="middle",
            )
            barb_ax.set_xlim(0, 1)
            barb_ax.set_ylim(0, 1)
            ax.text(
                lx,
                ly,
                label,
                transform=ax.transAxes,
                fontsize=label_size,
                ha="left",
                va="center",
                fontfamily="monospace",
                zorder=10,
            )
            return

        arr_ax = inset_axes(
            ax,
            width=sym_w,
            height=sym_h,
            loc="lower left",
            bbox_to_anchor=chrome["legend_anchor"],
            bbox_transform=ax.transAxes,
            borderpad=0,
        )
        arr_ax.set_facecolor("none")
        for spine in arr_ax.spines.values():
            spine.set_visible(False)
        arr_ax.set_xticks([])
        arr_ax.set_yticks([])
        arr_ax.annotate(
            "",
            xy=(0.88, 0.5),
            xytext=(0.1, 0.5),
            arrowprops=dict(arrowstyle="->", color="k", lw=0.8 * chrome["symbol_scale"]),
            xycoords="axes fraction",
        )
        arr_ax.set_xlim(0, 1)
        arr_ax.set_ylim(0, 1)
        ax.text(
            lx,
            ly,
            label,
            transform=ax.transAxes,
            fontsize=label_size,
            ha="left",
            va="center",
            fontfamily="monospace",
            zorder=10,
        )

    def _add_map_annotations(self, ax, portrait, chrome):
        _, fcstime = self._time_annotation_lines()
        title_size = chrome["title_size"]
        time_size = chrome["time_size"]
        title = f"{self.config.var2display}\n{self._region_label()}"
        sx, sy = chrome["source_xy"]
        cx, cy = chrome["credit_xy"]

        ax.text(
            0.012,
            0.985,
            title,
            transform=ax.transAxes,
            fontsize=title_size,
            ha="left",
            va="top",
            fontfamily="monospace",
            linespacing=1.2,
            zorder=10,
            bbox=self._panel(pad=1.8),
        )
        ax.text(
            0.988,
            0.985,
            fcstime,
            transform=ax.transAxes,
            fontsize=time_size,
            ha="right",
            va="top",
            fontfamily="monospace",
            zorder=10,
            bbox=self._panel(pad=1.8),
        )
        ax.text(
            sx,
            sy,
            f"Source: {self.config.datasource}",
            transform=ax.transAxes,
            fontsize=chrome["source_size"],
            ha="left",
            va="bottom",
            fontfamily="monospace",
            zorder=10,
        )
        ax.text(
            cx,
            cy,
            f"Nusawave Forecast \u00A9{datetime.now().year}",
            transform=ax.transAxes,
            fontsize=chrome["credit_size"],
            ha="right",
            va="bottom",
            fontfamily="monospace",
            zorder=10,
            bbox=self._grey_text_panel(pad=0.22),
        )

    def _apply_bbox(self, ax, bbox):
        if bbox:
            min_lon, max_lon, min_lat, max_lat = bbox
            ax.set_extent([min_lon, max_lon, min_lat, max_lat], crs=ccrs.PlateCarree())
            try:
                ax.set_aspect("auto")
            except Exception:
                pass

    def plot_map(self, ds, param):
        self._apply_region_config(self.config.region)
        self._apply_param_config(param)
        handler = self._load_handler(param)
        data = handler.load(ds)

        plot_cfg = getattr(self.config, "plot", {}) or {}
        colorbar_cfg = plot_cfg.get("colorbar", {}) if isinstance(plot_cfg, dict) else {}
        vector_method = (plot_cfg.get("vector") or {}).get("method", "none")
        has_vector = vector_method not in (None, "none")

        figsize, portrait = self._resolve_figsize()
        self.config.figsize = figsize
        chrome = self._chrome_layout(figsize, portrait, has_vector)

        proj = get_projection(self.config.proj)
        fig = plt.figure(figsize=figsize, dpi=self.config.dpi)
        # Edge-to-edge map — overlays live inside axes, no outer chrome bands.
        ax = fig.add_axes([0.01, 0.01, 0.98, 0.98], projection=proj)

        self._apply_bbox(ax, self.config.bbox)
        im, iq = handler.plot(ax, data)
        self._add_bottom_panels(ax, chrome)

        colorbar_enabled = colorbar_cfg.get("enabled", im is not None) and im is not None
        cbar_extend = colorbar_cfg.get("extend", self.config.extend)

        if colorbar_enabled:
            cbar_ax = inset_axes(
                ax,
                width=chrome["cbar_size_in"][0],
                height=chrome["cbar_size_in"][1],
                loc="lower left",
                bbox_to_anchor=chrome["cbar_anchor"],
                bbox_transform=ax.transAxes,
                borderpad=0,
            )
            cbar = fig.colorbar(
                im,
                cax=cbar_ax,
                ticks=colorbar_ticks(self.config.levels),
                orientation=colorbar_cfg.get("orientation", "horizontal"),
                extend=cbar_extend,
            )
            cbar.ax.tick_params(direction="inout", labelsize=chrome["tick_size"], pad=0.6)
            cbar.ax.xaxis.set_major_formatter(FuncFormatter(self.__format_tick__))
            cbar.set_label("")
            cbar_ax.set_facecolor("#E5E7EB")
            # Keep unit clear of the extend triangle / end ticks.
            cbar_ax.text(
                chrome["unit_x"],
                0.5,
                f"{self.config.unit}",
                va="center",
                ha="left",
                fontsize=chrome["unit_size"],
                fontname="monospace",
                transform=cbar_ax.transAxes,
                clip_on=False,
            )

        if iq is not None or has_vector:
            if iq is not None:
                self._add_vector_legend(ax, chrome)

        ax.coastlines(linewidth=0.8, zorder=2)
        ax.add_feature(cfeature.BORDERS, linewidth=0.7, zorder=3)
        ax.add_feature(cfeature.LAND, edgecolor="black", facecolor="gray", zorder=2)
        self._apply_bbox(ax, self.config.bbox)
        ax.set_aspect("auto")
        ax.spines["geo"].set_visible(True)
        gl = ax.gridlines(
            crs=ccrs.PlateCarree(),
            draw_labels=False,
            linewidth=0.3,
            color="w",
            linestyle="--",
        )

        xs = gl.xlocator.tick_values(*ax.get_extent(ccrs.PlateCarree())[:2])
        ys = gl.ylocator.tick_values(*ax.get_extent(ccrs.PlateCarree())[2:])

        extent = ax.get_extent(ccrs.PlateCarree())
        lon_min, lon_max, lat_min, lat_max = extent
        lon_span = lon_max - lon_min
        lat_span = lat_max - lat_min

        # Lon labels sit just above the compact bottom panel; skip the center strip.
        label_step = 2 if portrait else 1
        lon_label_lat = lat_min + lat_span * (chrome["panel_top"] + 0.012)
        for i, x in enumerate(xs[1:-1]):
            if i % label_step:
                continue
            frac = (x - lon_min) / lon_span if lon_span else 0.5
            if 0.18 < frac < 0.82:
                continue
            ax.text(
                x,
                lon_label_lat,
                LONGITUDE_FORMATTER(x),
                transform=ccrs.PlateCarree(),
                ha="center",
                va="bottom",
                fontsize=chrome["grid_size"],
                color="black",
                bbox=dict(fc="lightgrey", alpha=0.75, ec="none", boxstyle="round,pad=0.25"),
                zorder=6,
            )

        lat_label_x = lon_min + lon_span * 0.012
        for i, y in enumerate(ys[1:-1]):
            if i % label_step:
                continue
            y_frac = (y - lat_min) / lat_span if lat_span else 0.5
            if y_frac < chrome["panel_top"] + 0.05 or y_frac > 0.90:
                continue
            ax.text(
                lat_label_x,
                y,
                LATITUDE_FORMATTER(y),
                transform=ccrs.PlateCarree(),
                ha="left",
                va="center",
                fontsize=chrome["grid_size"],
                color="black",
                bbox=dict(fc="lightgrey", alpha=0.75, ec="none", boxstyle="round,pad=0.25"),
                zorder=6,
            )

        self._add_map_annotations(ax, portrait, chrome)

        if self.config.outfile:
            fname = f"{self.config.outfile}.{self.config.fileformat}"
            out_dir = os.path.dirname(self.config.outfile)
            if out_dir and not os.path.exists(out_dir):
                os.makedirs(out_dir)
            plt.savefig(
                fname,
                format=self.config.fileformat,
                dpi=self.config.dpi,
                bbox_inches=None,
                pad_inches=0.0,
                facecolor="white",
            )
            print(f"[INFO] File saved at {fname}")

        plt.close(fig)

    def plot_route(self, ds, route_points):
        """Later: along-track interpolation."""
        raise NotImplementedError

    def plot_station(self, ds, lat, lon):
        """Later: time-series plotting."""
        raise NotImplementedError
