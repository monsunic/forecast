"""Land-masked navigable sea grid for dynamic Route Forecast A*.

Unlike hand-digitized corridors, this builds an 8-connected lattice of open-
water cells over the SEA AOI. Edges that cross Natural Earth land are rejected,
so the browser A* can pick weather-aware tracks without sailing over Thailand
or Borneo. Ports snap to the nearest sea cells.
"""

from __future__ import annotations

import math
from collections import deque
from functools import lru_cache
from typing import Iterable, Optional

from shapely.geometry import Point, box
from shapely.ops import unary_union
from shapely.prepared import prep

from .route_graph import haversine_nm, interpolate_great_circle

# SEA routing domain (matches map / site coverage).
DEFAULT_AOI = (-11.0, 95.0, 24.0, 128.0)  # lat_min, lon_min, lat_max, lon_max
DEFAULT_STEP_DEG = 0.5
PORT_SNAP_NM = 90.0
PORT_SNAP_NEIGHBOURS = 3
# How many great-circle probes must stay wet for an edge to be kept.
EDGE_SEA_SAMPLES = 3


@lru_cache(maxsize=2)
def _prepared_land(aoi: tuple[float, float, float, float]):
    """Natural Earth 50m land clipped to the AOI, prepared for fast contains."""
    from cartopy.io import shapereader

    lat_min, lon_min, lat_max, lon_max = aoi
    # Slight pad so coastal cells near the AOI rim still see land.
    clip = box(lon_min - 1.0, lat_min - 1.0, lon_max + 1.0, lat_max + 1.0)
    shp = shapereader.natural_earth(
        resolution="50m", category="physical", name="land"
    )
    pieces = []
    for geom in shapereader.Reader(shp).geometries():
        if geom.is_empty or not geom.intersects(clip):
            continue
        part = geom.intersection(clip)
        if not part.is_empty:
            pieces.append(part)
    if not pieces:
        raise RuntimeError("Natural Earth land mask is empty for the route AOI")
    return prep(unary_union(pieces))


def is_sea(lat: float, lon: float, aoi: tuple[float, float, float, float] = DEFAULT_AOI) -> bool:
    """True when the point is outside the land polygon (open water / lake)."""
    return not _prepared_land(aoi).contains(Point(float(lon), float(lat)))


def _edge_stays_at_sea(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    aoi: tuple[float, float, float, float],
    samples: int = EDGE_SEA_SAMPLES,
) -> bool:
    """Reject legs whose great-circle probes hit land (peninsula cutters)."""
    land = _prepared_land(aoi)
    for i in range(samples):
        frac = (i + 1) / (samples + 1)
        lat, lon = interpolate_great_circle(lat1, lon1, lat2, lon2, frac)
        if land.contains(Point(lon, lat)):
            return False
    return True


def _grid_id(ilat: int, ilon: int) -> str:
    return f"g_{ilat}_{ilon}"


def build_sea_grid_network(
    ports: Iterable[dict],
    aoi: tuple[float, float, float, float] = DEFAULT_AOI,
    step_deg: float = DEFAULT_STEP_DEG,
    port_snap_nm: float = PORT_SNAP_NM,
    port_neighbours: int = PORT_SNAP_NEIGHBOURS,
) -> dict:
    """Return ``{waypoints, edges}`` for ``build_graph``.

    Waypoints are wet lattice cells. Each port is linked to up to
    ``port_neighbours`` nearest sea cells within ``port_snap_nm``.
    """
    lat_min, lon_min, lat_max, lon_max = aoi
    step = float(step_deg)
    if step <= 0:
        raise ValueError("step_deg must be positive")

    n_lat = int(math.floor((lat_max - lat_min) / step)) + 1
    n_lon = int(math.floor((lon_max - lon_min) / step)) + 1

    sea_cells: dict[tuple[int, int], tuple[float, float]] = {}
    for ilat in range(n_lat):
        lat = lat_min + ilat * step
        for ilon in range(n_lon):
            lon = lon_min + ilon * step
            if is_sea(lat, lon, aoi):
                sea_cells[(ilat, ilon)] = (lat, lon)

    # 8-connected wet neighbours whose segment stays at sea.
    deltas = [
        (1, 0),
        (-1, 0),
        (0, 1),
        (0, -1),
        (1, 1),
        (1, -1),
        (-1, 1),
        (-1, -1),
    ]
    edge_set: set[tuple[str, str]] = set()
    for (ilat, ilon), (lat, lon) in sea_cells.items():
        a = _grid_id(ilat, ilon)
        for dlat, dlon in deltas:
            key = (ilat + dlat, ilon + dlon)
            if key not in sea_cells:
                continue
            # Emit each undirected edge once.
            if (dlat, dlon) not in ((1, 0), (0, 1), (1, 1), (1, -1)):
                continue
            lat2, lon2 = sea_cells[key]
            if not _edge_stays_at_sea(lat, lon, lat2, lon2, aoi):
                continue
            b = _grid_id(*key)
            edge_set.add(tuple(sorted((a, b))))

    waypoints = {
        _grid_id(ilat, ilon): {"lat": lat, "lon": lon}
        for (ilat, ilon), (lat, lon) in sea_cells.items()
    }

    # Snap each port to nearest sea cells (ports themselves are added by build_graph).
    port_list = list(ports)
    for port in port_list:
        plat, plon = float(port["lat"]), float(port["lon"])
        ranked = sorted(
            (
                (haversine_nm(plat, plon, lat, lon), _grid_id(ilat, ilon))
                for (ilat, ilon), (lat, lon) in sea_cells.items()
            ),
            key=lambda item: item[0],
        )
        linked = 0
        for dist_nm, gid in ranked:
            if dist_nm > port_snap_nm:
                break
            # Port→sea approach must stay wet when the port itself is offshore;
            # for inland river ports, only require the sea endpoint to be wet.
            glat, glon = waypoints[gid]["lat"], waypoints[gid]["lon"]
            if is_sea(plat, plon, aoi) and not _edge_stays_at_sea(
                plat, plon, glat, glon, aoi
            ):
                continue
            edge_set.add(tuple(sorted((port["id"], gid))))
            linked += 1
            if linked >= port_neighbours:
                break
        if linked == 0:
            raise RuntimeError(
                f"Port {port['id']} has no sea-grid cell within {port_snap_nm} nm"
            )

    # Keep only the component that contains every port (drop isolated lakes).
    adj: dict[str, list[str]] = {}
    for a, b in edge_set:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)

    port_ids = [p["id"] for p in port_list]
    seed = port_ids[0]
    reached = {seed}
    queue = deque([seed])
    while queue:
        node = queue.popleft()
        for nxt in adj.get(node, []):
            if nxt not in reached:
                reached.add(nxt)
                queue.append(nxt)

    missing = [pid for pid in port_ids if pid not in reached]
    if missing:
        raise RuntimeError(
            "Sea grid does not connect all ports; unreachable: " + ", ".join(missing)
        )

    waypoints = {wid: meta for wid, meta in waypoints.items() if wid in reached}
    edges = [(a, b) for a, b in sorted(edge_set) if a in reached and b in reached]

    return {
        "waypoints": waypoints,
        "edges": edges,
        "meta": {
            "aoi": aoi,
            "step_deg": step,
            "sea_cells": len(waypoints),
            "edges": len(edges),
        },
    }
