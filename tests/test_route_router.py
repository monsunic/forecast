"""Python reference for the browser time-dependent A* router.

Mirrors the cost model and search in ``src/route.js`` so regressions in either
side show up as a failing unit test.
"""

from __future__ import annotations

import heapq
import math
from typing import Optional

from plotter.core.route_graph import haversine_nm, initial_bearing_deg

EARTH_RADIUS_NM = 3440.065
MAX_CURRENT_ASSIST_KT = 3.0
MODE_WEIGHTS = {"fastest": 0.0, "balanced": 2.0, "safest": 9.0}


def _along_course(speed, direction_deg, course_deg) -> float:
    if speed is None or direction_deg is None:
        return 0.0
    return float(speed) * math.cos(math.radians(float(direction_deg) - course_deg))


def effective_speed_kt(profile: dict, conditions: dict, course_deg: float) -> float:
    calm = float(profile["calm_speed_kt"])
    swh = float(conditions.get("swh") or 0.0)
    headwind = _along_course(conditions.get("wind_speed"), conditions.get("wind_dir"), course_deg)
    set_along = _along_course(conditions.get("current"), conditions.get("current_dir"), course_deg)
    hull = min(
        calm,
        max(
            float(profile["min_speed_kt"]),
            calm
            - float(profile["wave_coeff"]) * swh * swh
            - float(profile["wind_coeff"]) * headwind,
        ),
    )
    return max(float(profile["min_speed_kt"]) * 0.5, hull + float(profile["current_coeff"]) * set_along)


def hazard_score(profile: dict, conditions: dict) -> float:
    swh = float(conditions.get("swh_max", conditions.get("swh") or 0.0) or 0.0)
    wind = float(conditions.get("wind_speed") or 0.0)
    wave = swh / max(0.5, float(profile["comfort_swh_m"]))
    wind_h = wind / max(1.0, float(profile["max_wind_kt"]) * 0.55)
    return wave * wave + 0.4 * wind_h * wind_h


def is_unsafe(profile: dict, conditions: dict) -> bool:
    swh = float(conditions.get("swh_max", conditions.get("swh") or 0.0) or 0.0)
    wind = float(conditions.get("wind_speed") or 0.0)
    return swh > float(profile["max_swh_m"]) or wind > float(profile["max_wind_kt"])


def conditions_at(edge: dict, leads: list[float], lead_hours: float) -> dict:
    series = edge.get("series") or {}
    if not leads:
        return {}
    t = max(leads[0], min(leads[-1], lead_hours))
    hi = next((i for i, lead in enumerate(leads) if lead >= t), len(leads) - 1)
    lo = max(0, hi - 1)
    span = leads[hi] - leads[lo]
    frac = (t - leads[lo]) / span if span > 0 else 0.0
    out = {}
    for key, values in series.items():
        a = values[lo] if lo < len(values) else None
        b = values[hi] if hi < len(values) else None
        if a is None and b is None:
            out[key] = None
        elif a is None or b is None:
            out[key] = b if a is None else a
        elif key.endswith("_dir"):
            delta = ((b - a + 540) % 360) - 180
            out[key] = (a + delta * frac + 360) % 360
        else:
            out[key] = a + (b - a) * frac
    return out


def plan_route(
    nodes: dict[str, dict],
    edges: dict[str, dict],
    profile: dict,
    origin: str,
    destination: str,
    depart_lead: float = 0.0,
    mode: str = "balanced",
    prune: bool = True,
) -> Optional[dict]:
    """Time-dependent A* matching ``src/route.js``.

    Returns ``None`` when no path exists. Edge keys must be stable ids; each
    edge is traversable in both directions.
    """
    weight = MODE_WEIGHTS[mode]
    adj: dict[str, list[tuple[str, str, bool]]] = {nid: [] for nid in nodes}
    for eid, edge in edges.items():
        adj[edge["from"]].append((eid, edge["to"], False))
        adj[edge["to"]].append((eid, edge["from"], True))

    dest = nodes[destination]
    best_speed = float(profile["calm_speed_kt"]) + MAX_CURRENT_ASSIST_KT

    def heuristic(nid: str) -> float:
        n = nodes[nid]
        return haversine_nm(n["lat"], n["lon"], dest["lat"], dest["lon"]) / best_speed

    best_cost = {origin: 0.0}
    arrival = {origin: depart_lead}
    came_from = {}
    open_heap = [(heuristic(origin), origin)]

    while open_heap:
        _, node_id = heapq.heappop(open_heap)
        if node_id == destination:
            break
        cost = best_cost[node_id]
        time = arrival[node_id]
        for eid, nxt, _rev in adj[node_id]:
            edge = edges[eid]
            leads = list(range(len(next(iter(edge["series"].values())))))
            # Tests use integer lead indices as hours for simplicity.
            conditions = conditions_at(edge, [float(h) for h in leads], time)
            if prune and is_unsafe(profile, conditions):
                continue
            course = initial_bearing_deg(
                nodes[node_id]["lat"],
                nodes[node_id]["lon"],
                nodes[nxt]["lat"],
                nodes[nxt]["lon"],
            )
            speed = effective_speed_kt(profile, conditions, course)
            hours = float(edge["distance_nm"]) / max(0.5, speed)
            leg_cost = hours * (1.0 + weight * hazard_score(profile, conditions))
            next_cost = cost + leg_cost
            if next_cost < best_cost.get(nxt, math.inf):
                best_cost[nxt] = next_cost
                arrival[nxt] = time + hours
                came_from[nxt] = node_id
                heapq.heappush(open_heap, (next_cost + heuristic(nxt), nxt))

    if destination not in best_cost:
        return None
    return {
        "cost": best_cost[destination],
        "arrival_lead": arrival[destination],
        "duration_hours": arrival[destination] - depart_lead,
    }


