#!/usr/bin/env bash
# Run GFS Wave + Atmosphere forecast plots and regenerate frontend config.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONUNBUFFERED=1

pick_cycle() {
    python3 -c "
import sys
sys.path.insert(0, '${ROOT}')
from plotter.core.grib_loader import pick_latest_gfs_cycle
print(pick_latest_gfs_cycle())
"
}

default_max_hours() {
    python3 -c "
import sys
sys.path.insert(0, '${ROOT}')
from plotter.core.config_loader import get_default_max_hours
print(get_default_max_hours())
"
}

CYCLE="${CYCLE:-$(pick_cycle)}"
MAX_HOURS="${MAX_HOURS:-$(default_max_hours)}"
REGION="${REGION:-all}"
DATASETS="${DATASETS:-gfswave gfsatmos}"

echo "[INFO] Using cycle: $CYCLE, forecast hours: $MAX_HOURS, datasets: $DATASETS"
for ds in $DATASETS; do
    echo "[INFO] Plotting dataset: $ds"
    python3 src/plot.py --dataset "$ds" --cycle "$CYCLE" --region "$REGION" --max-hours "$MAX_HOURS"
done

# Combined config for every dataset that has maps on disk.
python3 scripts/generate_config.py --cycle "$CYCLE" --max-hours "$MAX_HOURS"
python3 scripts/generate_product_catalog.py
