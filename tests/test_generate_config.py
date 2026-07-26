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
    sites = tmp_path / "sites"
    sites.mkdir()
    monkeypatch.setattr(gc, "MAPS_ROOT", maps)
    monkeypatch.setattr(gc, "SITES_ROOT", sites)

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

    status = config["status"]
    assert "generated_at" in status
    assert status["forecast"] == {"max_hours": 4, "hour_step": 1}
    assert status["services"][0]["id"] == "map_forecast"
    assert status["services"][0]["state"] == "operational"
    assert status["services"][1]["state"] == "planned"
    wave = status["datasets"]["gfswave"]
    assert wave["state"] == "operational"
    assert wave["cycle"] == "2026072500"
    assert wave["regions"] == 1
    assert set(wave["products"]) == {"wind", "swh", "swell"}
    assert wave["hour_count"] == 4
    assert wave["hours_first"] == "F000"
    assert wave["hours_last"] == "F003"
    atmos = status["datasets"]["gfsatmos"]
    assert atmos["cycle"] == "2026072506"
    assert atmos["state"] == "operational"


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


def test_build_status_marks_dataset_with_cycle_but_no_maps_unavailable(tmp_path, monkeypatch):
    """A dataset that was attempted (has a cycle) but produced no maps is 'unavailable'."""
    import scripts.generate_config as gc

    maps = tmp_path / "maps"
    monkeypatch.setattr(gc, "MAPS_ROOT", maps)
    _write_maps(maps, "gfswave", "indonesia", {"wind": ["000"]})

    config = gc.build_config(
        datasets=["gfswave", "hycom"],
        cycles={"gfswave": "2026072500", "hycom": "2026072400"},
        max_hours=3,
        hour_step=3,
    )
    assert config["status"]["datasets"]["gfswave"]["state"] == "operational"
    assert config["status"]["datasets"]["hycom"]["state"] == "unavailable"
    assert config["status"]["datasets"]["hycom"]["regions"] == 0
    assert config["status"]["datasets"]["hycom"]["cycle"] == "2026072400"


def test_build_status_excludes_dataset_without_maps_or_cycle(tmp_path, monkeypatch):
    """Catalog-only datasets with no maps and no cycle (e.g. cmems) are omitted."""
    import scripts.generate_config as gc

    maps = tmp_path / "maps"
    monkeypatch.setattr(gc, "MAPS_ROOT", maps)
    _write_maps(maps, "gfswave", "indonesia", {"wind": ["000"]})

    config = gc.build_config(
        datasets=["gfswave", "cmems"],
        cycles={"gfswave": "2026072500"},
        max_hours=3,
        hour_step=3,
    )
    assert "gfswave" in config["status"]["datasets"]
    assert "cmems" not in config["status"]["datasets"]


def test_sync_status_cycles():
    import scripts.generate_config as gc

    config = {
        "cycles": {"gfswave": "2026072512", "hycom": "2026072412"},
        "status": {
            "datasets": {
                "gfswave": {"cycle": "2026072500"},
                "hycom": {"cycle": None},
            }
        },
    }
    gc.sync_status_cycles(config)
    assert config["status"]["datasets"]["gfswave"]["cycle"] == "2026072512"
    assert config["status"]["datasets"]["hycom"]["cycle"] == "2026072412"


def test_load_previous_cycles_preserves_stale_source_metadata(tmp_path):
    import scripts.generate_config as gc

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "cycles": {
                    "gfswave": "2026072518",
                    "gfsatmos": "2026072518",
                    "hycom": "2026072421",
                }
            }
        )
    )

    cycles = gc.load_previous_cycles(config_path)
    cycles.update({"gfswave": "2026072600", "gfsatmos": "2026072600"})

    assert cycles == {
        "gfswave": "2026072600",
        "gfsatmos": "2026072600",
        "hycom": "2026072421",
    }