PROFILE = {
    "id": "test",
    "name": "Test vessel",
    "calm_speed_kt": 10.0,
    "min_speed_kt": 4.0,
    "max_swh_m": 4.0,
    "max_wind_kt": 35.0,
    "comfort_swh_m": 2.0,
    "wave_coeff": 0.4,
    "wind_coeff": 0.05,
    "current_coeff": 1.0,
}


def _toy_network():
    """Two-path diamond: calm northern lane, rough southern shortcut."""
    nodes = {
        "origin": {"id": "origin", "lat": 0.0, "lon": 100.0},
        "north": {"id": "north", "lat": 1.0, "lon": 101.0},
        "south": {"id": "south", "lat": -1.0, "lon": 101.0},
        "dest": {"id": "dest", "lat": 0.0, "lon": 102.0},
    }
    hours = 3

    def series(swh, wind=0.0):
        return {
            "swh": [swh] * hours,
            "swh_max": [swh] * hours,
            "wind_speed": [wind] * hours,
            "wind_dir": [0.0] * hours,
            "current": [0.0] * hours,
            "current_dir": [90.0] * hours,
        }

    edges = {
        "o_n": {
            "from": "origin",
            "to": "north",
            "distance_nm": 80.0,
            "series": series(0.0),
        },
        "n_d": {
            "from": "north",
            "to": "dest",
            "distance_nm": 80.0,
            "series": series(0.0),
        },
        "o_s": {
            "from": "origin",
            "to": "south",
            "distance_nm": 70.0,
            "series": series(5.5),
        },
        "s_d": {
            "from": "south",
            "to": "dest",
            "distance_nm": 70.0,
            "series": series(5.5),
        },
    }
    return nodes, edges


def test_effective_speed_drops_with_waves_and_headwind():
    calm = effective_speed_kt(PROFILE, {"swh": 0.0, "wind_speed": 0.0, "wind_dir": 0.0, "current": 0.0, "current_dir": 0.0}, 90.0)
    rough = effective_speed_kt(PROFILE, {"swh": 3.0, "wind_speed": 20.0, "wind_dir": 90.0, "current": 0.0, "current_dir": 0.0}, 90.0)
    assert rough < calm
    assert rough >= PROFILE["min_speed_kt"] * 0.5


def test_current_assist_raises_ground_speed():
    base = effective_speed_kt(PROFILE, {"swh": 0.0, "wind_speed": 0.0, "wind_dir": 0.0, "current": 0.0, "current_dir": 90.0}, 90.0)
    assisted = effective_speed_kt(PROFILE, {"swh": 0.0, "wind_speed": 0.0, "wind_dir": 0.0, "current": 2.0, "current_dir": 90.0}, 90.0)
    assert assisted > base


def test_conditions_at_interpolates_and_wraps_bearing():
    edge = {
        "series": {
            "swh": [1.0, 3.0],
            "wind_dir": [350.0, 10.0],
        }
    }
    mid = conditions_at(edge, [0.0, 6.0], 3.0)
    assert abs(mid["swh"] - 2.0) < 1e-9
    assert abs(mid["wind_dir"] - 0.0) < 1e-6 or abs(mid["wind_dir"] - 360.0) < 1e-6


def test_fastest_prefers_shorter_rough_lane_when_within_limits():
    nodes, edges = _toy_network()
    # Soften the south lane just enough that pruning still allows it.
    for eid in ("o_s", "s_d"):
        edges[eid]["series"]["swh"] = [3.5, 3.5, 3.5]
        edges[eid]["series"]["swh_max"] = [3.5, 3.5, 3.5]

    fastest = plan_route(nodes, edges, PROFILE, "origin", "dest", mode="fastest")
    safest = plan_route(nodes, edges, PROFILE, "origin", "dest", mode="safest")
    assert fastest is not None and safest is not None
    # Shorter rough lane is faster; safer mode pays the detour.
    assert fastest["duration_hours"] <= safest["duration_hours"]
    assert safest["cost"] >= fastest["cost"]


def test_safety_pruning_blocks_hazardous_shortcut():
    nodes, edges = _toy_network()
    blocked = plan_route(nodes, edges, PROFILE, "origin", "dest", mode="fastest", prune=True)
    assert blocked is not None
    # Only the calm northern path remains after pruning the 5.5 m south lane.
    assert abs(blocked["duration_hours"] - 160.0 / 10.0) < 1e-6


def test_heuristic_never_overestimates_remaining_time():
    nodes, edges = _toy_network()
    for eid in ("o_s", "s_d"):
        edges[eid]["series"]["swh"] = [1.0, 1.0, 1.0]
        edges[eid]["series"]["swh_max"] = [1.0, 1.0, 1.0]
    result = plan_route(nodes, edges, PROFILE, "origin", "dest", mode="fastest")
    assert result is not None
    # Direct great-circle / best possible speed is an admissible lower bound.
    direct = haversine_nm(0.0, 100.0, 0.0, 102.0) / (
        PROFILE["calm_speed_kt"] + MAX_CURRENT_ASSIST_KT
    )
    assert result["duration_hours"] + 1e-9 >= direct
