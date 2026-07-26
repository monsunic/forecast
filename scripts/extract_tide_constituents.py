#!/usr/bin/env python3
"""Extract per-port tidal harmonics from a global atlas into a compact JSON table.

Preferred model is FES2022 (AVISO atlas via ``NW_TIDE_DIRECTORY`` / pyTMD cache).
When FES is not installed, GOT4.10 can be fetched publicly and used as a bootstrap
so Site Forecast still ships working astronomical-tide charts.

Examples::

    # Public bootstrap (downloads GOT4.10 on first run)
    python scripts/extract_tide_constituents.py --model GOT4.10 --fetch

    # Production FES2022 once the atlas is registered/downloaded
    NW_TIDE_DIRECTORY=/data/tides python scripts/extract_tide_constituents.py \\
        --model FES2022_extrapolated
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plotter.core.config_loader import get_sites  # noqa: E402

OUT_DEFAULT = ROOT / "plotter" / "data" / "tide_constituents.json"


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--model",
        default=os.environ.get("NW_TIDE_MODEL", "FES2022_extrapolated"),
        help="pyTMD model name (default: FES2022_extrapolated, or NW_TIDE_MODEL)",
    )
    p.add_argument(
        "--directory",
        default=os.environ.get("NW_TIDE_DIRECTORY") or None,
        help="Tide atlas root (default: pyTMD cache / NW_TIDE_DIRECTORY)",
    )
    p.add_argument(
        "--fetch",
        action="store_true",
        help="For GOT* models, download the atlas from NASA GSFC if missing",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=OUT_DEFAULT,
        help=f"Output JSON path (default: {OUT_DEFAULT})",
    )
    p.add_argument(
        "--extrapolate",
        action="store_true",
        default=True,
        help="Extrapolate atlas values onto coastal port coordinates (default on)",
    )
    return p.parse_args()


def _maybe_fetch_got(model: str, directory: Path | None):
    if not model.upper().startswith("GOT"):
        return
    from pyTMD.datasets import fetch_gsfc_got

    fetch_gsfc_got(model=model, directory=directory, format="ascii", compressed=True)


def extract_site(ds, lon: float, lat: float, extrapolate: bool) -> dict:
    local = ds.tmd.interp(float(lon), float(lat), extrapolate=bool(extrapolate))
    names = list(ds.tmd.constituents)
    amp = []
    phase = []
    for name in names:
        z = np.asarray(local[name].values).item()
        if not np.isfinite(z):
            amp.append(None)
            phase.append(None)
            continue
        # Dataset values are already metres for GOT/FES via pyTMD open_dataset.
        a = float(np.abs(z))
        g = float(np.degrees(np.angle(z))) % 360.0
        amp.append(round(a, 6))
        phase.append(round(g, 3))
    return {
        "lat": round(float(local.y.values), 4),
        "lon": round(float(local.x.values), 4),
        "constituents": names,
        "amplitude_m": amp,
        "phase_deg": phase,
    }


def main():
    args = parse_args()
    from pyTMD.io.model import model as tide_model
    from pyTMD.utilities import get_cache_path

    directory = Path(args.directory) if args.directory else Path(get_cache_path())
    directory.mkdir(parents=True, exist_ok=True)

    if args.fetch:
        print(f"[INFO] Fetching {args.model} into {directory}")
        _maybe_fetch_got(args.model, directory)

    try:
        m = tide_model(directory=directory).from_database(args.model)
    except FileNotFoundError as exc:
        raise SystemExit(
            f"[ERROR] Tide model '{args.model}' not found under {directory}.\n"
            f"  For GOT bootstrap: add --fetch\n"
            f"  For FES2022: download the AVISO atlas and set NW_TIDE_DIRECTORY\n"
            f"  Detail: {exc}"
        ) from exc

    print(f"[INFO] Opening {m.name} ({m.format})")
    ds = m.open_dataset(group="z")
    sites = get_sites()
    table = {
        "model": m.name,
        "corrections": getattr(m, "corrections", None) or "FES",
        "reference": getattr(m, "reference", None),
        "extracted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "Astronomical tide harmonics for Site Forecast ports (no surge).",
        "sites": {},
    }
    for site in sites:
        entry = extract_site(ds, site["lon"], site["lat"], args.extrapolate)
        entry["name"] = site["name"]
        entry["corrections"] = table["corrections"]
        # Prefer the configured port coordinates for metadata; interp may snap.
        entry["port_lat"] = site["lat"]
        entry["port_lon"] = site["lon"]
        table["sites"][site["id"]] = entry
        m2 = None
        if "m2" in entry["constituents"]:
            i = entry["constituents"].index("m2")
            m2 = entry["amplitude_m"][i]
        print(f"[INFO] {site['id']}: M2={m2} m  constituents={len(entry['constituents'])}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(table, indent=2) + "\n")
    print(f"[INFO] Wrote {args.output}")


if __name__ == "__main__":
    main()
