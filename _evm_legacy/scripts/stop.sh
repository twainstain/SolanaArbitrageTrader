#!/bin/bash
# Stop the arbitrage trader gracefully.
# Called by cron: Friday 1 PM EST (Friday 18:00 UTC).
#
# Usage: ./scripts/stop.sh
# Cron:  0 18 * * 5 cd /opt/arbitrage/ArbitrageTrader && ./scripts/stop.sh

set -e

PID_FILE="/tmp/arbitrage-trader.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "No PID file found — trader may not be running."
    # Try to find and kill by process name as fallback.
    pkill -f "run_event_driven" 2>/dev/null && echo "Killed by process name." || echo "Nothing to stop."
    exit 0
fi

PID=$(cat "$PID_FILE")

if kill -0 "$PID" 2>/dev/null; then
    echo "Stopping trader (PID $PID)..."
    # Send SIGTERM for graceful shutdown (saves final metrics + queue stats).
    kill -TERM "$PID"

    # Wait up to 30 seconds for graceful shutdown.
    for i in $(seq 1 30); do
        if ! kill -0 "$PID" 2>/dev/null; then
            echo "Stopped gracefully after ${i}s."
            rm -f "$PID_FILE"
            exit 0
        fi
        sleep 1
    done

    # Force kill if still running.
    echo "Force killing (PID $PID)..."
    kill -9 "$PID" 2>/dev/null
    rm -f "$PID_FILE"
    echo "Force killed."
else
    echo "PID $PID not running. Cleaning up."
    rm -f "$PID_FILE"
fi