def test_scan_sites_and_status_operational(tmp_path, monkeypatch):
    import scripts.generate_config as gc

    maps = tmp_path / "maps"
    sites = tmp_path / "sites"
    monkeypatch.setattr(gc, "MAPS_ROOT", maps)
    monkeypatch.setattr(gc, "SITES_ROOT", sites)

    _write_maps(maps, "gfswave", "indonesia", {"wind": ["000"], "swh": ["000"], "swell": ["000"]})

    site_dir = sites / "singapore"
    site_dir.mkdir(parents=True)
    (site_dir / "forecast.json").write_text(
        """
        {
          "site": {"id": "singapore", "name": "Port of Singapore", "lat": 1.2788, "lon": 103.7566},
          "cycles": {"gfswave": "2026072506"},
          "generated_at": "2026-07-26T02:00:00Z",
          "hours": ["F000"],
          "valid_times": ["2026-07-25T06:00:00Z"],
          "series": {"wind_speed": {"unit": "kt", "values": [10], "dir_deg": [90]}}
        }
        """.strip()
    )
    (site_dir / "charts.webp").write_bytes(b"fake")

    config = gc.build_config(
        datasets=["gfswave"],
        cycles={"gfswave": "2026072506"},
        max_hours=3,
        hour_step=3,
    )
    assert any(s["id"] == "singapore" and s["has_data"] for s in config["sites"])
    site_svc = next(s for s in config["status"]["services"] if s["id"] == "site_forecast")
    assert site_svc["state"] == "operational"


def test_site_forecast_planned_without_site_files(tmp_path, monkeypatch):
    import scripts.generate_config as gc

    maps = tmp_path / "maps"
    sites = tmp_path / "sites"
    sites.mkdir()
    monkeypatch.setattr(gc, "MAPS_ROOT", maps)
    monkeypatch.setattr(gc, "SITES_ROOT", sites)
    _write_maps(maps, "gfswave", "indonesia", {"wind": ["000"]})

    config = gc.build_config(
        datasets=["gfswave"],
        cycles={"gfswave": "2026072506"},
        max_hours=3,
        hour_step=3,
    )
    site_svc = next(s for s in config["status"]["services"] if s["id"] == "site_forecast")
    assert site_svc["state"] == "planned"
    assert all(not s.get("has_data") for s in config["sites"])


def test_route_forecast_operational_when_published(tmp_path, monkeypatch):
    import scripts.generate_config as gc

    maps = tmp_path / "maps"
    routes = tmp_path / "routes"
    routes.mkdir()
    monkeypatch.setattr(gc, "MAPS_ROOT", maps)
    monkeypatch.setattr(gc, "ROUTES_ROOT", routes)
    monkeypatch.setattr(gc, "SITES_ROOT", tmp_path / "sites")
    (tmp_path / "sites").mkdir()
    _write_maps(maps, "gfswave", "indonesia", {"wind": ["000"]})

    (routes / "field.json").write_text(
        """
        {
          "generated_at": "2026-07-26T00:00:00Z",
          "kind": "grid_field",
          "ports": [{"id": "singapore", "name": "Singapore", "lat": 1.3, "lon": 103.8}],
          "hours": ["F000", "F003"],
          "cycles": {"gfswave": "2026072600"},
          "grid": {"lat_min": 0.0, "lon_min": 100.0, "dlat": 0.5, "dlon": 0.5, "nlat": 2, "nlon": 2},
          "sea_mask": [1, 0, 1, 1],
          "vars": {"swh": [[1.0, null, 1.1, 1.2], [1.0, null, 1.1, 1.2]]}
        }
        """.strip()
    )

    config = gc.build_config(
        datasets=["gfswave"],
        cycles={"gfswave": "2026072600"},
        max_hours=3,
        hour_step=3,
    )
    assert config["route"]["sea_cells"] == 3
    assert config["route"]["lane_source"] == "grid"
    route_svc = next(s for s in config["status"]["services"] if s["id"] == "route_forecast")
    assert route_svc["state"] == "operational"
    assert route_svc["hours_last"] == "F003"

