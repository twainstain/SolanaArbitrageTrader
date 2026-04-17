#!/usr/bin/env bash
# Start SolanaTrader on the production EC2 (docker-compose).
#
# Intended to run on the host directly (cron / systemd).  For local
# dev use ./scripts/run_local.sh instead.
#
# Usage: ./scripts/start.sh
set -eo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -f .env ]]; then
    echo "ERROR: .env missing in $PROJECT_DIR" >&2
    exit 1
fi

# Pull the latest image tag (if running from ECR).  Ignore failure — the
# compose up will fall back to the local build if the registry is unreachable.
docker compose pull bot || true

# Start (or restart) just the bot service.  Monitoring sidecars are
# opt-in via `docker compose --profile monitoring up -d`.
docker compose up -d bot

echo "SolanaTrader started."
docker compose ps bot
