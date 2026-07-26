#!/usr/bin/env bash
# Run GFS Wave + Atmosphere + HYCOM Ocean forecast plots and regenerate frontend config.
#
# CI contract
# -----------
# Datasets are plotted independently. When CONTINUE_ON_DATASET_ERROR=1 a failing
# dataset is recorded and the remaining ones still run. Each dataset render is
# staged and promoted only when complete, so failed sources retain their last
# successful maps while successful sources publish their latest cycle.
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
#                           HOUR_STEP is the base/finest stride; per-dataset
#                           overrides (forecast.dataset_hour_step, e.g. HYCOM 6h)
#                           apply only when HOUR_STEP is not set explicitly.
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

# Per-dataset stride: honours forecast.dataset_hour_step overrides (e.g. HYCOM
# at 6-hourly) and falls back to the base step. Overrides are coarser than the
# base, so the base step passed to generate_config stays a valid superset.
dataset_hour_step() {
    python3 -c "
import sys
sys.path.insert(0, '${ROOT}')
from plotter.core.config_loader import get_dataset_hour_step
print(get_dataset_hour_step('$1', $2))
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

# An explicit HOUR_STEP (e.g. a manual dispatch for a dense run) overrides the
# per-dataset config defaults; a blank one lets each dataset use its own stride.
HOUR_STEP_EXPLICIT=0
[[ -n "$HOUR_STEP" ]] && HOUR_STEP_EXPLICIT=1

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

    if [[ "$HOUR_STEP_EXPLICIT" == "1" ]]; then
        ds_step="$HOUR_STEP"
    else
        ds_step="$(dataset_hour_step "$ds" "$HOUR_STEP")"
    fi

    echo "[INFO] Plotting dataset: $ds (cycle $ds_cycle, step ${ds_step}h)"
    # A hung upstream download must not eat the whole job's 6 h budget, which is
    # what silently killed the first 3-hourly production run.
    timeout --signal=TERM --kill-after=60s "${DATASET_TIMEOUT_MINUTES}m" \
        python3 src/plot.py --dataset "$ds" --cycle "$ds_cycle" --region "$REGION" \
        --max-hours "$MAX_HOURS" --hour-step "$ds_step"
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

# Site forecast reuses the same GRIB/NCSS caches as map plots when available.
# Only successful datasets are re-extracted; site_forecast.py keeps the last
# published series for any dataset missing from this run.
SITE_DATASETS=""
for ds in gfswave gfsatmos hycom; do
    for ok in "${SUCCEEDED[@]+"${SUCCEEDED[@]}"}"; do
        if [[ "$ok" == "$ds" ]]; then
            SITE_DATASETS="${SITE_DATASETS:+$SITE_DATASETS,}$ds"
            break
        fi
    done
done

if [[ -n "$SITE_DATASETS" ]]; then
    echo "[INFO] Extracting site forecasts ($SITE_DATASETS)"
    SITE_ARGS=(--gfs-cycle "$GFS_CYCLE" --max-hours "$MAX_HOURS" --hour-step "$HOUR_STEP" --datasets "$SITE_DATASETS")
    if [[ "$SITE_DATASETS" == *"hycom"* && -n "${HYCOM_CYCLE:-}" ]]; then
        SITE_ARGS+=(--hycom-cycle "$HYCOM_CYCLE")
    fi
    if ! python3 src/site_forecast.py "${SITE_ARGS[@]}"; then
        echo "[WARN] Site forecast extraction failed; continuing with maps-only publish" >&2
    fi
else
    echo "[WARN] Skipping site forecast — no successful datasets"
fi

# Always rebuild the frontend config from the maps that exist. Successful
# datasets get a fresh cycle stamp; failed datasets retain the cycle associated
# with their last-published maps.
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
