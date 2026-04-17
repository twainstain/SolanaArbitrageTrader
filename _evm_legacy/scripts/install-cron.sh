#!/bin/bash
# Install cron jobs for the trading schedule:
#   Start: Saturday 10 PM EST = Sunday 03:00 UTC
#   Stop:  Friday 1 PM EST   = Friday 18:00 UTC
#
# Usage: ./scripts/install-cron.sh

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Build cron entries.
START_CRON="0 3 * * 0 cd $PROJECT_DIR && ./scripts/start.sh >> logs/cron.log 2>&1"
STOP_CRON="0 18 * * 5 cd $PROJECT_DIR && ./scripts/stop.sh >> logs/cron.log 2>&1"

# Check if already installed.
EXISTING=$(crontab -l 2>/dev/null || true)
if echo "$EXISTING" | grep -q "arbitrage-trader"; then
    echo "Cron jobs already installed. To reinstall, run: crontab -e"
    echo "Current schedule:"
    echo "$EXISTING" | grep "arbitrage"
    exit 0
fi

# Append to existing crontab.
(
    echo "$EXISTING"
    echo ""
    echo "# --- Arbitrage Trader Schedule (EST) ---"
    echo "# Start: Saturday 10 PM EST (Sunday 03:00 UTC)"
    echo "$START_CRON  # arbitrage-trader-start"
    echo "# Stop: Friday 1 PM EST (Friday 18:00 UTC)"
    echo "$STOP_CRON  # arbitrage-trader-stop"
) | crontab -

echo "Cron jobs installed:"
echo "  START: Sunday 03:00 UTC (Saturday 10 PM EST)"
echo "  STOP:  Friday 18:00 UTC (Friday 1 PM EST)"
echo ""
echo "Verify with: crontab -l"
echo "Logs: $PROJECT_DIR/logs/cron.log"
