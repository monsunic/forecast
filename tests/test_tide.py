"""Tests for astronomical tide prediction helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import json


def _minimal_table(tmp_path: Path) -> Path:
    table = {
        "model": "TEST",
        "corrections": "FES",
        "sites": {
            "singapore": {
                "lat": 1.2644,
                "lon": 103.82,
                "corrections": "FES",
                "constituents": ["m2", "s2", "k1", "o1"],
                "amplitude_m": [0.75, 0.25, 0.28, 0.22],
                "phase_deg": [300.0, 330.0, 340.0, 280.0],
            }
        },
    }
    path = tmp_path / "tide_constituents.json"
    path.write_text(json.dumps(table))
    return path


def test_predict_tide_series_returns_finite_heights(tmp_path):
    from plotter.core.tide import load_constituents, predict_tide_series, site_entry

    table = load_constituents(_minimal_table(tmp_path))
    entry = site_entry(table, "singapore")
    times = [
        (datetime(2026, 7, 25, 18, tzinfo=timezone.utc) + timedelta(hours=3 * i)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        for i in range(8)
    ]
    values = predict_tide_series(entry, times)
    assert len(values) == 8
    assert all(v is not None and abs(v) < 3.0 for v in values)
    # Not a flat line — tidal oscillation should appear.
    assert max(values) - min(values) > 0.2


def test_hourly_tide_axis_spans_window():
    from plotter.core.tide import hourly_tide_axis

    hours, times = hourly_tide_axis(
        ["2026-07-25T18:00:00Z", "2026-07-25T21:00:00Z", "2026-07-25T15:00:00Z"]
    )
    assert hours == ["F000", "F001", "F002", "F003", "F004", "F005", "F006"]
    assert times[0] == "2026-07-25T15:00:00Z"
    assert times[-1] == "2026-07-25T21:00:00Z"
    assert len(times) == 7


def test_attach_astronomical_tide_writes_hourly_series(tmp_path):
    from plotter.core.tide import load_constituents
    from src.site_forecast import attach_astronomical_tide

    table = load_constituents(_minimal_table(tmp_path))
    doc = {
        "site": {"id": "singapore", "name": "Port of Singapore", "lat": 1.26, "lon": 103.82},
        "hours": ["F000", "F003"],
        "valid_times": ["2026-07-25T18:00:00Z", "2026-07-25T21:00:00Z"],
        "series": {},
        "cycles": {},
    }
    assert attach_astronomical_tide(doc, constituents=table) is True
    tide = doc["series"]["tide"]
    assert doc["cycles"]["tide"] == "TEST"
    assert tide["unit"] == "m"
    assert tide["step_hours"] == 1
    assert tide["hours"] == ["F000", "F001", "F002", "F003"]
    assert tide["valid_times"] == [
        "2026-07-25T18:00:00Z",
        "2026-07-25T19:00:00Z",
        "2026-07-25T20:00:00Z",
        "2026-07-25T21:00:00Z",
    ]
    assert len(tide["values"]) == 4
    assert "hourly" in tide["note"].lower()
