#!/bin/bash
# run_daily.sh — Daily Rachio collection + dashboard generation
# Run from the rachio-dashboard directory or anywhere via absolute path

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="/workspace/.venv"
LOG_FILE="${SCRIPT_DIR}/data/rundaily.log"

mkdir -p "${SCRIPT_DIR}/data" "${SCRIPT_DIR}/dashboard"

echo "=== $(date) Starting Rachio daily run ===" >> "$LOG_FILE"

# Activate virtual environment
source "${VENV_PATH}/bin/activate"

# Run collector
cd "$SCRIPT_DIR"
python -c "
import sys, logging
sys.path.insert(0, '.')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
from rachio.collector import run as collector_run
from rachio.daily_report import generate_full_report

states = collector_run()
print(f'Collected state for {len(states)} zones')
report = generate_full_report(states)
print(f'Report generated: {len(report.zones)} zones, {report.total_monthly_gallons:.0f} gal total')
" >> "$LOG_FILE" 2>&1

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "=== $(date) Daily run completed successfully ===" >> "$LOG_FILE"
else
    echo "=== $(date) Daily run FAILED with exit code $EXIT_CODE ===" >> "$LOG_FILE"
    exit $EXIT_CODE
fi
