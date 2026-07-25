#!/usr/bin/env bash
# Run GFS Wave + Atmosphere + HYCOM Ocean forecast plots and regenerate frontend config.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONUNBUFFERED=1

pick_gfs_cycle() {
    python3 -c "
import sys
sys.path.insert(0, '${ROOT}')
from plotter.core.grib_loader import pick_latest_gfs_cycle
print(pick_latest_gfs_cycle())
"
}

pick_hycom_cycle() {
    python3 -c "
import sys
sys.path.insert(0, '${ROOT}')
from plotter.core.hycom_loader import pick_latest_hycom_cycle
print(pick_latest_hycom_cycle())
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

default_hour_step() {
    python3 -c "
import sys
sys.path.insert(0, '${ROOT}')
from plotter.core.config_loader import get_hour_step
print(get_hour_step())
"
}

GFS_CYCLE="${CYCLE:-$(pick_gfs_cycle)}"
HYCOM_CYCLE="${HYCOM_CYCLE:-$(pick_hycom_cycle)}"
MAX_HOURS="${MAX_HOURS:-$(default_max_hours)}"
HOUR_STEP="${HOUR_STEP:-$(default_hour_step)}"
REGION="${REGION:-all}"
DATASETS="${DATASETS:-gfswave gfsatmos hycom}"

echo "[INFO] GFS cycle: $GFS_CYCLE, HYCOM cycle: $HYCOM_CYCLE, forecast: F000…F$(printf '%03d' "$MAX_HOURS") step ${HOUR_STEP}h, datasets: $DATASETS"

CYCLE_ARGS=()
for ds in $DATASETS; do
    if [[ "$ds" == "hycom" ]]; then
        ds_cycle="$HYCOM_CYCLE"
    else
        ds_cycle="$GFS_CYCLE"
    fi
    echo "[INFO] Plotting dataset: $ds (cycle $ds_cycle)"
    python3 src/plot.py --dataset "$ds" --cycle "$ds_cycle" --region "$REGION" \
        --max-hours "$MAX_HOURS" --hour-step "$HOUR_STEP"
    CYCLE_ARGS+=(--cycle-for "$ds=$ds_cycle")
done

# Combined config for every dataset that has maps on disk.
python3 scripts/generate_config.py "${CYCLE_ARGS[@]}" --max-hours "$MAX_HOURS" --hour-step "$HOUR_STEP"
python3 scripts/generate_product_catalog.py
