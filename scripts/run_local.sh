#!/bin/bash
# ============================================================
# Run the bot locally mimicking production behavior.
#
# Usage:
#   ./scripts/run_local.sh                # event-driven on-chain (prod mode)
#   ./scripts/run_local.sh --polling      # polling mode (simpler, finite iterations)
#   ./scripts/run_local.sh --stop         # kill running instance
#   ./scripts/run_local.sh --fast         # 8s poll interval (more data, more RPC)
#
# Dashboard: http://localhost:8000/dashboard
# Ops:       http://localhost:8000/ops
# Login:     admin / test
# Logs:      /tmp/arb_bot.log
# Latency:   logs/latency.jsonl
# ============================================================

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

PID_FILE="/tmp/arb_bot_local.pid"
LOG_FILE="/tmp/arb_bot.log"

# --- Stop ---
if [ "${1:-}" = "--stop" ]; then
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            echo "Stopped bot (PID $PID)"
        else
            echo "Bot not running (stale PID $PID)"
        fi
        rm -f "$PID_FILE"
    else
        pkill -f "run_event_driven\|run_live_with_dashboard" 2>/dev/null && echo "Stopped" || echo "No bot running"
    fi
    exit 0
fi

# --- Kill any existing instance ---
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    kill "$OLD_PID" 2>/dev/null && echo "Killed previous instance (PID $OLD_PID)" || true
    rm -f "$PID_FILE"
    sleep 1
fi

# --- Parse args ---
MODE="event-driven"
POLL_INTERVAL=15
for arg in "$@"; do
    case "$arg" in
        --polling)  MODE="polling" ;;
        --fast)     POLL_INTERVAL=8 ;;
    esac
done

# --- Load RPC keys from .env (only RPC_* vars, skip problematic lines) ---
ENV_FILE=".env"
if [ ! -f "$ENV_FILE" ] && [ -f ".env.test" ]; then
    ENV_FILE=".env.test"
fi
if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        # Skip comments and empty lines
        case "$key" in \#*|"") continue ;; esac
        # Only import RPC_* vars (we override everything else below)
        case "$key" in RPC_*) export "$key=$value" ;; esac
    done < "$ENV_FILE"
fi

# Override: local SQLite, no alerts, test credentials
export DATABASE_URL=""
export DASHBOARD_PASS="test"
export DISCORD_WEBHOOK_URL=""
export TELEGRAM_BOT_TOKEN=""
export GMAIL_ADDRESS=""
export DASHBOARD_URL="http://localhost:8000/dashboard"

# Use public Optimism RPC if Infura is configured (rate-limited)
case "${RPC_OPTIMISM:-}" in
    *infura.io*) export RPC_OPTIMISM="https://mainnet.optimism.io" ;;
esac

# --- Ensure dirs ---
mkdir -p logs data

echo "============================================================"
echo "  Arbitrage Trader — Local ${MODE} Mode"
echo "============================================================"
echo "  Dashboard: http://localhost:8000/dashboard"
echo "  Ops:       http://localhost:8000/ops"
echo "  Login:     admin / test"
echo "  Poll:      ${POLL_INTERVAL}s"
echo "  Logs:      $LOG_FILE"
echo "============================================================"

> "$LOG_FILE"

if [ "$MODE" = "event-driven" ]; then
    PYTHONPATH=src nohup python3.11 -m run_event_driven \
        --config config/multichain_onchain_config.json \
        --port 8000 \
        --poll-interval "$POLL_INTERVAL" \
        > "$LOG_FILE" 2>&1 &
else
    PYTHONPATH=src nohup python3.11 -m run_live_with_dashboard \
        --onchain \
        --config config/multichain_onchain_config.json \
        --iterations 50 \
        --sleep "$POLL_INTERVAL" \
        --port 8000 \
        > "$LOG_FILE" 2>&1 &
fi

echo $! > "$PID_FILE"
echo "Started (PID $!)"
echo ""
echo "Tail logs:   tail -f $LOG_FILE"
echo "Stop:        ./scripts/run_local.sh --stop"
