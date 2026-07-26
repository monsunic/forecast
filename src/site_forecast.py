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
from plotter.core.site_extract import (
    EXTRACTORS,
    append_hour_to_series,
    build_site_forecast_doc,
    empty_series_shell,
)

SITES_ROOT = ROOT / "assets" / "sites"


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


def _load_hour(dataset: str, cycle: str, forecast_hour: int):
    if dataset == "gfswave":
        from plotter.core.grib_loader import load_gfswave_forecast

        return load_gfswave_forecast(cycle, forecast_hour)
    if dataset == "gfsatmos":
        from plotter.core.grib_loader import load_gfsatmos_forecast

        return load_gfsatmos_forecast(cycle, forecast_hour)
    if dataset == "hycom":
        from plotter.core.hycom_loader import load_hycom_forecast

        return load_hycom_forecast(cycle, forecast_hour)
    raise ValueError(f"Unsupported dataset for site forecast: {dataset}")


def _valid_time_iso(ds) -> str | None:
    if "time" not in ds.coords:
        return None
    t = pd.Timestamp(np.asarray(ds["time"].values).reshape(-1)[0])
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _round_or_none(v, ndigits=2):
    if v is None:
        return None
    try:
        return round(float(v), ndigits)
    except (TypeError, ValueError):
        return None


def render_site_charts(doc: dict, outfile: Path) -> None:
    """Write a multi-panel WebP chart pack for download."""
    hours = doc.get("hours") or []
    series = doc.get("series") or {}
    site = doc.get("site") or {}
    x = list(range(len(hours)))
    labels = hours

    fig, axes = plt.subplots(3, 1, figsize=(10.5, 9.5), sharex=True, dpi=110)
    fig.suptitle(
        f"{site.get('name', site.get('id', 'Site'))} — Site Forecast",
        fontsize=13,
        fontfamily="monospace",
        y=0.98,
    )

    # --- Waves & wind ---
    ax = axes[0]
    wind = (series.get("wind_speed") or {}).get("values") or []
    swh = (series.get("swh") or {}).get("values") or []
    swell = (series.get("swell") or {}).get("values") or []
    if any(v is not None for v in wind):
        ax.plot(x, wind, color="#0B74DE", linewidth=1.6, label="Wind (kt)")
    if any(v is not None for v in swh):
        ax.plot(x, swh, color="#0B2340", linewidth=1.6, label="SWH (m)")
    if any(v is not None for v in swell):
        ax.plot(x, swell, color="#5B8DEF", linewidth=1.4, linestyle="--", label="Swell (m)")
    ax.set_ylabel("Waves / wind", fontsize=9)
    ax.legend(loc="upper right", fontsize=8, framealpha=0.85)
    ax.grid(True, alpha=0.25)
    ax.set_title("Waves & wind", fontsize=10, loc="left", pad=4)

    # --- Ocean ---
    ax = axes[1]
    sst = (series.get("sst") or {}).get("values") or []
    current = (series.get("current") or {}).get("values") or []
    if any(v is not None for v in sst):
        ax.plot(x, sst, color="#C45C26", linewidth=1.6, label="SST (°C)")
    if any(v is not None for v in current):
        ax2 = ax.twinx()
        ax2.plot(x, current, color="#1F7A5C", linewidth=1.5, label="Current (cm/s)")
        ax2.set_ylabel("Current (cm/s)", fontsize=9, color="#1F7A5C")
        ax2.tick_params(axis="y", labelcolor="#1F7A5C", labelsize=8)
        lines1, labels1 = ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(lines1 + lines2, labels1 + labels2, loc="upper right", fontsize=8, framealpha=0.85)
    else:
        ax.legend(loc="upper right", fontsize=8, framealpha=0.85)
    ax.set_ylabel("SST (°C)", fontsize=9)
    ax.grid(True, alpha=0.25)
    ax.set_title("Ocean", fontsize=10, loc="left", pad=4)

    # --- Weather ---
    ax = axes[2]
    rain = (series.get("rain") or {}).get("values") or []
    temp = (series.get("temp") or {}).get("values") or []
    rh = (series.get("rh") or {}).get("values") or []
    if any(v is not None for v in rain):
        ax.bar(x, [0 if v is None else v for v in rain], color="#6BA3D6", alpha=0.55, width=0.7, label="Rain (mm/hr)")
    if any(v is not None for v in temp):
        ax.plot(x, temp, color="#C0392B", linewidth=1.5, label="Temp (°C)")
    if any(v is not None for v in rh):
        ax_rh = ax.twinx()
        ax_rh.plot(x, rh, color="#7D3C98", linewidth=1.3, linestyle=":", label="RH (%)")
        ax_rh.set_ylabel("RH (%)", fontsize=9, color="#7D3C98")
        ax_rh.tick_params(axis="y", labelcolor="#7D3C98", labelsize=8)
        lines1, labels1 = ax.get_legend_handles_labels()
        lines2, labels2 = ax_rh.get_legend_handles_labels()
        ax.legend(lines1 + lines2, labels1 + labels2, loc="upper right", fontsize=8, framealpha=0.85)
    else:
        ax.legend(loc="upper right", fontsize=8, framealpha=0.85)
    ax.set_ylabel("Rain / temp", fontsize=9)
    ax.grid(True, alpha=0.25)
    ax.set_title("Weather", fontsize=10, loc="left", pad=4)

    step = max(1, len(labels) // 12)
    ax.set_xticks(x[::step])
    ax.set_xticklabels(labels[::step], rotation=45, ha="right", fontsize=8)
    ax.set_xlabel("Forecast lead", fontsize=9)

    cycles = doc.get("cycles") or {}
    cycle_bits = ", ".join(f"{k}={v}" for k, v in cycles.items())
    fig.text(
        0.01,
        0.005,
        f"Nusawave Forecast  |  {cycle_bits}  |  {doc.get('generated_at', '')}",
        fontsize=7,
        fontfamily="monospace",
        color="#4a5d73",
    )

    outfile.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=[0, 0.03, 1, 0.96])
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
                ds = _load_hour(dataset, cycle, t)
            except Exception as exc:
                print(f"[WARN] {dataset} no data at t+{t:03d}h, stopping: {exc}")
                break

            hour_label = f"F{t:03d}"
            valid = _valid_time_iso(ds)
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

        if not hours_done:
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
