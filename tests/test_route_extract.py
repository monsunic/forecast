"""Along-lane sampling and retain-on-failure tests."""

from __future__ import annotations

from plotter.core.route_extract import (
    aggregate_edge_hour,
    build_route_forecast_doc,
    edge_sample_points,
    merge_retained_route_series,
)


def _edge(series):
    return {
        "from": "a",
        "to": "b",
        "distance_nm": 100.0,
        "bearing_deg": 90.0,
        "series": series,
    }


def test_edge_sample_points_cover_every_edge():
    graph = {
        "nodes": [
            {"id": "a", "lat": 0.0, "lon": 100.0, "type": "port"},
            {"id": "b", "lat": 0.0, "lon": 102.0, "type": "waypoint"},
            {"id": "c", "lat": 1.0, "lon": 102.0, "type": "port"},
        ],
        "edges": [
            {"id": "a__b", "from": "a", "to": "b", "distance_nm": 120.0},
            {"id": "b__c", "from": "b", "to": "c", "distance_nm": 60.0},
        ],
    }
    points = edge_sample_points(graph, max_step_nm=80.0, max_samples=6)
    assert set(points) == {"a__b", "b__c"}
    assert len(points["a__b"]) >= 2
    assert len(points["b__c"]) >= 1


def test_aggregate_edge_hour_uses_mean_and_max_swh():
    samples = [
        {"swh": 1.0, "wind_speed": 10.0, "wind_dir": 0.0, "current": 50.0, "current_dir": 90.0, "rain": 0.1},
        {"swh": 3.0, "wind_speed": 20.0, "wind_dir": 0.0, "current": 50.0, "current_dir": 90.0, "rain": 0.3},
    ]
    out = aggregate_edge_hour(samples)
    assert out["swh"] == 2.0
    assert out["swh_max"] == 3.0
    assert out["wind_speed"] == 15.0
    assert out["wind_dir"] == 0.0
    # current is converted from cm/s to kt
    assert out["current"] is not None and abs(out["current"] - 50.0 / 51.444444) < 1e-3
    assert out["current_dir"] == 90.0
    assert out["rain"] == 0.2


def test_aggregate_edge_hour_averages_bearings_across_wrap():
    samples = [
        {"swh": 1.0, "wind_speed": 10.0, "wind_dir": 350.0},
        {"swh": 1.0, "wind_speed": 10.0, "wind_dir": 10.0},
    ]
    out = aggregate_edge_hour(samples)
    # Mean of 350° and 10° must land near north, not near 180°.
    assert abs(out["wind_dir"] - 0.0) < 1e-6 or abs(out["wind_dir"] - 360.0) < 1e-6


def test_merge_retained_route_series_keeps_ocean_when_hycom_missing():
    doc = build_route_forecast_doc(
        graph={"source": "manual", "generated_at": "now", "ports": [], "nodes": []},
        cycles={"gfswave": "2026010100"},
        hours=["F000", "F003"],
        valid_times=["2026-01-01T00:00:00Z", "2026-01-01T03:00:00Z"],
        edges={
            "a__b": _edge(
                {
                    "swh": [1.0, 1.2],
                    "swh_max": [1.1, 1.3],
                    "wind_speed": [10.0, 12.0],
                    "wind_dir": [90.0, 100.0],
                }
            )
        },
        generated_at="now",
    )
    previous = build_route_forecast_doc(
        graph={"source": "manual", "generated_at": "old", "ports": [], "nodes": []},
        cycles={"gfswave": "2025123100", "hycom": "2025123100"},
        hours=["F000", "F006"],
        valid_times=["2025-12-31T00:00:00Z", "2025-12-31T06:00:00Z"],
        edges={
            "a__b": _edge(
                {
                    "swh": [0.5, 0.6],
                    "current": [0.4, 0.5],
                    "current_dir": [180.0, 190.0],
                }
            )
        },
        generated_at="old",
    )

    retained = merge_retained_route_series(doc, previous, refreshed_datasets=["gfswave"])
    assert retained == ["hycom"]
    series = doc["edges"]["a__b"]["series"]
    # Fresh wave series stay; ocean is reindexed onto the union of hours.
    assert series["swh"][0] == 1.0
    assert series["current"] == [0.4, None, 0.5]
    assert "F006" in doc["hours"]
    assert doc["cycles"]["hycom"] == "2025123100"


def test_merge_retained_route_series_skips_refreshed_datasets():
    doc = build_route_forecast_doc(
        graph={"source": "manual", "ports": [], "nodes": []},
        cycles={"gfswave": "2026010100", "hycom": "2026010100"},
        hours=["F000"],
        valid_times=["2026-01-01T00:00:00Z"],
        edges={"a__b": _edge({"swh": [1.0], "current": [0.2]})},
        generated_at="now",
    )
    previous = build_route_forecast_doc(
        graph={"source": "manual", "ports": [], "nodes": []},
        cycles={"gfswave": "old", "hycom": "old"},
        hours=["F000"],
        valid_times=["2025-12-31T00:00:00Z"],
        edges={"a__b": _edge({"swh": [9.0], "current": [9.0]})},
        generated_at="old",
    )
    retained = merge_retained_route_series(
        doc, previous, refreshed_datasets=["gfswave", "hycom"]
    )
    assert retained == []
    assert doc["edges"]["a__b"]["series"]["swh"] == [1.0]
    assert doc["edges"]["a__b"]["series"]["current"] == [0.2]
