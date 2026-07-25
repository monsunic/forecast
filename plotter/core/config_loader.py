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
    """Forecast length in hours from config.yaml ``forecast.max_hours``."""
    return int(load_param_config().get("forecast", {}).get("max_hours", 24))


def get_products():
    """Return the product catalog dict keyed by slug."""
    return load_param_config().get("products", {})


def get_product(slug):
    """Return product metadata for a single slug, or empty dict."""
    return get_products().get(slug, {})


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
