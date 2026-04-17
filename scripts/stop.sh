#!/usr/bin/env bash
# Stop SolanaTrader on the production EC2 (docker-compose).
#
# SIGTERM first (handler in run_event_driven drains the queue), then
# SIGKILL after the compose default timeout (10s).
#
# Usage: ./scripts/stop.sh
set -eo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

docker compose stop bot
echo "SolanaTrader stopped."
