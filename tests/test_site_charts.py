"""Tests for site chart rendering helpers and downloadable WebP smoke path."""

from __future__ import annotations

import json
from pathlib import Path


def _fixture_doc(*, n_hours: int = 25, hour_step: int = 3) -> dict:
    """Build a site forecast doc shaped like a full F000…F072 / 3-hourly run.

    HYCOM slots are populated only every 6 h (every other GFS step) so the
    renderer must tolerate null-padded ocean series the same way production does.
    """
    from datetime import datetime, timedelta, timezone

    hours = [f"F{t:03d}" for t in range(0, n_hours * hour_step, hour_step)]
    n = len(hours)
    cycle = datetime(2026, 7, 25, 18, tzinfo=timezone.utc)
    valid_times = [
        (cycle + timedelta(hours=i * hour_step)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for i in range(n)
    ]

    def series(values, dirs=None, unit="kt"):
        entry = {"unit": unit, "values": values}
        if dirs is not None:
            entry["dir_deg"] = dirs
        return entry

    wind = [8.0 + (i % 5) * 0.3 for i in range(n)]
    wind_dir = [150.0 + i * 2 for i in range(n)]
    swh = [0.2 + (i % 4) * 0.05 for i in range(n)]
    swh_dir = [120.0 + i for i in range(n)]
    swell = [0.1 + (i % 3) * 0.02 for i in range(n)]
    swell_dir = [90.0 + i for i in range(n)]

    # Ocean: present on 6-hourly slots only (indices 0, 2, 4, …)
    sst = [29.5 + 0.01 * i if i % 2 == 0 else None for i in range(n)]
    current = [40.0 + i if i % 2 == 0 else None for i in range(n)]
    current_dir = [50.0 + i * 3 if i % 2 == 0 else None for i in range(n)]

    rain = [0.0 if i % 5 else 1.2 for i in range(n)]
    temp = [27.0 + (i % 6) * 0.1 for i in range(n)]
    rh = [70.0 + (i % 8) for i in range(n)]
    # Hourly tide over the same window (F000…F072 → 73 samples).
    n_tide = (n_hours - 1) * hour_step + 1
    tide = [0.2 * ((i % 5) - 2) for i in range(n_tide)]
    tide_hours = [f"F{t:03d}" for t in range(n_tide)]
    tide_times = [
        (cycle + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for i in range(n_tide)
    ]

    return {
        "site": {
            "id": "singapore",
            "name": "Port of Singapore",
            "lat": 1.2644,
            "lon": 103.82,
        },
        "cycles": {
            "gfswave": "2026072518",
            "gfsatmos": "2026072518",
            "hycom": "2026072421",
            "tide": "GOT4.10",
        },
        "generated_at": "2026-07-26T04:00:00Z",
        "hours": hours,
        "valid_times": valid_times,
        "grid_points": {
            "gfswave": {"lat": 1.25, "lon": 104.0},
            "gfsatmos": {"lat": 1.25, "lon": 103.75},
            "hycom": {"lat": 1.2, "lon": 103.84},
        },
        "series": {
            "wind_speed": series(wind, wind_dir, "kt"),
            "swh": series(swh, swh_dir, "m"),
            "swell": series(swell, swell_dir, "m"),
            "sst": series(sst, None, "degC"),
            "current": series(current, current_dir, "cm/s"),
            "rain": series(rain, None, "mm/hr"),
            "temp": series(temp, None, "degC"),
            "rh": series(rh, None, "%"),
            "tide": {
                "unit": "m",
                "values": tide,
                "hours": tide_hours,
                "valid_times": tide_times,
                "model": "GOT4.10",
                "step_hours": 1,
            },
        },
    }


def test_format_axis_label_compact_utc():
    from src.site_forecast import _format_axis_label

    assert _format_axis_label("2026-07-25T18:00:00Z", "F000") == "25 Jul 18Z"
    assert _format_axis_label("2026-07-26T06:00:00Z", "F012") == "26 Jul 06Z"
    assert _format_axis_label(None, "F003") == "F003"
    assert _format_axis_label("not-a-date", "F006") == "F006"


def test_uv_speed_dir_cardinal_edges():
    """Direction conventions used by chart arrows / tooltips."""
    from plotter.core.site_extract import CURRENT_CMS_SCALE, WIND_KT_SCALE, _uv_speed_dir

    # Southward wind (v < 0) → meteorological FROM is north (0°)
    speed, direction = _uv_speed_dir(0.0, -2.0, WIND_KT_SCALE, meteorological=True)
    assert abs(speed - 2.0 * WIND_KT_SCALE) < 1e-6
    assert abs(direction - 0.0) < 1e-6 or abs(direction - 360.0) < 1e-6

    # Westward current (u < 0) → oceanographic TO is west (270°)
    speed, direction = _uv_speed_dir(-1.5, 0.0, CURRENT_CMS_SCALE, meteorological=False)
    assert abs(speed - 1.5 * CURRENT_CMS_SCALE) < 1e-6
    assert abs(direction - 270.0) < 1e-6

    # Missing components
    assert _uv_speed_dir(None, 1.0) == (None, None)


def test_render_site_charts_smoke(tmp_path: Path):
    """Full dual-axis WebP pack must render with HYCOM null padding."""
    from src.site_forecast import render_site_charts

    doc = _fixture_doc(n_hours=25, hour_step=3)
    assert len(doc["hours"]) == 25  # F000…F072 @ 3 h
    out = tmp_path / "charts.webp"
    render_site_charts(doc, out)
    assert out.is_file()
    assert out.stat().st_size > 5_000
    # WebP RIFF header
    assert out.read_bytes()[:4] == b"RIFF"


def test_render_site_charts_short_window(tmp_path: Path):
    from src.site_forecast import render_site_charts

    doc = _fixture_doc(n_hours=5, hour_step=3)
    out = tmp_path / "short.webp"
    render_site_charts(doc, out)
    assert out.stat().st_size > 2_000


def test_fixture_doc_is_json_serializable():
    doc = _fixture_doc(n_hours=5)
    payload = json.dumps(doc)
    assert "singapore" in payload
