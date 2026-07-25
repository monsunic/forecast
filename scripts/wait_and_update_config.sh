#!/usr/bin/env bash
# Wait for a plot PID to exit, then regenerate config.json.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID="${1:?Usage: wait_and_update_config.sh <plot-pid> [cycle]}"
CYCLE="${2:-}"

cd "$ROOT"

echo "[INFO] Waiting for plot process $PID to finish..."
while kill -0 "$PID" 2>/dev/null; do
    sleep 30
done

default_max_hours() {
    python3 -c "
import sys
sys.path.insert(0, '${ROOT}')
from plotter.core.config_loader import get_default_max_hours
print(get_default_max_hours())
"
}

MAX_HOURS="${MAX_HOURS:-$(default_max_hours)}"

echo "[INFO] Plot finished. Regenerating config..."
if [ -n "$CYCLE" ]; then
    python3 scripts/generate_config.py --cycle "$CYCLE" --max-hours "$MAX_HOURS"
else
    python3 scripts/generate_config.py --max-hours "$MAX_HOURS"
fi

echo "[INFO] Config updated."
python3 scripts/generate_product_catalog.py || true
