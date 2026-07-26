"""Lane graph geometry and validation for Route Forecast.

The graph is static: nodes are ports (from ``sites:``) plus lane waypoints, and
edges are navigable segments between them. Only the along-lane forecast sampling
refreshes each cycle, so this module has no dependency on model data.
"""

from __future__ import annotations

import json
import math
from collections import deque
from pathlib import Path
from typing import Iterable, Optional

GRAPH_PATH = Path(__file__).resolve().parents[1] / "data" / "route_graph.json"

EARTH_RADIUS_NM = 3440.065


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(min(1.0, math.sqrt(a)))


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, degrees true."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlam)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def interpolate_great_circle(
    lat1: float, lon1: float, lat2: float, lon2: float, fraction: float
) -> tuple[float, float]:
    """Point at ``fraction`` along the great circle between two coordinates."""
    p1, l1 = math.radians(lat1), math.radians(lon1)
    p2, l2 = math.radians(lat2), math.radians(lon2)
    d = haversine_nm(lat1, lon1, lat2, lon2) / EARTH_RADIUS_NM
    if d < 1e-9:
        return lat1, lon1
    a = math.sin((1 - fraction) * d) / math.sin(d)
    b = math.sin(fraction * d) / math.sin(d)
    x = a * math.cos(p1) * math.cos(l1) + b * math.cos(p2) * math.cos(l2)
    y = a * math.cos(p1) * math.sin(l1) + b * math.cos(p2) * math.sin(l2)
    z = a * math.sin(p1) + b * math.sin(p2)
    return (
        math.degrees(math.atan2(z, math.hypot(x, y))),
        math.degrees(math.atan2(y, x)),
    )


def densify_edge(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    max_step_nm: float = 40.0,
    max_samples: int = 12,
) -> list[tuple[float, float]]:
    """Return sample points along an edge, spaced at most ``max_step_nm`` apart.

    Points are segment midpoints so a sample never lands exactly on a port
    centroid (which is often a land-masked grid cell).
    """
    distance = haversine_nm(lat1, lon1, lat2, lon2)
    if distance <= 0:
        return [(lat1, lon1)]
    n = max(1, min(max_samples, math.ceil(distance / max(1.0, max_step_nm))))
    return [
        interpolate_great_circle(lat1, lon1, lat2, lon2, (i + 0.5) / n)
        for i in range(n)
    ]


def build_graph(
    ports: Iterable[dict],
    waypoints: dict[str, dict],
    edges: Iterable[tuple[str, str]],
    source: str = "manual",
    generated_at: Optional[str] = None,
) -> dict:
    """Assemble the route graph document from ports, waypoints, and edges.

    Raises ``ValueError`` when an edge references an unknown node.
    """
    nodes: dict[str, dict] = {}
    port_ids = []
    for port in ports:
        nodes[port["id"]] = {
            "id": port["id"],
            "name": port.get("name") or port["id"],
            "lat": float(port["lat"]),
            "lon": float(port["lon"]),
            "type": "port",
        }
        port_ids.append(port["id"])

    for wp_id, meta in waypoints.items():
        if wp_id in nodes:
            raise ValueError(f"Waypoint id collides with a port id: {wp_id}")
        nodes[wp_id] = {
            "id": wp_id,
            "name": wp_id,
            "lat": float(meta["lat"]),
            "lon": float(meta["lon"]),
            "type": "waypoint",
        }

    built_edges = []
    seen: set[tuple[str, str]] = set()
    for a, b in edges:
        if a not in nodes or b not in nodes:
            missing = a if a not in nodes else b
            raise ValueError(f"Edge ({a}, {b}) references unknown node: {missing}")
        if a == b:
            continue
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        built_edges.append(
            {
                "id": f"{key[0]}__{key[1]}",
                "from": key[0],
                "to": key[1],
                "distance_nm": round(
                    haversine_nm(
                        nodes[key[0]]["lat"],
                        nodes[key[0]]["lon"],
                        nodes[key[1]]["lat"],
                        nodes[key[1]]["lon"],
                    ),
                    2,
                ),
            }
        )

    doc = {
        "source": source,
        "note": "Common vessel lanes for Route Forecast. Advisory only.",
        "ports": port_ids,
        "nodes": [nodes[k] for k in sorted(nodes)],
        "edges": sorted(built_edges, key=lambda e: e["id"]),
    }
    if generated_at:
        doc["generated_at"] = generated_at
    return doc


def adjacency(graph: dict) -> dict[str, list[str]]:
    """Undirected neighbour map keyed by node id."""
    adj: dict[str, list[str]] = {n["id"]: [] for n in graph.get("nodes", [])}
    for edge in graph.get("edges", []):
        a, b = edge["from"], edge["to"]
        if a in adj and b in adj:
            adj[a].append(b)
            adj[b].append(a)
    return adj


def unreachable_port_pairs(graph: dict) -> list[tuple[str, str]]:
    """Port pairs with no path through the lane network."""
    adj = adjacency(graph)
    ports = [p for p in graph.get("ports", []) if p in adj]
    bad = []
    for i, origin in enumerate(ports):
        reached = {origin}
        queue = deque([origin])
        while queue:
            node = queue.popleft()
            for nxt in adj[node]:
                if nxt not in reached:
                    reached.add(nxt)
                    queue.append(nxt)
        for dest in ports[i + 1:]:
            if dest not in reached:
                bad.append((origin, dest))
    return bad


def validate_graph(graph: dict) -> list[str]:
    """Return human-readable problems; empty list means the graph is usable."""
    problems = []
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    if not nodes:
        problems.append("graph has no nodes")
    if not graph.get("edges"):
        problems.append("graph has no edges")

    for port in graph.get("ports", []):
        if port not in nodes:
            problems.append(f"port {port} is missing from nodes")

    adj = adjacency(graph)
    for node_id, neighbours in adj.items():
        if not neighbours:
            problems.append(f"node {node_id} has no edges")

    for origin, dest in unreachable_port_pairs(graph):
        problems.append(f"no lane path between {origin} and {dest}")
    return problems


def load_route_graph(path: Path | None = None) -> dict:
    """Read the committed lane graph."""
    return json.loads((Path(path) if path else GRAPH_PATH).read_text())
