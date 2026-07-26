"""Derive vessel lane centerlines from historical AIS positions.

Offline helper for ``scripts/build_route_graph.py --source ais|auto``. Reads the
public ``AISDataPortal/DB`` daily parquet snapshots from Hugging Face, keeps
commercial traffic underway inside the AOI, and reduces the resulting traffic
density into a connected lane graph.

Nothing here runs in the per-cycle pipeline, and every failure path returns
``None`` so the caller can fall back to the manual corridors in config.yaml.
"""

from __future__ import annotations

import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from plotter.core.route_graph import haversine_nm

HF_DATASET = "AISDataPortal/DB"
HF_API = f"https://huggingface.co/api/datasets/{HF_DATASET}"
HF_FILE = f"https://huggingface.co/datasets/{HF_DATASET}/resolve/main/"

CACHE_DIR = Path(
    os.environ.get("NW_AIS_CACHE")
    or Path(__file__).resolve().parents[2] / ".ais-cache"
)

# Cargo (70-79), tanker (80-89), and passenger (60-69) AIS ship types: the
# classes whose routing behaviour the Route Forecast is meant to represent.
COMMERCIAL_TYPE_RANGE = (60.0, 90.0)
MIN_SOG_KT = 3.0
MAX_SOG_KT = 30.0

GRID_DEG = 0.25
MIN_CELL_COUNT = 8
# Lane nodes are thinned onto a coarser lattice (~60 nm spacing) so a corridor
# keeps its shape instead of collapsing to one centroid.
LANE_NODE_DEG = 1.0
MAX_LINK_NM = 110.0
PORT_SNAP_NM = 80.0

_COLUMNS = ["LATITUDE", "LONGITUDE", "SOG", "TYPE"]


def _log(msg: str) -> None:
    print(f"[INFO] ais_lanes: {msg}", file=sys.stderr)


def list_remote_days(limit: int) -> list[str]:
    """Return the most recent daily parquet filenames, newest first."""
    with urllib.request.urlopen(HF_API, timeout=60) as resp:
        meta = json.load(resp)
    names = [
        s["rfilename"]
        for s in meta.get("siblings", [])
        if s.get("rfilename", "").endswith("_ais.parquet")
    ]
    return sorted(names, reverse=True)[: max(1, limit)]


