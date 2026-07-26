"""Land-masked sea-grid construction for dynamic Route Forecast."""

from __future__ import annotations

from plotter.core.config_loader import get_sites
from plotter.core.route_graph import build_graph, validate_graph
from plotter.core.sea_grid import (
    DEFAULT_AOI,
    build_sea_grid_network,
    is_sea,
    _edge_stays_at_sea,
)


def test_sea_points_are_wet_and_bangkok_is_land():
    assert is_sea(10.0, 110.0)  # open SCS
    assert not is_sea(13.75, 100.5)  # Bangkok / central Thailand


def test_build_sea_grid_connects_all_ports_without_land_edges():
    sites = get_sites()
    network = build_sea_grid_network(ports=sites, aoi=DEFAULT_AOI, step_deg=0.75)
    graph = build_graph(
        ports=sites,
        waypoints=network["waypoints"],
        edges=network["edges"],
        source="grid",
    )
    assert validate_graph(graph) == []
    nodes = {n["id"]: n for n in graph["nodes"]}
    for edge in graph["edges"]:
        a, b = nodes[edge["from"]], nodes[edge["to"]]
        # Port approaches may start on land (river ports); skip those.
        if a["type"] == "port" or b["type"] == "port":
            continue
        assert _edge_stays_at_sea(a["lat"], a["lon"], b["lat"], b["lon"], DEFAULT_AOI)
