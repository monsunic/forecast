"""Helpers for managing rendered map WebP assets on disk."""

from pathlib import Path


def _hour_from_name(path: Path):
    try:
        return int(path.stem.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return None


def clear_param_maps(
    maps_root: Path,
    regions,
    params,
    max_hours: int | None = None,
    *,
    purge_beyond: bool = True,
):
    """
    Remove WebP maps for params being regenerated.

    - ``max_hours is None``: delete all matching param maps.
    - otherwise: delete hours in ``[0, max_hours)`` (about to be rewritten).
    - if ``purge_beyond``: also delete hours ``>= max_hours`` (stale leftovers).
    """
    removed = 0
    for region in regions:
        region_dir = maps_root / region
        region_dir.mkdir(parents=True, exist_ok=True)
        for param in params:
            for old in region_dir.glob(f"{param}_*.webp"):
                hour = _hour_from_name(old)
                if hour is None:
                    continue
                if max_hours is None:
                    old.unlink()
                    removed += 1
                elif hour < max_hours or (purge_beyond and hour >= max_hours):
                    old.unlink()
                    removed += 1
    if removed:
        print(f"[INFO] Cleared {removed} stale map file(s) under {maps_root}")
    return removed


def verify_param_maps(maps_root: Path, regions, params, hours_completed: int):
    """Fail loudly if any expected map is missing after a partial run."""
    missing = []
    for region in regions:
        for param in params:
            for t in range(hours_completed):
                path = maps_root / region / f"{param}_{t:03d}.webp"
                if not path.is_file():
                    missing.append(str(path.relative_to(maps_root.parent.parent)))
    if missing:
        preview = "\n  ".join(missing[:12])
        more = f"\n  ... and {len(missing) - 12} more" if len(missing) > 12 else ""
        raise SystemExit(
            f"[ERROR] Incomplete render: {len(missing)} expected map(s) missing:\n  {preview}{more}"
        )
