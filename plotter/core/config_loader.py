import sys
import yaml
from pathlib import Path

_CONFIG_CACHE = None


def deep_update(base: dict, updates: dict):
    """Recursively merge updates into base."""
    for k, v in updates.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_update(base[k], v)
        else:
            base[k] = v
    return base


def _config_path():
    base_dir = Path(__file__).resolve().parent.parent
    return base_dir / "config" / "config.yaml"


def load_param_config():
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    config_path = _config_path()
    if not config_path.exists():
        raise FileNotFoundError(f"[ERROR] config.yaml not found at: {config_path}")

    with open(config_path, "r") as f:
        print(f"[INFO] Loading config file: {config_path}", file=sys.stderr)
        _CONFIG_CACHE = yaml.safe_load(f)
    return _CONFIG_CACHE


def get_default_max_hours():
    """Last forecast lead time (hours) from config.yaml ``forecast.max_hours``."""
    return int(load_param_config().get("forecast", {}).get("max_hours", 72))


def get_hour_step():
    """Forecast hour stride from config.yaml ``forecast.hour_step``."""
    return int(load_param_config().get("forecast", {}).get("hour_step", 3))


def get_dataset_hour_step(dataset, hour_step=None):
    """Lead-time stride for a single dataset.

    Uses ``forecast.dataset_hour_step.<dataset>`` when present, else falls back
    to ``hour_step`` (or the global ``forecast.hour_step``). Overrides are meant
    to be coarser than the global step so slow-moving datasets render fewer
    frames while the global 3-hourly schedule stays a superset for the frontend.
    """
    forecast = load_param_config().get("forecast", {})
    overrides = forecast.get("dataset_hour_step") or {}
    if dataset in overrides:
        return max(1, int(overrides[dataset]))
    if hour_step is not None:
        return max(1, int(hour_step))
    return get_hour_step()


def get_forecast_hours(max_hours=None, hour_step=None):
    """Return lead times to render/publish, e.g. [0, 3, 6, …, 72].

    With ``hour_step > 1``, ``max_hours`` is an inclusive end lead time.
    With ``hour_step == 1``, uses ``range(0, max_hours)`` (exclusive end) so
    callers that pass a frame count keep working.
    """
    if max_hours is None:
        max_hours = get_default_max_hours()
    if hour_step is None:
        hour_step = get_hour_step()
    max_hours = int(max_hours)
    hour_step = max(1, int(hour_step))
    if hour_step == 1:
        return list(range(max_hours))
    return list(range(0, max_hours + 1, hour_step))


def get_products():
    """Return the product catalog dict keyed by slug."""
    return load_param_config().get("products", {})


def get_product(slug):
    """Return product metadata for a single slug, or empty dict."""
    return get_products().get(slug, {})


def get_sites():
    """Return site forecast locations as a list of dicts.

    Each entry: ``{id, name, lat, lon}`` from ``sites:`` in config.yaml.
    """
    raw = load_param_config().get("sites") or {}
    sites = []
    for site_id, meta in raw.items():
        if not isinstance(meta, dict):
            continue
        if "lat" not in meta or "lon" not in meta:
            continue
        sites.append(
            {
                "id": str(site_id),
                "name": str(meta.get("name") or site_id),
                "lat": float(meta["lat"]),
                "lon": float(meta["lon"]),
            }
        )
    return sites


def apply_product_config(config, slug, yaml_cfg=None):
    """
    Merge product plot metadata and variable styling onto a PlotConfig instance.
    """
    yaml_cfg = yaml_cfg or load_param_config()

    product = yaml_cfg.get("products", {}).get(slug, {})
    variables = yaml_cfg.get("variables", {}).get(slug, {})

    for source in (product, variables):
        for key, value in source.items():
            if key == "plot" and isinstance(value, dict):
                deep_update(config.plot, value)
            elif hasattr(config, key) and isinstance(getattr(config, key), dict) and isinstance(value, dict):
                deep_update(getattr(config, key), value)
            else:
                setattr(config, key, value)

    return config
