"""Lane graph schema and connectivity tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plotter.core.config_loader import get_route_corridors, get_sites
from plotter.core.route_graph import (
    GRAPH_PATH,
    adjacency,
    build_graph,
    densify_edge,
    haversine_nm,
    load_route_graph,
    unreachable_port_pairs,
    validate_graph,
)

ROOT = Path(__file__).resolve().parents[1]


def test_committed_graph_exists_and_validates():
    assert GRAPH_PATH.is_file()
    graph = load_route_graph()
    assert graph["source"] in ("manual", "ais", "grid")
    problems = validate_graph(graph)
    assert problems == [], problems


def test_committed_graph_covers_every_configured_port():
    sites = {s["id"] for s in get_sites()}
    graph = load_route_graph()
    assert set(graph["ports"]) == sites
    for node in graph["nodes"]:
        assert "lat" in node and "lon" in node
        assert node["type"] in ("port", "waypoint")
    for edge in graph["edges"]:
        assert edge["distance_nm"] > 0
        assert edge["from"] != edge["to"]
        assert "__" in edge["id"]


def test_every_port_pair_is_reachable():
    graph = load_route_graph()
    assert unreachable_port_pairs(graph) == []


def test_manual_corridors_rebuild_validates():
    sites = get_sites()
    corridors = get_route_corridors()
    rebuilt = build_graph(
        ports=sites,
        waypoints=corridors["waypoints"],
        edges=corridors["edges"],
        source="manual",
    )
    assert validate_graph(rebuilt) == []


def test_build_graph_rejects_unknown_endpoint():
    with pytest.raises(ValueError, match="unknown node"):
        build_graph(
            ports=[{"id": "a", "name": "A", "lat": 0.0, "lon": 100.0}],
            waypoints={},
            edges=[("a", "missing")],
        )


def test_densify_edge_spaces_samples_by_distance():
    points = densify_edge(0.0, 100.0, 0.0, 110.0, max_step_nm=120.0, max_samples=8)
    assert 4 <= len(points) <= 8
    # Midpoints never land exactly on the endpoints.
    assert points[0] != (0.0, 100.0)
    assert points[-1] != (0.0, 110.0)
    # First and last midpoints stay roughly one half-step from the ends.
    assert haversine_nm(0.0, 100.0, *points[0]) < 80
    assert haversine_nm(0.0, 110.0, *points[-1]) < 80


def test_adjacency_is_undirected():
    graph = {
        "nodes": [
            {"id": "a", "lat": 0, "lon": 0},
            {"id": "b", "lat": 1, "lon": 1},
        ],
        "edges": [{"from": "a", "to": "b", "distance_nm": 10}],
        "ports": [],
    }
    adj = adjacency(graph)
    assert adj["a"] == ["b"]
    assert adj["b"] == ["a"]


def _shortest_path(graph: dict, start: str, goal: str) -> list[str]:
    """Dijkstra on edge distance_nm; returns node id path inclusive of endpoints."""
    from heapq import heappop, heappush

    nodes = {n["id"]: n for n in graph["nodes"]}
    edge_dist = {}
    for e in graph["edges"]:
        edge_dist[(e["from"], e["to"])] = e["distance_nm"]
        edge_dist[(e["to"], e["from"])] = e["distance_nm"]
    adj = adjacency(graph)

    dist = {start: 0.0}
    prev: dict[str, str | None] = {start: None}
    heap = [(0.0, start)]
    while heap:
        d, u = heappop(heap)
        if u == goal:
            break
        if d > dist.get(u, float("inf")):
            continue
        for v in adj.get(u, []):
            nd = d + edge_dist[(u, v)]
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = u
                heappush(heap, (nd, v))
    if goal not in prev:
        pytest.fail(f"no path {start} → {goal}")
    path = []
    cur: str | None = goal
    while cur is not None:
        path.append(cur)
        cur = prev[cur]
    path.reverse()
    assert path[0] == start and path[-1] == goal
    assert nodes[start] and nodes[goal]
    return path


def test_laem_chabang_to_manila_prefers_scs_not_singapore():
    """Gulf of Thailand → Manila must cross the South China Sea.

    On the sea-grid graph the distance-optimal track stays offshore through
    the SCS rather than detouring south past Singapore.
    """
    graph = load_route_graph()
    path = _shortest_path(graph, "laem_chabang", "manila")
    assert "singapore" not in path
    # Path should go east of ~105E toward Manila, not south of 3N past Tioman.
    nodes = {n["id"]: n for n in graph["nodes"]}
    lats = [nodes[nid]["lat"] for nid in path if nid in nodes]
    lons = [nodes[nid]["lon"] for nid in path if nid in nodes]
    assert max(lons) > 115.0
    assert min(lats) > 5.0


def test_route_graph_json_is_valid_json():
    raw = GRAPH_PATH.read_text()
    doc = json.loads(raw)
    assert doc["source"] in ("manual", "ais", "grid")
    assert len(doc["edges"]) >= len(doc["ports"]) - 1