def _download(name: str, cache_dir: Path) -> Optional[Path]:
    dest = cache_dir / name
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        _log(f"downloading {name}")
        urllib.request.urlretrieve(HF_FILE + name, tmp)  # noqa: S310 - fixed host
    except (urllib.error.URLError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        _log(f"download failed for {name}: {exc}")
        return None
    tmp.replace(dest)
    return dest


def harvest_positions(
    aoi: tuple[float, float, float, float],
    days: int,
    max_positions: int,
    cache_dir: Path | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (lat, lon) arrays of commercial traffic underway inside the AOI."""
    import pyarrow.parquet as pq

    lat_min, lon_min, lat_max, lon_max = aoi
    cache = cache_dir or CACHE_DIR
    lats: list[np.ndarray] = []
    lons: list[np.ndarray] = []
    total = 0

    for name in list_remote_days(days):
        path = _download(name, cache)
        if path is None:
            continue
        try:
            pf = pq.ParquetFile(path)
            for batch in pf.iter_batches(columns=_COLUMNS, batch_size=500_000):
                lat = np.asarray(batch.column("LATITUDE"), dtype="float64")
                lon = np.asarray(batch.column("LONGITUDE"), dtype="float64")
                sog = np.asarray(batch.column("SOG"), dtype="float64")
                typ = np.asarray(batch.column("TYPE"), dtype="float64")
                keep = (
                    (lat >= lat_min)
                    & (lat <= lat_max)
                    & (lon >= lon_min)
                    & (lon <= lon_max)
                    & (sog >= MIN_SOG_KT)
                    & (sog <= MAX_SOG_KT)
                    & (typ >= COMMERCIAL_TYPE_RANGE[0])
                    & (typ < COMMERCIAL_TYPE_RANGE[1])
                )
                if not keep.any():
                    continue
                lats.append(lat[keep])
                lons.append(lon[keep])
                total += int(keep.sum())
                if total >= max_positions:
                    break
        except (OSError, ValueError) as exc:
            _log(f"unreadable parquet {name}: {exc}")
            continue
        _log(f"{name}: {total} AOI positions so far")
        if total >= max_positions:
            break

    if not lats:
        return np.empty(0), np.empty(0)
    return np.concatenate(lats)[:max_positions], np.concatenate(lons)[:max_positions]


def traffic_cells(
    lat: np.ndarray,
    lon: np.ndarray,
    grid_deg: float = GRID_DEG,
    min_count: int = MIN_CELL_COUNT,
) -> dict[tuple[int, int], int]:
    """Bin positions onto a regular grid and keep well-travelled cells."""
    if lat.size == 0:
        return {}
    keys_y = np.floor(lat / grid_deg).astype(np.int64)
    keys_x = np.floor(lon / grid_deg).astype(np.int64)
    stacked = np.stack([keys_y, keys_x], axis=1)
    uniq, counts = np.unique(stacked, axis=0, return_counts=True)
    return {
        (int(row[0]), int(row[1])): int(count)
        for row, count in zip(uniq, counts)
        if count >= min_count
    }


def _cell_center(key: tuple[int, int], grid_deg: float) -> tuple[float, float]:
    return (key[0] + 0.5) * grid_deg, (key[1] + 0.5) * grid_deg


def cluster_lane_nodes(
    cells: dict[tuple[int, int], int],
    grid_deg: float = GRID_DEG,
    node_deg: float = LANE_NODE_DEG,
) -> list[tuple[float, float]]:
    """Reduce travelled cells to lane node coordinates.

    HDBSCAN groups travelled cells into coherent corridors and discards noise
    (one-off transits, drifting fishing traffic). Each corridor is then thinned
    onto a coarser lattice rather than averaged away, so a long lane such as the
    Malacca Strait yields a chain of nodes instead of one mid-strait point.

    Raises ``ImportError`` when scikit-learn is absent so the caller can fall
    back to the manual corridors.
    """
    from sklearn.cluster import HDBSCAN

    centers = np.array([_cell_center(k, grid_deg) for k in cells], dtype="float64")
    weights = np.array([cells[k] for k in cells], dtype="float64")
    if centers.shape[0] < 10:
        return []

    labels = HDBSCAN(
        min_cluster_size=4, min_samples=2, cluster_selection_epsilon=0.6, copy=True
    ).fit_predict(centers)
    keep = labels >= 0
    if int(keep.sum()) < 10:
        return []

    buckets: dict[tuple[int, int, int], list[float]] = {}
    for (lat, lon), weight, label in zip(centers[keep], weights[keep], labels[keep]):
        key = (
            int(label),
            int(math.floor(lat / node_deg)),
            int(math.floor(lon / node_deg)),
        )
        acc = buckets.setdefault(key, [0.0, 0.0, 0.0])
        acc[0] += lat * weight
        acc[1] += lon * weight
        acc[2] += weight
    return [
        (round(acc[0] / acc[2], 4), round(acc[1] / acc[2], 4))
        for acc in buckets.values()
        if acc[2] > 0
    ]


def _has_clear_lane(
    a: tuple[float, float],
    b: tuple[float, float],
    cells: set[tuple[int, int]],
    grid_deg: float,
) -> bool:
    """True when every step between two nodes lands on a travelled cell.

    This is the AIS stand-in for a landmask: vessels do not report positions
    over land, so an unbroken chain of travelled cells implies navigable water.
    """
    steps = max(
        2, int(math.hypot(b[0] - a[0], b[1] - a[1]) / (grid_deg * 0.5))
    )
    for i in range(steps + 1):
        f = i / steps
        lat = a[0] + (b[0] - a[0]) * f
        lon = a[1] + (b[1] - a[1]) * f
        key = (int(math.floor(lat / grid_deg)), int(math.floor(lon / grid_deg)))
        if key in cells:
            continue
        # Tolerate one-cell gaps from sparse snapshot reporting.
        if any(
            (key[0] + dy, key[1] + dx) in cells
            for dy in (-1, 0, 1)
            for dx in (-1, 0, 1)
        ):
            continue
        return False
    return True


def prune_to_main_component(
    waypoints: dict[str, dict],
    edges: list[tuple[str, str]],
    port_ids: list[str],
) -> tuple[dict[str, dict], list[tuple[str, str]]]:
    """Drop lane nodes outside the component holding the most ports.

    Sparse reporting always leaves a tail of isolated traffic cells. They are
    noise rather than a coverage failure, so they are removed before the graph
    is judged on whether the ports can actually reach each other.
    """
    adj: dict[str, set[str]] = {}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    seen: set[str] = set()
    best: set[str] = set()
    best_ports = -1
    for start in adj:
        if start in seen:
            continue
        component = {start}
        stack = [start]
        while stack:
            node = stack.pop()
            for nxt in adj.get(node, ()):
                if nxt not in component:
                    component.add(nxt)
                    stack.append(nxt)
        seen |= component
        port_hits = sum(1 for p in port_ids if p in component)
        if port_hits > best_ports or (port_hits == best_ports and len(component) > len(best)):
            best, best_ports = component, port_hits

    kept_waypoints = {k: v for k, v in waypoints.items() if k in best}
    kept_edges = [(a, b) for a, b in edges if a in best and b in best]
    dropped = len(waypoints) - len(kept_waypoints)
    if dropped:
        _log(f"dropped {dropped} lane nodes outside the main traffic component")
    return kept_waypoints, kept_edges


def harvest_lane_network(
    ports: Iterable[dict],
    aoi: tuple[float, float, float, float],
    days: int = 30,
    max_positions: int = 400000,
    grid_deg: float = GRID_DEG,
    min_cell_count: int | None = None,
    cache_dir: Path | None = None,
) -> Optional[dict]:
    """Build ``{"waypoints", "edges"}`` from AIS traffic, or None if unusable."""
    ports = list(ports)
    lat, lon = harvest_positions(aoi, days, max_positions, cache_dir)
    if lat.size < 500:
        _log(f"only {lat.size} AOI positions harvested; not enough for lanes")
        return None

    cells = traffic_cells(
        lat, lon, grid_deg, min_cell_count if min_cell_count else MIN_CELL_COUNT
    )
    if len(cells) < 40:
        _log(f"only {len(cells)} travelled cells; coverage too sparse")
        return None
    _log(f"{lat.size} positions -> {len(cells)} travelled cells")

    nodes = cluster_lane_nodes(cells, grid_deg)
    if len(nodes) < 8:
        _log(f"clustering yielded {len(nodes)} lane nodes; too few")
        return None

    cell_set = set(cells)
    waypoints = {
        f"ais_{i:03d}": {"lat": round(la, 4), "lon": round(lo, 4)}
        for i, (la, lo) in enumerate(nodes)
    }
    ids = list(waypoints)

    edges: list[tuple[str, str]] = []
    for i, a_id in enumerate(ids):
        a = (waypoints[a_id]["lat"], waypoints[a_id]["lon"])
        for b_id in ids[i + 1:]:
            b = (waypoints[b_id]["lat"], waypoints[b_id]["lon"])
            if haversine_nm(a[0], a[1], b[0], b[1]) > MAX_LINK_NM:
                continue
            if _has_clear_lane(a, b, cell_set, grid_deg):
                edges.append((a_id, b_id))

    for port in ports:
        nearest = min(
            ids,
            key=lambda wid: haversine_nm(
                port["lat"], port["lon"], waypoints[wid]["lat"], waypoints[wid]["lon"]
            ),
        )
        gap = haversine_nm(
            port["lat"], port["lon"], waypoints[nearest]["lat"], waypoints[nearest]["lon"]
        )
        if gap > PORT_SNAP_NM:
            _log(f"port {port['id']} is {gap:.0f} nm from any lane node")
            return None
        edges.append((port["id"], nearest))

    waypoints, edges = prune_to_main_component(
        waypoints, edges, [p["id"] for p in ports]
    )
    _log(f"lane network: {len(waypoints)} waypoints, {len(edges)} edges")
    return {"waypoints": waypoints, "edges": edges}
