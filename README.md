# Nusawave Forecast

A lightweight marine weather forecast platform for Southeast Asia. The site is a static HTML/JS frontend that displays pre-generated map images from NOAA GFS Wave model data.

## Architecture

1. **Plotter** (`src/plot.py`) — fetches GFS Wave data from [NOAA NOMADS](https://nomads.ncep.noaa.gov/) and renders regional forecast maps with Cartopy.
2. **Config generator** (`scripts/generate_config.py`) — scans `assets/maps/` and writes `assets/config/config.json` for the frontend.
3. **Frontend** (`index.html`, `src/main.js`) — static SPA with cascading dropdowns and time animation controls.

Maps are stored at:

```
assets/maps/gfswave/{region}/{param}_{forecast_hour}.webp
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

Default forecast window is **24 hours** (`forecast.max_hours` in `plotter/config/config.yaml`). Override with `--max-hours` / `MAX_HOURS`.

```bash
python src/plot.py --dataset gfswave --cycle 2025120700 --region indonesia --max-hours 24
python scripts/generate_config.py --dataset gfswave --cycle 2025120700 --max-hours 24
```

Or use the batch script (auto-selects latest available GFS cycle):

```bash
bash scripts/run_forecast.sh
```

Environment overrides: `CYCLE`, `MAX_HOURS`, `REGION`.

### Serve the site locally

```bash
python -m http.server 8000
```

Open http://localhost:8000 — select a region, choose **Wind and Waves**, pick a parameter and forecast time.

## Automated updates

GitHub Actions workflow `.github/workflows/forecast.yml` runs on a cron schedule, generates maps, updates `config.json`, and commits results to `main`. Enable **GitHub Pages** on the repository root for static hosting.

## Data attribution

Forecast maps use **NOAA GFS Wave** (0.25° global) via NOMADS HTTPS GRIB2 downloads. OpenDAP access was retired in February 2026; this project uses the GRIB2 path under `plotter/core/grib_loader.py`.

## Project status (MVP)

- **Map Forecast** — GFS Wave (wind, significant wave height, swell)
- **Product catalog** — see [docs/PRODUCT_CATALOG.md](docs/PRODUCT_CATALOG.md) for all defined products, plot types (shaded `contourf`/`pcolormesh`, line `contour`, vector `quiver`/`windbarb`), and deployment status. Each shaded product uses a **Nusawave-branded discrete palette** from [`plotter/core/colormaps.py`](plotter/core/colormaps.py) (not BMKG-style defaults).
- **Site / Route / Observations** — planned (UI placeholders)

## License

Licensed under **[Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International (CC BY-NC-ND 4.0)](https://creativecommons.org/licenses/by-nc-nd/4.0/)**.

You may view, share, and learn from this repository for **non-commercial** purposes with attribution. You may **not** use it commercially or distribute modified versions.

See [LICENSE](LICENSE) for full terms and [NOTICE](NOTICE) for data and third-party attributions.

For commercial or derivative use: [nusawaveintelligence@gmail.com](mailto:nusawaveintelligence@gmail.com).
