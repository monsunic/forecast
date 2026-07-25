"""Tests for multi-dataset frontend config generation."""

import json
from pathlib import Path

import pytest


def _write_maps(root: Path, dataset: str, region: str, params_hours):
    region_dir = root / dataset / region
    region_dir.mkdir(parents=True, exist_ok=True)
    for param, hours in params_hours.items():
        for h in hours:
            (region_dir / f"{param}_{h}.webp").write_bytes(b"fake")


def test_build_config_merges_wave_and_atmosphere(tmp_path, monkeypatch):
    import scripts.generate_config as gc

    maps = tmp_path / "maps"
    monkeypatch.setattr(gc, "MAPS_ROOT", maps)

    _write_maps(
        maps,
        "gfswave",
        "malacca_strait",
        {"wind": ["000", "001", "002", "003"], "swh": ["000", "001", "002", "003"], "swell": ["000", "001", "002", "003"]},
    )
    _write_maps(
        maps,
        "gfsatmos",
        "malacca_strait",
        {
            "rainrate": ["000", "001", "002", "003"],
            "temp": ["000", "001", "002", "003"],
            "relhum": ["000", "001", "002", "003"],
            "mslp": ["000", "001", "002", "003"],
        },
    )

    config = gc.build_config(
        datasets=["gfswave", "gfsatmos"],
        cycles={"gfswave": "2026072500", "gfsatmos": "2026072506"},
        max_hours=4,
        hour_step=1,
    )

    region = config["regions"]["malacca_strait"]["forecast_types"]
    assert "Wind and Waves" in region
    assert "Atmosphere" in region

    waves = region["Wind and Waves"]
    atmos = region["Atmosphere"]
    assert waves["dataset"] == "gfswave"
    assert atmos["dataset"] == "gfsatmos"
    assert waves["cycle"] == "2026072500"
    assert atmos["cycle"] == "2026072506"
    assert set(waves["parameters"]) == {"surface_wind", "swh", "swell"}
    assert set(atmos["parameters"]) == {"sfc_temp", "rh"}
    assert atmos["parameters"]["sfc_temp"] == ["F000", "F001", "F002", "F003"]
    assert config["cycle"] == "2026072500"
    assert config["cycles"]["gfsatmos"] == "2026072506"


def test_build_config_skips_empty_params(tmp_path, monkeypatch):
    import scripts.generate_config as gc

    maps = tmp_path / "maps"
    monkeypatch.setattr(gc, "MAPS_ROOT", maps)

    # Only wind frames — swh/swell absent → omitted from parameters.
    _write_maps(maps, "gfswave", "indonesia", {"wind": ["000", "001"]})

    config = gc.build_config(
        datasets=["gfswave"], cycles="2026072500", max_hours=4, hour_step=1
    )
    params = config["regions"]["indonesia"]["forecast_types"]["Wind and Waves"]["parameters"]
    assert list(params.keys()) == ["surface_wind"]
    assert params["surface_wind"] == ["F000", "F001"]


def test_build_config_three_hourly(tmp_path, monkeypatch):
    import scripts.generate_config as gc

    maps = tmp_path / "maps"
    monkeypatch.setattr(gc, "MAPS_ROOT", maps)

    hours = ["000", "003", "006", "009", "012"]
    _write_maps(
        maps,
        "gfswave",
        "indonesia",
        {"wind": hours, "swh": hours, "swell": hours},
    )

    config = gc.build_config(
        datasets=["gfswave"], cycles="2026072500", max_hours=12, hour_step=3
    )
    waves = config["regions"]["indonesia"]["forecast_types"]["Wind and Waves"]
    assert waves["timestamps"] == ["F000", "F003", "F006", "F009", "F012"]
    assert waves["parameters"]["surface_wind"] == ["F000", "F003", "F006", "F009", "F012"]


def test_build_config_preserves_wave_when_only_atmos_regenerated(tmp_path, monkeypatch):
    """Scanning both datasets keeps wave entries even if only atmos was re-rendered."""
    import scripts.generate_config as gc

    maps = tmp_path / "maps"
    monkeypatch.setattr(gc, "MAPS_ROOT", maps)

    _write_maps(
        maps,
        "gfswave",
        "philippines",
        {"wind": ["000"], "swh": ["000"], "swell": ["000"]},
    )
    _write_maps(
        maps,
        "gfsatmos",
        "philippines",
        {"temp": ["000", "001"]},
    )

    config = gc.build_config(
        datasets=["gfswave", "gfsatmos"],
        cycles={"gfswave": "2026072500", "gfsatmos": "2026072500"},
        max_hours=4,
        hour_step=1,
    )
    ftypes = config["regions"]["philippines"]["forecast_types"]
    assert "Wind and Waves" in ftypes
    assert "Atmosphere" in ftypes
    assert "sfc_temp" in ftypes["Atmosphere"]["parameters"]
    assert "rain" not in ftypes["Atmosphere"]["parameters"]
