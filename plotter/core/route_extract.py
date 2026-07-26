"""Along-lane forecast sampling for Route Forecast.

Each lane edge is resampled into segment midpoints; the model conditions at
those points are aggregated into one value per edge per forecast hour. The
browser-side router reads that compact table instead of raw model fields.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Optional

from .route_graph import densify_edge

# cm/s → kt (site extraction publishes current in cm/s; the router works in kt)
CURRENT_KT_SCALE = 1.0 / 51.444444

EDGE_MAX_STEP_NM = 60.0
EDGE_MAX_SAMPLES = 8

# Per-edge series written into assets/routes/forecast.json.
ROUTE_SERIES_SPEC = {
    "swh": {"unit": "m", "dataset": "gfswave"},
    "swh_max": {"unit": "m", "dataset": "gfswave"},
    "wind_speed": {"unit": "kt", "dataset": "gfswave"},
    "wind_dir": {"unit": "deg", "dataset": "gfswave"},
    "current": {"unit": "kt", "dataset": "hycom"},
    "current_dir": {"unit": "deg", "dataset": "hycom"},
    "rain": {"unit": "mm/hr", "dataset": "gfsatmos"},
}


def edge_sample_points(
    graph: dict,
    max_step_nm: float = EDGE_MAX_STEP_NM,
    max_samples: int = EDGE_MAX_SAMPLES,
) -> dict[str, list[tuple[float, float]]]:
    """Return the sampling coordinates for every lane edge, keyed by edge id."""
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    points: dict[str, list[tuple[float, float]]] = {}
    for edge in graph.get("edges", []):
        a, b = nodes.get(edge["from"]), nodes.get(edge["to"])
        if not a or not b:
            continue
        points[edge["id"]] = densify_edge(
            a["lat"], a["lon"], b["lat"], b["lon"], max_step_nm, max_samples
        )
    return points


def node_sample_points(graph: dict) -> dict[str, tuple[float, float]]:
    """Return one sample coordinate per graph node (used by the sea-grid graph)."""
    return {
        n["id"]: (float(n["lat"]), float(n["lon"]))
        for n in graph.get("nodes", [])
        if "lat" in n and "lon" in n
    }


def _finite(values: Iterable[Any]) -> list[float]:
    out = []
    for v in values:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return out


def _vector_mean(
    pairs: Iterable[tuple[Any, Any]], meteorological: bool
) -> tuple[Optional[float], Optional[float]]:
    """Average speed/direction pairs as vectors.

    Scalar averaging of compass bearings is wrong across the 0/360 wrap, so
    both wind (direction FROM) and current (direction TOWARD) are averaged in
    component space and converted back.
    """
    us, vs = [], []
    for speed, direction in pairs:
        s = _finite([speed])
        d = _finite([direction])
        if not s or not d:
            continue
        rad = math.radians(d[0])
        sign = -1.0 if meteorological else 1.0
        us.append(sign * s[0] * math.sin(rad))
        vs.append(sign * s[0] * math.cos(rad))
    if not us:
        return None, None
    u = sum(us) / len(us)
    v = sum(vs) / len(vs)
    speed = math.hypot(u, v)
    if meteorological:
        deg = (math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0
    else:
        deg = (math.degrees(math.atan2(u, v)) + 360.0) % 360.0
    return speed, deg


def aggregate_edge_hour(samples: list[dict[str, Any]]) -> dict[str, Optional[float]]:
    """Reduce per-point extractions along one edge to a single condition set.

    Mean values drive the speed model; ``swh_max`` is kept separately because
    safety limits must respond to the worst point on the leg, not its average.
    """
    out: dict[str, Optional[float]] = {}

    swh = _finite(s.get("swh") for s in samples)
    out["swh"] = round(sum(swh) / len(swh), 3) if swh else None
    out["swh_max"] = round(max(swh), 3) if swh else None

    wind_speed, wind_dir = _vector_mean(
        ((s.get("wind_speed"), s.get("wind_dir")) for s in samples), meteorological=True
    )
    out["wind_speed"] = round(wind_speed, 2) if wind_speed is not None else None
    out["wind_dir"] = round(wind_dir, 1) if wind_dir is not None else None

    cur_speed, cur_dir = _vector_mean(
        ((s.get("current"), s.get("current_dir")) for s in samples), meteorological=False
    )
    out["current"] = (
        round(cur_speed * CURRENT_KT_SCALE, 3) if cur_speed is not None else None
    )
    out["current_dir"] = round(cur_dir, 1) if cur_dir is not None else None

    rain = _finite(s.get("rain") for s in samples)
    out["rain"] = round(sum(rain) / len(rain), 3) if rain else None
    return out


def empty_edge_series() -> dict[str, list]:
    """Empty per-edge series containers matching the route forecast schema."""
    return {key: [] for key in ROUTE_SERIES_SPEC}


def build_route_forecast_doc(
    graph: dict,
    cycles: dict[str, str],
    hours: list[str],
    valid_times: list[Optional[str]],
    edges: dict[str, dict],
    generated_at: str,
    samples: dict[str, dict] | None = None,
) -> dict:
    """Assemble the assets/routes/forecast.json document.

    Lane geometry is embedded alongside the samples so the frontend needs a
    single fetch under ``assets/`` to draw and route. Sea-grid graphs publish
    per-node ``samples``; corridor graphs keep per-edge ``series``.
    """
    doc = {
        "generated_at": generated_at,
        "graph": {
            "source": graph.get("source"),
            "generated_at": graph.get("generated_at"),
        },
        "ports": list(graph.get("ports") or []),
        "nodes": list(graph.get("nodes") or []),
        "cycles": {k: v for k, v in cycles.items() if v},
        "hours": hours,
        "valid_times": valid_times,
        "units": {key: spec["unit"] for key, spec in ROUTE_SERIES_SPEC.items()},
        "edges": edges,
    }
    if samples:
        doc["samples"] = samples
    return doc


def _has_values(values: Any) -> bool:
    return isinstance(values, list) and any(v is not None for v in values)


def _hour_lead(label: str) -> int:
    try:
        return int(str(label).lstrip("Ff"))
    except (TypeError, ValueError):
        return 0


def _realign(values: list, source_hours: list[str], target_hours: list[str]) -> list:
    index = {label: i for i, label in enumerate(source_hours)}
    return [
        values[index[h]] if h in index and index[h] < len(values) else None
        for h in target_hours
    ]


def merge_retained_route_series(
    doc: dict,
    previous: dict | None,
    refreshed_datasets: Iterable[str] = (),
) -> list[str]:
    """Keep last-published series for datasets that were not refreshed.

    Works for both per-edge corridor ``series`` and sea-grid per-node
    ``samples``. Mirrors the Site Forecast retention rule.

    Returns the dataset names whose series were retained.
    """
    if not isinstance(previous, dict) or not previous:
        return []

    refreshed = set(refreshed_datasets or ())
    candidate_keys = [
        key
        for key, spec in ROUTE_SERIES_SPEC.items()
        if spec["dataset"] not in refreshed
    ]

    doc_edges = doc.get("edges") or {}
    prev_edges = previous.get("edges") or {}
    doc_samples = doc.get("samples") or {}
    prev_samples = previous.get("samples") or {}

    use_nodes = bool(doc_samples) or bool(prev_samples)
    if use_nodes:
        containers_doc, containers_prev = doc_samples, prev_samples
        series_of = lambda entry: (entry or {}).get("series") or {}
    else:
        containers_doc, containers_prev = doc_edges, prev_edges
        series_of = lambda entry: (entry or {}).get("series") or {}

    if not containers_doc or not containers_prev:
        return []

    retained_keys = [
        key
        for key in candidate_keys
        if any(
            not _has_values(series_of(containers_doc.get(eid)).get(key))
            and _has_values(series_of(entry).get(key))
            for eid, entry in containers_prev.items()
        )
    ]
    if not retained_keys:
        return []

    doc_hours = list(doc.get("hours") or [])
    prev_hours = list(previous.get("hours") or [])
    extra_hours = {
        h
        for entry in containers_prev.values()
        for key in retained_keys
        for h, value in zip(prev_hours, series_of(entry).get(key) or [])
        if value is not None and h not in set(doc_hours)
    }
    target_hours = sorted(set(doc_hours) | extra_hours, key=_hour_lead)

    if target_hours != doc_hours:
        prev_valid = dict(zip(prev_hours, previous.get("valid_times") or []))
        doc_valid = dict(zip(doc_hours, doc.get("valid_times") or []))
        doc["hours"] = target_hours
        doc["valid_times"] = [
            doc_valid.get(h) or prev_valid.get(h) for h in target_hours
        ]
        for entry in list(doc_edges.values()) + list(doc_samples.values()):
            series = entry.get("series") or {}
            for key, values in list(series.items()):
                series[key] = _realign(values, doc_hours, target_hours)

    retained_datasets: list[str] = []
    for eid, entry in containers_doc.items():
        prev_series = series_of(containers_prev.get(eid))
        series = entry.setdefault("series", {})
        for key in retained_keys:
            if _has_values(series.get(key)) or not _has_values(prev_series.get(key)):
                continue
            series[key] = _realign(prev_series[key], prev_hours, target_hours)
            dataset = ROUTE_SERIES_SPEC[key]["dataset"]
            if dataset not in retained_datasets:
                retained_datasets.append(dataset)

    prev_cycles = previous.get("cycles") or {}
    for dataset in retained_datasets:
        if prev_cycles.get(dataset):
            doc.setdefault("cycles", {})[dataset] = prev_cycles[dataset]

    return retained_datasets
