#!/bin/bash
# Start the arbitrage trader (event-driven mode with dashboard).
# Called by cron: Saturday 10 PM EST (Sunday 03:00 UTC).
#
# Usage: ./scripts/start.sh
# Cron:  0 3 * * 0 cd /opt/arbitrage/ArbitrageTrader && ./scripts/start.sh

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

# Don't start if already running.
PID_FILE="/tmp/arbitrage-trader.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Already running (PID $OLD_PID). Stop first with ./scripts/stop.sh"
        exit 1
    fi
    rm -f "$PID_FILE"
fi

# Ensure logs directory exists.
mkdir -p logs

# Load .env if present.
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="logs/trader_${TIMESTAMP}.log"

echo "Starting arbitrage trader..."
echo "  Config: config/multichain_onchain_config.json"
echo "  Dashboard: http://localhost:8000/dashboard"
echo "  Log: $LOG_FILE"
echo "  Mode: DRY-RUN (execution_enabled=false)"

# Run in background.
PYTHONPATH=src nohup python -m run_event_driven \
    --config config/multichain_onchain_config.json \
    --port 8000 \
    --poll-interval 8 \
    > "$LOG_FILE" 2>&1 &

PID=$!
echo "$PID" > "$PID_FILE"
echo "Started (PID $PID). Logs: tail -f $LOG_FILE"
echo "Stop with: ./scripts/stop.sh"
