#!/usr/bin/env python3
"""Extract Site Forecast JSON + downloadable static charts for fixed ports."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plotter.core.config_loader import (
    get_dataset_hour_step,
    get_default_max_hours,
    get_forecast_hours,
    get_hour_step,
    get_sites,
)
from plotter.core.dataset_hours import load_dataset_hour, valid_time_iso
from plotter.core.site_extract import (
    EXTRACTORS,
    append_hour_to_series,
    build_site_forecast_doc,
    empty_series_shell,
    merge_retained_series,
)
from plotter.core.tide import (
    hourly_tide_axis,
    load_constituents,
    predict_tide_series,
    site_entry,
    tide_series_entry,
)

SITES_ROOT = ROOT / "assets" / "sites"


def attach_astronomical_tide(doc: dict, constituents: dict | None = None) -> bool:
    """Predict hourly astronomical tide over the site forecast window.

    Tide keeps its own ``hours`` / ``valid_times`` (1-hour step) so it is not
    forced onto the coarser GFS axis. Always rebuilt from harmonics.
    """
    site = doc.get("site") or {}
    sid = site.get("id")
    valid_times = doc.get("valid_times") or []
    if not sid or not valid_times:
        return False
    try:
        table = constituents if constituents is not None else load_constituents()
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    entry = site_entry(table, sid)
    if not entry:
        return False

    tide_hours, tide_times = hourly_tide_axis(valid_times)
    if not tide_times:
        return False
    values = predict_tide_series(entry, tide_times)
    if not any(v is not None for v in values):
        return False
    model = str(table.get("model") or entry.get("model") or "tide")
    doc.setdefault("series", {})["tide"] = tide_series_entry(
        values, model, hours=tide_hours, valid_times=tide_times
    )
    doc.setdefault("cycles", {})["tide"] = model
    return True


def parse_args():
    parser = argparse.ArgumentParser(description="NusaWave Site Forecast extractor")
    parser.add_argument("--gfs-cycle", required=True, help="YYYYMMDDHH for GFS wave/atmos")
    parser.add_argument(
        "--hycom-cycle",
        default=None,
        help="YYYYMMDDHH for HYCOM (omit to skip ocean series)",
    )
    parser.add_argument(
        "--max-hours",
        type=int,
        default=None,
        help="Last forecast lead hour (default: config.yaml)",
    )
    parser.add_argument(
        "--hour-step",
        type=int,
        default=None,
        help="Lead-time step in hours (default: config.yaml)",
    )
    parser.add_argument(
        "--datasets",
        default="gfswave,gfsatmos,hycom",
        help="Comma-separated datasets to extract (default: gfswave,gfsatmos,hycom)",
    )
    parser.add_argument(
        "--site",
        action="append",
        dest="sites",
        default=None,
        help="Site id to process (repeatable; default: all from config.yaml)",
    )
    return parser.parse_args()


def _cycle_for(dataset: str, gfs_cycle: str, hycom_cycle: str | None) -> str | None:
    if dataset == "hycom":
        return hycom_cycle
    return gfs_cycle


def _round_or_none(v, ndigits=2):
    if v is None:
        return None
    try:
        return round(float(v), ndigits)
    except (TypeError, ValueError):
        return None


def _format_axis_label(iso: str | None, fallback: str) -> str:
    """Compact UTC tick, e.g. ``25 Jul 18Z``."""
    if not iso:
        return fallback
    months = (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )
    try:
        t = pd.Timestamp(iso)
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        else:
            t = t.tz_convert("UTC")
        return f"{t.day} {months[t.month - 1]} {t.hour:02d}Z"
    except (TypeError, ValueError):
        return fallback


def _nan_arr(values):
    return [np.nan if v is None else float(v) for v in values]


def _draw_dir_arrows(ax, x, y, dirs, *, color: str, convention: str = "from") -> None:
    """Annotate magnitude series with short TO/propagation arrows."""
    if not any(v is not None for v in y) or not any(d is not None for d in dirs):
        return
    n = len(x)
    step = max(1, n // 12)
    ymin, ymax = ax.get_ylim()
    span = (ymax - ymin) or 1.0
    arrow_dy = 0.07 * span
    for i in range(0, n, step):
        if y[i] is None or dirs[i] is None:
            continue
        try:
            deg = float(dirs[i])
            yi = float(y[i])
        except (TypeError, ValueError):
            continue
        if not np.isfinite(deg) or not np.isfinite(yi):
            continue
        to_deg = deg + 180.0 if convention == "from" else deg
        rad = np.radians(to_deg)
        dx = 0.42 * np.sin(rad)
        dy = arrow_dy * np.cos(rad)
        ax.annotate(
            "",
            xy=(x[i] + dx, yi + dy),
            xytext=(x[i] - dx * 0.35, yi - dy * 0.35),
            arrowprops=dict(arrowstyle="->", color=color, lw=1.3, mutation_scale=12),
            annotation_clip=True,
        )


def _plot_gapless(ax, x, values, **kwargs):
    """Plot a series that may be sampled coarser than the shared hour axis.

    Ocean fields run on a 6-hourly stride while the axis follows the 3-hourly
    GFS one, so plotting raw NaNs would leave every point isolated and nothing
    would be drawn. Connect the valid samples instead and mark them.
    """
    pts = [
        (xi, float(v))
        for xi, v in zip(x, values)
        if v is not None and np.isfinite(float(v))
    ]
    if not pts:
        return False
    xs, ys = zip(*pts)
    kwargs.setdefault("marker", "o")
    kwargs.setdefault("markersize", 3)
    ax.plot(xs, ys, **kwargs)
    return True


def _style_axes(ax, ax_right=None, ax_right2=None):
    ax.grid(True, alpha=0.25)
    ax.tick_params(axis="both", labelsize=8)
    if ax_right is not None:
        ax_right.tick_params(axis="y", labelsize=8)
    if ax_right2 is not None:
        ax_right2.tick_params(axis="y", labelsize=8)


def _merge_legends(*axes_list):
    handles, labels = [], []
    for ax in axes_list:
        if ax is None:
            continue
        h, lab = ax.get_legend_handles_labels()
        handles.extend(h)
        labels.extend(lab)
    if handles:
        axes_list[0].legend(handles, labels, loc="upper right", fontsize=8, framealpha=0.85)


def render_site_charts(doc: dict, outfile: Path) -> None:
    """Write a multi-panel WebP chart pack matching the interactive Chart.js layout."""
    hours = doc.get("hours") or []
    valid_times = doc.get("valid_times") or []
    series = doc.get("series") or {}
    site = doc.get("site") or {}
    n = len(hours)
    x = list(range(n))
    labels = [
        _format_axis_label(valid_times[i] if i < len(valid_times) else None, hours[i])
        for i in range(n)
    ]

    def _aligned(values):
        vals = list(values or [])
        if len(vals) < n:
            vals = vals + [None] * (n - len(vals))
        return vals[:n]

    def _entry(key):
        return series.get(key) or {}

    fig, axes = plt.subplots(4, 1, figsize=(10.5, 11.5), sharex=False, dpi=110)
    fig.suptitle(
        f"{site.get('name', site.get('id', 'Site'))} — Site Forecast",
        fontsize=13,
        fontfamily="monospace",
        y=0.98,
    )

    # --- Waves & wind: wind (left, kt) | SWH + swell (right, m) ---
    ax = axes[0]
    ax_r = ax.twinx()
    wind = _aligned(_entry("wind_speed").get("values"))
    wind_dir = _aligned(_entry("wind_speed").get("dir_deg"))
    swh = _aligned(_entry("swh").get("values"))
    swh_dir = _aligned(_entry("swh").get("dir_deg"))
    swell = _aligned(_entry("swell").get("values"))
    swell_dir = _aligned(_entry("swell").get("dir_deg"))
    plotted = False
    if any(v is not None for v in wind):
        ax.plot(x, _nan_arr(wind), color="#0B74DE", linewidth=1.8, label="Wind (kt)")
        plotted = True
    if any(v is not None for v in swh):
        ax_r.plot(x, _nan_arr(swh), color="#0B2340", linewidth=1.8, label="SWH (m)")
        plotted = True
    if any(v is not None for v in swell):
        ax_r.plot(
            x, _nan_arr(swell), color="#5B8DEF", linewidth=1.8,
            linestyle=(0, (5, 4)), label="Swell (m)",
        )
        plotted = True
    if plotted:
        _draw_dir_arrows(ax, x, wind, wind_dir, color="#0B74DE", convention="from")
        _draw_dir_arrows(ax_r, x, swh, swh_dir, color="#0B2340", convention="from")
        _draw_dir_arrows(ax_r, x, swell, swell_dir, color="#5B8DEF", convention="from")
        _merge_legends(ax, ax_r)
    ax.set_ylabel("kt", fontsize=9)
    ax_r.set_ylabel("m", fontsize=9)
    ax.set_title("Waves & wind", fontsize=10, loc="left", pad=4)
    _style_axes(ax, ax_r)

    # --- Ocean: SST (left) | current (right) ---
    ax = axes[1]
    ax_r = ax.twinx()
    sst = _aligned(_entry("sst").get("values"))
    current = _aligned(_entry("current").get("values"))
    current_dir = _aligned(_entry("current").get("dir_deg"))
    plotted = _plot_gapless(
        ax, x, sst, color="#C45C26", linewidth=1.8, label="SST (°C)"
    )
    if _plot_gapless(
        ax_r, x, current, color="#1F7A5C", linewidth=1.8, label="Current (cm/s)"
    ):
        _draw_dir_arrows(ax_r, x, current, current_dir, color="#1F7A5C", convention="to")
        plotted = True
    if plotted:
        _merge_legends(ax, ax_r)
    ax.set_ylabel("°C", fontsize=9)
    ax_r.set_ylabel("cm/s", fontsize=9)
    ax.set_title("Ocean", fontsize=10, loc="left", pad=4)
    _style_axes(ax, ax_r)

    # --- Weather: rain (left) | temp (right) | RH (far right) ---
    ax = axes[2]
    ax_r = ax.twinx()
    ax_r2 = ax.twinx()
    ax_r2.spines["right"].set_position(("axes", 1.12))
    rain = _aligned(_entry("rain").get("values"))
    temp = _aligned(_entry("temp").get("values"))
    rh = _aligned(_entry("rh").get("values"))
    plotted = False
    if any(v is not None for v in rain):
        ax.bar(
            x, [0.0 if v is None else float(v) for v in rain],
            color="#6BA3D6", alpha=0.6, width=0.7, label="Rain (mm/hr)",
        )
        plotted = True
    if any(v is not None for v in temp):
        ax_r.plot(x, _nan_arr(temp), color="#C0392B", linewidth=1.8, label="Temp (°C)")
        plotted = True
    if any(v is not None for v in rh):
        ax_r2.plot(
            x, _nan_arr(rh), color="#7D3C98", linewidth=1.5,
            linestyle=(0, (2, 3)), label="RH (%)",
        )
        plotted = True
    if plotted:
        _merge_legends(ax, ax_r, ax_r2)
    ax.set_ylabel("mm/hr", fontsize=9)
    ax_r.set_ylabel("°C", fontsize=9, color="#C0392B")
    ax_r.tick_params(axis="y", labelcolor="#C0392B")
    ax_r2.set_ylabel("%", fontsize=9, color="#7D3C98")
    ax_r2.tick_params(axis="y", labelcolor="#7D3C98")
    ax.set_title("Weather", fontsize=10, loc="left", pad=4)
    _style_axes(ax, ax_r, ax_r2)

    # --- Tide: astronomical height on its own hourly axis ---
    ax = axes[3]
    tide_entry = _entry("tide")
    tide_times = list(tide_entry.get("valid_times") or [])
    tide_hours = list(tide_entry.get("hours") or [])
    tide_vals = list(tide_entry.get("values") or [])
    tide_model = (tide_entry.get("model") or "tide").strip()
    if tide_times and any(v is not None for v in tide_vals):
        n_tide = len(tide_times)
        tide_x = list(range(n_tide))
        if len(tide_vals) < n_tide:
            tide_vals = tide_vals + [None] * (n_tide - len(tide_vals))
        tide_vals = tide_vals[:n_tide]
        tide_labels = [
            _format_axis_label(
                tide_times[i],
                tide_hours[i] if i < len(tide_hours) else None,
            )
            for i in range(n_tide)
        ]
        ax.plot(
            tide_x,
            _nan_arr(tide_vals),
            color="#1B4F72",
            linewidth=1.8,
            label=f"Tide ({tide_model})",
        )
        ax.axhline(0.0, color="#9db0c3", linewidth=0.8, linestyle=(0, (4, 3)))
        ax.legend(loc="upper right", fontsize=8, framealpha=0.85)
        step_t = max(1, n_tide // 12)
        ax.set_xticks(tide_x[::step_t])
        ax.set_xticklabels(tide_labels[::step_t], rotation=45, ha="right", fontsize=8)
    else:
        ax.set_xticks([])
    ax.set_ylabel("m", fontsize=9)
    ax.set_title("Tide (astronomical, hourly)", fontsize=10, loc="left", pad=4)
    _style_axes(ax)
    ax.set_xlabel("Valid time (UTC)", fontsize=9)

    # Shared GFS axis labels on the weather panel (tide has its own ticks above).
    step = max(1, len(labels) // 12) if labels else 1
    for i in (0, 1):
        axes[i].set_xticks(x[::step] if x else [])
        axes[i].tick_params(axis="x", labelbottom=False)
    axes[2].set_xticks(x[::step] if x else [])
    axes[2].set_xticklabels(labels[::step] if labels else [], rotation=45, ha="right", fontsize=8)

    cycles = doc.get("cycles") or {}
    cycle_bits = ", ".join(f"{k}={v}" for k, v in cycles.items())
    fig.text(
        0.01,
        0.005,
        (
            f"Nusawave Forecast  |  {cycle_bits}  |  {doc.get('generated_at', '')}  |  "
            "Arrows = wind/wave propagation & current flow; tide = astronomical hourly (no surge)"
        ),
        fontsize=7,
        fontfamily="monospace",
        color="#4a5d73",
    )

    outfile.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=[0, 0.03, 0.96, 0.96])
    fig.savefig(outfile, format="webp", dpi=110, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def run_site_forecast(
    gfs_cycle: str,
    hycom_cycle: str | None,
    max_hours: int,
    hour_step: int,
    datasets: list[str],
    site_ids: list[str] | None = None,
) -> list[Path]:
    sites = get_sites()
    if site_ids:
        wanted = set(site_ids)
        sites = [s for s in sites if s["id"] in wanted]
    if not sites:
        raise SystemExit("[ERROR] No sites to process")

    datasets = [d.strip() for d in datasets if d.strip()]
    for ds in datasets:
        if ds not in EXTRACTORS:
            raise SystemExit(f"[ERROR] Unsupported site dataset: {ds}")

    cycles = {}
    for ds in datasets:
        cyc = _cycle_for(ds, gfs_cycle, hycom_cycle)
        if cyc:
            cycles[ds] = cyc

    # Accumulators per site
    state = {
        s["id"]: {
            "site": s,
            "series": empty_series_shell(),
            "hours": [],
            "valid_times": [],
            "grid_points": {},
            "hour_vals": {},  # hour_label -> merged hour dict
        }
        for s in sites
    }

    # Datasets that produced at least one usable hour; the rest fall back to
    # whatever the previous publication holds.
    extracted: list[str] = []

    # Process dataset-by-dataset so a missing HYCOM cycle does not block wave/weather.
    for dataset in datasets:
        cycle = cycles.get(dataset)
        if not cycle:
            print(f"[WARN] Skipping {dataset}: no cycle")
            continue
        extract = EXTRACTORS[dataset]
        hours_done = []

        # Match the map pipeline's per-dataset stride (e.g. HYCOM 6-hourly) so
        # site extraction reuses the same cached downloads instead of re-fetching
        # the odd-hour NCSS frames that 6-hourly maps skip.
        ds_step = get_dataset_hour_step(dataset, hour_step)
        forecast_hours = get_forecast_hours(max_hours=max_hours, hour_step=ds_step)

        for t in forecast_hours:
            try:
                ds = load_dataset_hour(dataset, cycle, t)
            except Exception as exc:
                print(f"[WARN] {dataset} no data at t+{t:03d}h, stopping: {exc}")
                break

            hour_label = f"F{t:03d}"
            valid = valid_time_iso(ds)
            hours_done.append(t)

            for site in sites:
                sid = site["id"]
                try:
                    vals = extract(ds, site["lat"], site["lon"])
                except Exception as exc:
                    print(f"[WARN] Extract failed {dataset}/{sid} F{t:03d}: {exc}")
                    vals = {}

                grid = vals.pop("_grid", {}) or {}
                for gkey, gval in grid.items():
                    state[sid]["grid_points"][gkey] = gval

                bucket = state[sid]["hour_vals"].setdefault(
                    hour_label, {"_valid": valid, "_order": t}
                )
                if valid and not bucket.get("_valid"):
                    bucket["_valid"] = valid
                bucket.update({k: _round_or_none(v, 3) for k, v in vals.items()})

            print(f"[INFO] Site extract {dataset} t+{t:03d}h ({len(sites)} sites)")

        if hours_done:
            extracted.append(dataset)
        else:
            print(f"[WARN] No hours extracted for {dataset}")

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    written: list[Path] = []

    for sid, st in state.items():
        # Stable hour order across datasets (union of hours that have any values)
        ordered_hours = sorted(
            st["hour_vals"].keys(),
            key=lambda h: st["hour_vals"][h].get("_order", 0),
        )
        if not ordered_hours:
            print(f"[WARN] No data for site {sid}; skipping")
            continue

        series = empty_series_shell()
        hours = []
        valid_times = []
        for h in ordered_hours:
            vals = st["hour_vals"][h]
            hours.append(h)
            valid_times.append(vals.get("_valid"))
            append_hour_to_series(series, vals)

        # Drop series that are entirely empty / all-null for this site
        pruned = {}
        for key, entry in series.items():
            vals = entry.get("values") or []
            if any(v is not None for v in vals):
                pruned[key] = entry

        doc = build_site_forecast_doc(
            site=st["site"],
            cycles=cycles,
            hours=hours,
            valid_times=valid_times,
            series=pruned,
            grid_points=st["grid_points"],
            generated_at=generated_at,
        )

        out_dir = SITES_ROOT / sid
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "forecast.json"
        previous = None
        if json_path.is_file():
            try:
                previous = json.loads(json_path.read_text())
            except (OSError, json.JSONDecodeError, TypeError):
                previous = None
        retained = merge_retained_series(doc, previous, refreshed_datasets=extracted)
        if retained:
            print(f"[INFO] Retained previous {', '.join(retained)} series for {sid}")
        if attach_astronomical_tide(doc):
            print(f"[INFO] Attached astronomical tide for {sid}")

        json_path.write_text(json.dumps(doc, indent=2) + "\n")
        chart_path = out_dir / "charts.webp"
        try:
            render_site_charts(doc, chart_path)
        except Exception as exc:
            print(f"[WARN] Chart render failed for {sid}: {exc}")
        print(f"[INFO] Wrote {json_path}")
        written.append(json_path)

    if not written:
        raise SystemExit("[ERROR] No site forecast files written")
    return written


def main():
    args = parse_args()
    max_hours = args.max_hours if args.max_hours is not None else get_default_max_hours()
    hour_step = args.hour_step if args.hour_step is not None else get_hour_step()
    datasets = [d.strip() for d in args.datasets.replace(" ", ",").split(",") if d.strip()]

    print(
        f"[INFO] Site forecast: GFS={args.gfs_cycle}, HYCOM={args.hycom_cycle or 'skip'}, "
        f"F000…F{max_hours:03d} step {hour_step}h, datasets={datasets}"
    )
    run_site_forecast(
        gfs_cycle=args.gfs_cycle,
        hycom_cycle=args.hycom_cycle,
        max_hours=max_hours,
        hour_step=hour_step,
        datasets=datasets,
        site_ids=args.sites,
    )


if __name__ == "__main__":
    main()
