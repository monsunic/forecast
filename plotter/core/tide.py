"""Astronomical tide prediction from site harmonic constituents.

Site Forecast stores compact per-port amplitude/phase tables extracted from a
global tide atlas (FES2022 preferred; GOT as a public bootstrap). Predictions
are pure harmonic reconstructions — not storm surge or residual water level.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

CONSTITUENTS_PATH = Path(__file__).resolve().parents[1] / "data" / "tide_constituents.json"

# Major + shallow-water set used for port charts. Names follow FES/GOT conventions.
DEFAULT_CONSTITUENTS = (
    "2n2", "eps2", "j1", "k1", "k2", "l2", "lambda2", "m2", "m3", "m4",
    "m6", "m8", "mf", "mks2", "mm", "mn4", "ms4", "msf", "msqm", "mtm",
    "mu2", "n2", "n4", "nu2", "o1", "p1", "q1", "r2", "s1", "s2", "s4",
    "sa", "ssa", "t2",
)


def _parse_iso_utc(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hourly_tide_axis(valid_times: list[str]) -> tuple[list[str], list[str]]:
    """Build an hourly lead/time axis spanning the site forecast window.

    Uses the first and last non-null ``valid_times`` from the GFS-aligned
    document so tide covers the same forecast period at 1-hour resolution.
    """
    parsed = [_parse_iso_utc(t) for t in valid_times if t]
    if not parsed:
        return [], []
    start = min(parsed).replace(minute=0, second=0, microsecond=0)
    end = max(parsed).replace(minute=0, second=0, microsecond=0)
    if end < start:
        start, end = end, start
    hours: list[str] = []
    times: list[str] = []
    lead = 0
    cursor = start
    # Inclusive end; cap at 8 days to guard against corrupt timestamps.
    while cursor <= end and lead <= 192:
        hours.append(f"F{lead:03d}")
        times.append(_format_iso_utc(cursor))
        cursor = cursor + timedelta(hours=1)
        lead += 1
    return hours, times


def load_constituents(path: Path | None = None) -> dict:
    """Load the bundled per-site harmonic table."""
    target = Path(path) if path else CONSTITUENTS_PATH
    return json.loads(target.read_text())


def site_entry(table: dict, site_id: str) -> Optional[dict]:
    sites = table.get("sites") or {}
    entry = sites.get(site_id)
    return entry if isinstance(entry, dict) else None


def predict_tide_series(
    entry: dict,
    valid_times: list[str],
    *,
    infer_minor: bool = True,
) -> list[Optional[float]]:
    """Predict astronomical tide height (metres) at each ISO UTC timestamp.

    Uses ``pyTMD.predict`` when available so nodal corrections match the atlas
    family. Falls back to a minor-free cosine sum if pyTMD is not installed.
    """
    times = [_parse_iso_utc(t) for t in valid_times if t]
    if not times or not entry:
        return [None] * len(valid_times)

    names = list(entry.get("constituents") or [])
    amp = np.asarray(entry.get("amplitude_m") or [], dtype=float)
    phase = np.asarray(entry.get("phase_deg") or [], dtype=float)
    if not names or amp.size != len(names) or phase.size != len(names):
        return [None] * len(valid_times)

    # Drop missing / land-masked constituents.
    keep = np.isfinite(amp) & np.isfinite(phase) & (amp >= 0)
    names = [n for n, ok in zip(names, keep) if ok]
    amp = amp[keep]
    phase = phase[keep]
    if not names:
        return [None] * len(valid_times)

    corrections = str(entry.get("corrections") or "FES")
    try:
        values = _predict_pytmd(names, amp, phase, times, corrections, infer_minor)
    except Exception:
        values = _predict_simple(names, amp, phase, times)

    # Map back onto the original valid_times length (None slots stay None).
    out: list[Optional[float]] = []
    idx = 0
    for raw in valid_times:
        if not raw:
            out.append(None)
            continue
        out.append(None if idx >= len(values) else values[idx])
        idx += 1
    return out


def _predict_pytmd(
    names: list[str],
    amp: np.ndarray,
    phase: np.ndarray,
    times: list[datetime],
    corrections: str,
    infer_minor: bool,
) -> list[float]:
    import pyTMD.predict
    import pyTMD.astro
    from timescale.time import Timescale

    # Complex harmonic constants in metres (GOT/FES phase convention: G).
    hc = amp * np.exp(-1j * phase * np.pi / 180.0)
    ts = Timescale(
        UTC=np.array(
            [
                [
                    dt.year,
                    dt.month,
                    dt.day,
                    dt.hour,
                    dt.minute,
                    dt.second + dt.microsecond * 1e-6,
                ]
                for dt in times
            ],
            dtype=float,
        )
    )
    tide = pyTMD.predict.time_series(
        ts.tide,
        hc,
        names,
        deltat=ts.tt_ut1,
        corrections=corrections,
    )
    if infer_minor:
        try:
            tide = tide + pyTMD.predict.infer_minor(
                ts.tide,
                hc,
                names,
                deltat=ts.tt_ut1,
                corrections=corrections,
            )
        except Exception:
            pass
    return [None if not np.isfinite(v) else round(float(v), 3) for v in np.asarray(tide)]


def _predict_simple(
    names: list[str],
    amp: np.ndarray,
    phase: np.ndarray,
    times: list[datetime],
) -> list[float]:
    """Cosine sum without nodal corrections — test / offline fallback only."""
    # Angular frequencies (rad/s) for a minimal major set.
    omega = {
        "m2": 1.405189e-4,
        "s2": 1.454441e-4,
        "n2": 1.378797e-4,
        "k2": 1.458423e-4,
        "k1": 7.292117e-5,
        "o1": 6.759774e-5,
        "p1": 7.252295e-5,
        "q1": 6.495854e-5,
        "mf": 0.053234e-4,
        "mm": 0.026392e-4,
        "ssa": 0.398257e-5,
        "sa": 0.199186e-5,
        "m4": 2.810377e-4,
        "ms4": 2.859630e-4,
        "mn4": 2.783986e-4,
    }
    t0 = datetime(2000, 1, 1, tzinfo=timezone.utc)
    out = []
    for dt in times:
        seconds = (dt - t0).total_seconds()
        height = 0.0
        for name, a, g in zip(names, amp, phase):
            w = omega.get(name.lower())
            if w is None or not math.isfinite(a):
                continue
            height += float(a) * math.cos(w * seconds - math.radians(float(g)))
        out.append(round(height, 3))
    return out


def tide_series_entry(
    values: list[Optional[float]],
    model: str,
    *,
    hours: list[str] | None = None,
    valid_times: list[str] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "unit": "m",
        "values": values,
        "model": model,
        "datum": "model_zero",
        "step_hours": 1,
        "note": "Astronomical tide (no surge), hourly",
    }
    if hours is not None:
        entry["hours"] = list(hours)
    if valid_times is not None:
        entry["valid_times"] = list(valid_times)
    return entry
