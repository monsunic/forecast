# Nusawave Forecast

A lightweight marine weather forecast platform for Southeast Asia. The site is a static HTML/JS frontend that displays pre-generated maps and point forecasts derived from NOAA GFS (Wave + Atmosphere) and HYCOM ocean model data.

## Architecture

1. **Plotter** (`src/plot.py`) — fetches gridded data from [NOAA NOMADS](https://nomads.ncep.noaa.gov/) (GFS Wave/Atmosphere) and [HYCOM NCSS](https://tds.hycom.org/) (ocean surface), then renders regional forecast maps with Cartopy.
2. **Site Forecast** (`src/site_forecast.py`) — samples the nearest valid grid point for each configured port and emits compact per-site time series (`forecast.json`) plus a downloadable static chart pack (`charts.webp`).
3. **Config generator** (`scripts/generate_config.py`) — scans `assets/maps/` and `assets/sites/`, then writes `assets/config/config.json` (the frontend catalog + service/data status).
4. **Frontend** (`index.html`, `src/main.js`) — static SPA with cascading map dropdowns, a time strip, an interactive Leaflet port map, and lazy-loaded Chart.js site charts.

Generated assets are stored at:

```
assets/maps/{dataset}/{region}/{param}_{forecast_hour}.webp   # dataset ∈ gfswave, gfsatmos, hycom
assets/sites/{site_id}/forecast.json                          # interactive time series
assets/sites/{site_id}/charts.webp                            # downloadable static chart pack
assets/maps/site_countries.geojson                            # tile-free boundaries for the port map
```

## Local development

### Prerequisites

- Python 3.10+ (conda env `nusawave` recommended)
- Cartopy system dependencies (GEOS, PROJ) — install via conda for easiest setup:

```bash
conda create -n nusawave python=3.12 cartopy matplotlib xarray netcdf4 pyyaml pandas numpy pytest -c conda-forge
conda activate nusawave
pip install -r requirements.txt
```

### Generate static assets (base map + logo)

```bash
python scripts/generate_static_assets.py
```

### Run forecast plots manually

Default forecast window is **3 days at 3-hourly** (`forecast.max_hours: 72`, `hour_step: 3` → F000…F072). HYCOM renders 6-hourly via `dataset_hour_step`. Override with `--max-hours` / `MAX_HOURS` and `--hour-step` / `HOUR_STEP`.

```bash
python src/plot.py --dataset gfswave --cycle 2025120700 --region indonesia --max-hours 72 --hour-step 3
python scripts/generate_config.py --dataset gfswave --cycle 2025120700 --max-hours 72 --hour-step 3
```

### Run the Site Forecast manually

Site extraction shares one download per dataset/hour across all ports, so adding ports is cheap. Ports are defined under `sites:` in `plotter/config/config.yaml`.

```bash
python src/site_forecast.py \
  --gfs-cycle 2026072518 --hycom-cycle 2026072421 \
  --max-hours 72 --hour-step 3
# optional: --site singapore --site manila   (repeatable; default = all)
```

Or use the batch script (auto-selects latest available cycles and runs maps + sites):

```bash
bash scripts/run_forecast.sh
```

Environment overrides: `CYCLE`, `HYCOM_CYCLE`, `MAX_HOURS`, `HOUR_STEP`, `REGION`, `DATASETS`.

### Serve the site locally

```bash
python -m http.server 8000
```

Open http://localhost:8000 — select a region, choose **Wind and Waves**, pick a parameter and forecast time.

## Automated updates

GitHub Actions workflow `.github/workflows/forecast.yml` runs on a cron schedule, generates maps + site forecasts, updates `config.json`, and commits results to `main`. The **Status** menu on the site is populated automatically from `config.json` (service state + per-dataset freshness). Enable **GitHub Pages** on the repository root for static hosting.

## Data attribution

- **NOAA GFS Wave** (0.25° global) — wind, significant wave height, swell — via NOMADS HTTPS GRIB2 (`plotter/core/grib_loader.py`). OpenDAP access was retired in February 2026.
- **NOAA GFS Atmosphere** (0.25°) — precipitation, 2 m temperature, humidity, MSLP.
- **HYCOM** — sea-surface temperature and ocean surface currents — via the NCSS endpoint (`plotter/core/hycom_loader.py`).

## Project status

- **Map Forecast** — operational across GFS Wave, GFS Atmosphere, and HYCOM ocean products.
- **Site Forecast** — operational. Interactive Leaflet port map (8 major SEA ports) with per-port charts for waves/wind, ocean, and weather. Includes wind/wave/current **direction vectors**, compass tooltips, human-readable UTC time axes, and a downloadable chart pack that matches the on-screen view.
- **Product catalog** — see [docs/PRODUCT_CATALOG.md](docs/PRODUCT_CATALOG.md) for all defined products, plot types (shaded `contourf`/`pcolormesh`, line `contour`, vector `quiver`/`windbarb`), and deployment status. Each shaded product uses a **Nusawave-branded discrete palette** from [`plotter/core/colormaps.py`](plotter/core/colormaps.py) (not BMKG-style defaults).
- **Route / Observations** — planned (UI placeholders).

### Configured ports (Site Forecast)

Hai Phong · Ho Chi Minh (Saigon) · Laem Chabang · Manila · Port Klang · Singapore · Tanjung Perak · Tanjung Priok

## License

Licensed under **[Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International (CC BY-NC-ND 4.0)](https://creativecommons.org/licenses/by-nc-nd/4.0/)**.

You may view, share, and learn from this repository for **non-commercial** purposes with attribution. You may **not** use it commercially or distribute modified versions.

See [LICENSE](LICENSE) for full terms and [NOTICE](NOTICE) for data and third-party attributions.

For commercial or derivative use: [nusawaveintelligence@gmail.com](mailto:nusawaveintelligence@gmail.com).
