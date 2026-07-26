#!/usr/bin/env python3
"""Generate lightweight static frontend assets."""

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib.pyplot as plt
import cartopy.io.shapereader as shpreader
from shapely.geometry import box, mapping

ICONS_DIR = ROOT / "assets" / "icons"
MAPS_DIR = ROOT / "assets" / "maps"


def generate_logo():
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    out = ICONS_DIR / "nusawave-logo.png"

    fig, ax = plt.subplots(figsize=(2, 2), dpi=100)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.add_patch(plt.Circle((0.5, 0.5), 0.42, color="#1a6fb5", zorder=1))
    ax.text(
        0.5, 0.5, "NW",
        ha="center", va="center",
        fontsize=28, fontweight="bold", color="white",
        fontfamily="sans-serif",
    )
    plt.savefig(out, format="png", bbox_inches="tight", pad_inches=0.05, transparent=True)
    plt.close(fig)
    print(f"[INFO] Wrote {out}")


def generate_site_countries_geojson():
    """Export clipped country polygons for the tile-free interactive site map."""
    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    out = MAPS_DIR / "site_countries.geojson"
    # Matches SITE_MAP_BOUNDS in src/main.js: [[-11, 95], [24, 128]]
    bounds = box(95, -11, 128, 24)
    shp = shpreader.natural_earth(
        resolution="50m",
        category="cultural",
        name="admin_0_countries",
    )
    features = []
    for record in shpreader.Reader(shp).records():
        geom = record.geometry
        if geom is None or not geom.intersects(bounds):
            continue
        clipped = geom.intersection(bounds)
        if clipped.is_empty:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "name": record.attributes.get("NAME")
                    or record.attributes.get("ADMIN")
                    or "",
                },
                "geometry": mapping(clipped),
            }
        )

    out.write_text(
        json.dumps(
            {"type": "FeatureCollection", "features": features},
            separators=(",", ":"),
        )
        + "\n"
    )
    print(f"[INFO] Wrote {out}")


if __name__ == "__main__":
    generate_logo()
    generate_site_countries_geojson()
