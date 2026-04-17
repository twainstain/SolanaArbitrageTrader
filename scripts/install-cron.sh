#!/usr/bin/env bash
# ============================================================
# Install production cron jobs on the EC2 host.
# ============================================================
#
# Schedule:
#   03:00 UTC — nightly S3 backup (pg_dump → gzip → s3 cp)
#   03:30 UTC — daily analysis report (emailed via Gmail backend if configured)
#
# Uses the user-level crontab of the current user (ubuntu on prod EC2).
#
# Usage:
#   ./scripts/install-cron.sh            # add/update the two jobs
#   ./scripts/install-cron.sh --remove   # remove them
# ============================================================

set -eo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_JOB="0 3 * * * $PROJECT_DIR/scripts/backup_to_s3.sh >> $PROJECT_DIR/logs/backup.log 2>&1"
ANALYSIS_JOB="30 3 * * * $PROJECT_DIR/scripts/daily_analysis.py >> $PROJECT_DIR/logs/analysis.log 2>&1"
MARKER="# solana-trader cron"

if [[ "${1:-}" == "--remove" ]]; then
    crontab -l 2>/dev/null | grep -v "${MARKER}" | crontab -
    echo "Removed solana-trader cron entries."
    exit 0
fi

# Append-or-replace: strip any existing solana-trader entries, then re-add.
(crontab -l 2>/dev/null | grep -v "${MARKER}" ; cat <<EOF
${BACKUP_JOB}    ${MARKER}
${ANALYSIS_JOB}    ${MARKER}
EOF
) | crontab -

echo "Installed cron entries:"
crontab -l | grep "${MARKER}"
