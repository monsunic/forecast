#!/usr/bin/env bash
# Run GFS Wave + Atmosphere + HYCOM Ocean forecast plots and regenerate frontend config.
#
# CI contract
# -----------
# Datasets are plotted independently. When CONTINUE_ON_DATASET_ERROR=1 a failing
# dataset is recorded and the remaining ones still run, and the frontend config is
# always regenerated from whatever maps ended up on disk.
#
# Exit status is deliberately 0 when *at least one* dataset succeeded: the
# workflow's commit step then publishes the partial refresh instead of throwing
# away hours of good plots because one upstream server misbehaved. Exit 1 is
# reserved for "nothing usable was produced" — every dataset failed, or the
# config/catalog regeneration itself broke.
#
# Notable env vars (blank is treated as unset):
#   CYCLE, HYCOM_CYCLE      model cycles, YYYYMMDDHH   (default: auto-pick latest)
#   MAX_HOURS, HOUR_STEP    lead-time window           (default: plotter/config/config.yaml)
#   REGION                  region id or "all"         (default: all)
#   DATASETS                space/comma separated      (default: gfswave gfsatmos hycom)
#   CONTINUE_ON_DATASET_ERROR=1     keep going after a dataset fails
#   DATASET_TIMEOUT_MINUTES=100     wall-clock cap per dataset

# No `set -e`: dataset failures are handled explicitly below.
set -uo pipefail

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

# Strip surrounding whitespace so an empty workflow_dispatch input (which arrives
# as "" or a stray newline) falls back to the auto-detected value.
trim() {
    local s="${1-}"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

require() {
    local name="$1" value="$2"
    if [[ -z "$value" ]]; then
        echo "[ERROR] Could not determine $name (auto-detection failed and no value supplied)" >&2
        exit 1
    fi
}

CYCLE="$(trim "${CYCLE:-}")"
HYCOM_CYCLE="$(trim "${HYCOM_CYCLE:-}")"
MAX_HOURS="$(trim "${MAX_HOURS:-}")"
HOUR_STEP="$(trim "${HOUR_STEP:-}")"
REGION="$(trim "${REGION:-}")"
DATASETS="$(trim "${DATASETS:-}")"
CONTINUE_ON_DATASET_ERROR="$(trim "${CONTINUE_ON_DATASET_ERROR:-0}")"
DATASET_TIMEOUT_MINUTES="$(trim "${DATASET_TIMEOUT_MINUTES:-100}")"

GFS_CYCLE="${CYCLE:-$(pick_gfs_cycle)}"
HYCOM_CYCLE="${HYCOM_CYCLE:-$(pick_hycom_cycle)}"
MAX_HOURS="${MAX_HOURS:-$(default_max_hours)}"
HOUR_STEP="${HOUR_STEP:-$(default_hour_step)}"
REGION="${REGION:-all}"
DATASETS="${DATASETS:-gfswave gfsatmos hycom}"
DATASETS="${DATASETS//,/ }"

require "GFS cycle" "$GFS_CYCLE"
require "max hours" "$MAX_HOURS"
require "hour step" "$HOUR_STEP"

echo "[INFO] GFS cycle: $GFS_CYCLE, HYCOM cycle: ${HYCOM_CYCLE:-<unavailable>}, forecast: F000…F$(printf '%03d' "$MAX_HOURS") step ${HOUR_STEP}h, datasets: $DATASETS"

CYCLE_ARGS=()
SUCCEEDED=()
FAILED=()

for ds in $DATASETS; do
    if [[ "$ds" == "hycom" ]]; then
        ds_cycle="$HYCOM_CYCLE"
    else
        ds_cycle="$GFS_CYCLE"
    fi

    if [[ -z "$ds_cycle" ]]; then
        echo "[ERROR] No cycle available for dataset $ds — skipping" >&2
        FAILED+=("$ds")
        continue
    fi

    echo "[INFO] Plotting dataset: $ds (cycle $ds_cycle)"
    # A hung upstream download must not eat the whole job's 6 h budget, which is
    # what silently killed the first 3-hourly production run.
    timeout --signal=TERM --kill-after=60s "${DATASET_TIMEOUT_MINUTES}m" \
        python3 src/plot.py --dataset "$ds" --cycle "$ds_cycle" --region "$REGION" \
        --max-hours "$MAX_HOURS" --hour-step "$HOUR_STEP"
    status=$?

    if [[ $status -eq 0 ]]; then
        echo "[INFO] Dataset $ds completed"
        SUCCEEDED+=("$ds")
        CYCLE_ARGS+=(--cycle-for "$ds=$ds_cycle")
        continue
    fi

    if [[ $status -eq 124 || $status -eq 137 ]]; then
        echo "[ERROR] Dataset $ds timed out after ${DATASET_TIMEOUT_MINUTES}m" >&2
    else
        echo "[ERROR] Dataset $ds failed with exit code $status" >&2
    fi
    FAILED+=("$ds")

    if [[ "$CONTINUE_ON_DATASET_ERROR" != "1" ]]; then
        echo "[ERROR] CONTINUE_ON_DATASET_ERROR is not set — aborting remaining datasets" >&2
        break
    fi
done

# Always rebuild the frontend config from the maps that exist, so a partial run
# still ships a coherent site. Only successful datasets get a fresh cycle stamp.
echo "[INFO] Regenerating frontend config and product catalog"
python3 scripts/generate_config.py ${CYCLE_ARGS[@]+"${CYCLE_ARGS[@]}"} \
    --max-hours "$MAX_HOURS" --hour-step "$HOUR_STEP" || exit 1
python3 scripts/generate_product_catalog.py || exit 1

echo "[INFO] Succeeded: ${SUCCEEDED[*]:-none}"
echo "[INFO] Failed: ${FAILED[*]:-none}"

if [[ ${#SUCCEEDED[@]} -eq 0 ]]; then
    echo "[ERROR] No dataset produced maps" >&2
    exit 1
fi

if [[ ${#FAILED[@]} -gt 0 ]]; then
    echo "[WARN] Publishing a partial refresh; failed datasets keep their previous maps" >&2
fi
