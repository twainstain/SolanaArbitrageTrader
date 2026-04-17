#!/bin/bash
# Spot Interruption Monitor
#
# Polls EC2 instance metadata for a spot interruption notice.
# When AWS signals a 2-minute reclaim warning, this script
# gracefully stops the bot via docker compose stop (sends SIGTERM).
#
# Runs as a systemd service — see deploy/cloudformation.yml UserData.

set -euo pipefail

APP_DIR="/opt/arb-trader"
METADATA_URL="http://169.254.169.254/latest/meta-data/spot/instance-action"
POLL_INTERVAL=5

echo "[spot-monitor] Starting — polling every ${POLL_INTERVAL}s"

while true; do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        --connect-timeout 2 --max-time 5 \
        "$METADATA_URL" 2>/dev/null || echo "000")

    if [ "$HTTP_CODE" -eq 200 ]; then
        ACTION=$(curl -s --max-time 5 "$METADATA_URL" 2>/dev/null || echo "unknown")
        echo "[spot-monitor] Spot interruption notice received: $ACTION"
        echo "[spot-monitor] Gracefully stopping bot..."

        cd "$APP_DIR"
        docker compose stop bot 2>/dev/null || true

        echo "[spot-monitor] Bot stopped. Instance will be reclaimed shortly."
        exit 0
    fi

    sleep "$POLL_INTERVAL"
done
