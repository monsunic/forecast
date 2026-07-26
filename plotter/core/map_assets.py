"""Helpers for managing rendered map WebP assets on disk."""

import os
import shutil
from pathlib import Path


def _hour_from_name(path: Path):
    try:
        return int(path.stem.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return None


def _normalize_hours(hours):
    """Accept an int frame-count or an explicit lead-hour sequence."""
    if hours is None:
        return None
    if isinstance(hours, int):
        return list(range(hours))
    return [int(h) for h in hours]


def clear_param_maps(
    maps_root: Path,
    regions,
    params,
    max_hours: int | None = None,
    *,
    forecast_hours=None,
    purge_beyond: bool = True,
):
    """
    Remove WebP maps for params being regenerated.

    - ``forecast_hours``: delete those lead hours; with ``purge_beyond``, also
      delete any off-schedule leftovers (e.g. old 1-hourly frames).
    - ``max_hours is None`` and no ``forecast_hours``: delete all matching maps.
    - otherwise (legacy): delete hours in ``[0, max_hours)``; if ``purge_beyond``,
      also delete hours ``>= max_hours``.
    """
    hour_list = _normalize_hours(forecast_hours)
    hour_set = set(hour_list) if hour_list is not None else None

    removed = 0
    for region in regions:
        region_dir = maps_root / region
        region_dir.mkdir(parents=True, exist_ok=True)
        for param in params:
            for old in region_dir.glob(f"{param}_*.webp"):
                hour = _hour_from_name(old)
                if hour is None:
                    continue
                if hour_set is not None:
                    if hour in hour_set or purge_beyond:
                        old.unlink()
                        removed += 1
                elif max_hours is None:
                    old.unlink()
                    removed += 1
                elif hour < max_hours or (purge_beyond and hour >= max_hours):
                    old.unlink()
                    removed += 1
    if removed:
        print(f"[INFO] Cleared {removed} stale map file(s) under {maps_root}")
    return removed


def verify_param_maps(maps_root: Path, regions, params, hours):
    """Fail loudly if any expected map is missing after a partial run.

    ``hours`` may be an int (legacy: expect F000…F{n-1}) or a sequence of
    lead hours (e.g. ``[0, 3, 6, …, 72]``).
    """
    hour_list = _normalize_hours(hours)
    if not hour_list:
        return

    missing = []
    for region in regions:
        for param in params:
            for t in hour_list:
                path = maps_root / region / f"{param}_{t:03d}.webp"
                if not path.is_file():
                    missing.append(str(path.relative_to(maps_root.parent.parent)))
    if missing:
        preview = "\n  ".join(missing[:12])
        more = f"\n  ... and {len(missing) - 12} more" if len(missing) > 12 else ""
        raise SystemExit(
            f"[ERROR] Incomplete render: {len(missing)} expected map(s) missing:\n  {preview}{more}"
        )


def promote_param_maps(staging_root: Path, maps_root: Path, regions, params, hours):
    """Promote a verified render without clearing the previous maps first.

    Each new frame atomically replaces its old counterpart. Off-schedule frames
    are removed only after all expected staged frames have been promoted, so a
    download/render failure leaves the last successful dataset untouched.
    """
    hour_list = _normalize_hours(hours)
    if not hour_list:
        raise ValueError("Cannot promote a render with no forecast hours")

    verify_param_maps(staging_root, regions, params, hour_list)
    expected_hours = set(hour_list)
    promoted = 0
    removed = 0

    for region in regions:
        source_dir = staging_root / region
        target_dir = maps_root / region
        target_dir.mkdir(parents=True, exist_ok=True)

        for param in params:
            for hour in hour_list:
                source = source_dir / f"{param}_{hour:03d}.webp"
                target = target_dir / source.name
                os.replace(source, target)
                promoted += 1

            for old in target_dir.glob(f"{param}_*.webp"):
                hour = _hour_from_name(old)
                if hour is not None and hour not in expected_hours:
                    old.unlink()
                    removed += 1

    shutil.rmtree(staging_root, ignore_errors=True)
    print(
        f"[INFO] Promoted {promoted} map file(s) to {maps_root}"
        + (f"; removed {removed} stale frame(s)" if removed else "")
    )
    return promoted
