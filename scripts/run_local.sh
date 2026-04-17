#!/bin/bash
# ============================================================
# Run the SolanaTrader scanner locally.
#
# Usage:
#   ./scripts/run_local.sh                # Jupiter live mode (reads .env)
#   ./scripts/run_local.sh --sim          # simulated market (offline)
#   ./scripts/run_local.sh --test         # force .env.test (local SQLite, safe)
#   ./scripts/run_local.sh --sim --test   # safest: offline + test env
#   ./scripts/run_local.sh --stop         # kill running instance
#
# Logs:      /tmp/solana_arb_bot.log
# Latency:   logs/latency.jsonl
# DB:        data/solana_arb.db
# ============================================================

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

PID_FILE="/tmp/solana_arb_bot.pid"
LOG_FILE="/tmp/solana_arb_bot.log"
CONFIG="config/example_config.json"

# --- Stop ---
if [ "${1:-}" = "--stop" ]; then
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            echo "Stopped scanner (PID $PID)"
        else
            echo "Scanner not running (stale PID $PID)"
        fi
        rm -f "$PID_FILE"
    else
        pkill -f "run_event_driven" 2>/dev/null && echo "Stopped" || echo "No scanner running"
    fi
    exit 0
fi

# --- Kill existing instance ---
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    kill "$OLD_PID" 2>/dev/null && echo "Killed previous instance (PID $OLD_PID)" || true
    rm -f "$PID_FILE"
    sleep 1
fi

# --- Parse args ---
MODE="jupiter"
USE_TEST_ENV=0
for arg in "$@"; do
    case "$arg" in
        --sim)        MODE="simulated" ;;
        --test)       USE_TEST_ENV=1 ;;
        --config=*)   CONFIG="${arg#--config=}" ;;
    esac
done

# --- Load env vars ---
# Default priority: .env (real secrets) → .env.test (committed defaults).
# --test forces .env.test even when .env exists (useful when .env points
# at a legacy/prod DB and you want a clean local SQLite run).
# .env.example is NEVER loaded — it's a documentation template only.
if [ "$USE_TEST_ENV" = "1" ] && [ -f ".env.test" ]; then
    ENV_FILE=".env.test"
else
    ENV_FILE=".env"
    [ -f "$ENV_FILE" ] || ENV_FILE=".env.test"
fi
if [ -f "$ENV_FILE" ]; then
    echo "Loading env from: $ENV_FILE"
    while IFS='=' read -r key value; do
        case "$key" in \#*|"") continue ;; esac
        # Strip inline comments ONLY when preceded by whitespace (URLs can contain #)
        case "$value" in *' #'*|*'	#'*) value="${value%% #*}"; value="${value%%	#*}";; esac
        # Trim leading/trailing whitespace without invoking xargs (URLs with
        # unmatched quote chars were breaking xargs' argument parsing).
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        case "$key" in
            SOLANA_*|HELIUS_*|JUPITER_*|BOT_*|DATABASE_URL|DASHBOARD_*)
                export "$key=$value" ;;
        esac
    done < "$ENV_FILE"
fi

mkdir -p logs data

echo "============================================================"
echo "  SolanaTrader — Local scanner (${MODE})"
echo "============================================================"
echo "  Dashboard: http://localhost:8000/dashboard"
echo "  Ops:       http://localhost:8000/ops"
echo "  Analytics: http://localhost:8000/analytics"
echo "  Login:     ${DASHBOARD_USER:-admin} / ${DASHBOARD_PASS:-adminTest}"
echo "  Config:    $CONFIG"
echo "  Logs:      $LOG_FILE"
echo "  Latency:   logs/latency.jsonl"
echo "============================================================"

> "$LOG_FILE"

# Pick the first Python (3.11+) that has our runtime deps installed.
# 3.13/3.12 may be newer but often lack `requests`/`dotenv` — skip them silently.
PYTHON=""
for candidate in python3.11 python3.12 python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && \
       "$candidate" -c "import requests, dotenv" >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "No python3 (>=3.11) with 'requests' + 'python-dotenv' installed." >&2
    echo "Fix: python3.11 -m pip install -e ." >&2
    exit 1
fi
echo "Using Python:   $PYTHON"

# Include both the project src/ and the trading_platform shared library.
PYTHONPATH="src:lib/trading_platform/src" nohup "$PYTHON" -m run_event_driven \
    --config "$CONFIG" \
    --mode "$MODE" \
    --port 8000 \
    > "$LOG_FILE" 2>&1 &

echo $! > "$PID_FILE"
echo "Started (PID $!)"
echo ""
echo "Tail logs: tail -f $LOG_FILE"
echo "Stop:      ./scripts/run_local.sh --stop"
