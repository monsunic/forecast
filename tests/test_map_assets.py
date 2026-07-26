"""Tests for transactional publication of rendered map assets."""

from pathlib import Path

import pytest

from plotter.core.map_assets import promote_param_maps


def _write(root: Path, region: str, param: str, hour: int, content: bytes):
    path = root / region / f"{param}_{hour:03d}.webp"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_failed_staging_verification_keeps_previous_maps(tmp_path):
    live = tmp_path / "live"
    staging = tmp_path / "staging"
    old_000 = _write(live, "indonesia", "seatemp", 0, b"old-000")
    old_006 = _write(live, "indonesia", "seatemp", 6, b"old-006")
    _write(staging, "indonesia", "seatemp", 0, b"new-000")

    with pytest.raises(SystemExit, match="Incomplete render"):
        promote_param_maps(
            staging,
            live,
            regions=["indonesia"],
            params=["seatemp"],
            hours=[0, 6],
        )

    assert old_000.read_bytes() == b"old-000"
    assert old_006.read_bytes() == b"old-006"


def test_complete_staging_atomically_replaces_and_prunes_maps(tmp_path):
    live = tmp_path / "live"
    staging = tmp_path / "staging"
    _write(live, "indonesia", "seatemp", 0, b"old-000")
    stale = _write(live, "indonesia", "seatemp", 3, b"stale-003")
    _write(staging, "indonesia", "seatemp", 0, b"new-000")
    _write(staging, "indonesia", "seatemp", 6, b"new-006")

    count = promote_param_maps(
        staging,
        live,
        regions=["indonesia"],
        params=["seatemp"],
        hours=[0, 6],
    )

    assert count == 2
    assert (live / "indonesia" / "seatemp_000.webp").read_bytes() == b"new-000"
    assert (live / "indonesia" / "seatemp_006.webp").read_bytes() == b"new-006"
    assert not stale.exists()
    assert not staging.exists()
