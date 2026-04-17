#!/usr/bin/env bash
# ============================================================
# Nightly Postgres backup → gzip → S3.
# ============================================================
#
# Designed to run on the production EC2 via cron.  Uses the EC2
# instance IAM role for S3 auth (no creds in .env).
#
# Requirements on the host:
#   - awscli installed (apt-get install -y awscli  or  via docker)
#   - docker compose stack running (postgres service up)
#   - EC2 IAM role with: s3:PutObject on $BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/*
#
# Env vars (from /opt/solana-trader/.env):
#   POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
#   BACKUP_S3_BUCKET      bucket name (no s3:// prefix)
#   BACKUP_S3_PREFIX      e.g. "daily"
#   BACKUP_RETENTION_DAYS default 30
#
# Usage (cron or manual):
#   /opt/solana-trader/scripts/backup_to_s3.sh
# ============================================================

set -eo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

# Pull env vars we need without polluting PATH/other bash state.
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source <(grep -E '^(POSTGRES_|BACKUP_)' .env)
    set +a
fi

: "${POSTGRES_DB:=solana_arb}"
: "${POSTGRES_USER:=solana}"
: "${BACKUP_S3_PREFIX:=daily}"
: "${BACKUP_RETENTION_DAYS:=30}"

if [[ -z "${BACKUP_S3_BUCKET:-}" ]]; then
    echo "ERROR: BACKUP_S3_BUCKET not set in .env" >&2
    exit 1
fi

TS="$(date -u +%Y-%m-%dT%H%M%SZ)"
DUMP_NAME="solana_arb-${TS}.sql.gz"
LOCAL_PATH="/tmp/${DUMP_NAME}"
S3_URI="s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX}/${DUMP_NAME}"

echo "[backup] starting pg_dump at ${TS}"

# pg_dump from inside the postgres container so we don't need pg_dump on the host.
docker exec -e PGPASSWORD="${POSTGRES_PASSWORD}" solana-postgres \
    pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --format=plain --no-owner \
    | gzip -9 > "${LOCAL_PATH}"

SIZE_BYTES="$(stat -c%s "${LOCAL_PATH}" 2>/dev/null || stat -f%z "${LOCAL_PATH}")"
echo "[backup] dump size: ${SIZE_BYTES} bytes"

if [[ "${SIZE_BYTES}" -lt 1024 ]]; then
    echo "ERROR: dump suspiciously small (<1KB) — aborting upload" >&2
    rm -f "${LOCAL_PATH}"
    exit 1
fi

aws s3 cp "${LOCAL_PATH}" "${S3_URI}" --only-show-errors --storage-class STANDARD_IA
echo "[backup] uploaded to ${S3_URI}"
rm -f "${LOCAL_PATH}"

# Prune: delete S3 objects older than BACKUP_RETENTION_DAYS.
CUTOFF_SECS="$(date -u -d "-${BACKUP_RETENTION_DAYS} days" +%s 2>/dev/null \
               || date -u -v "-${BACKUP_RETENTION_DAYS}d" +%s)"
PRUNED=0
while read -r line; do
    # aws s3 ls output: "2026-04-01 03:00:00     12345 solana_arb-2026-04-01T030000Z.sql.gz"
    KEY="$(echo "$line" | awk '{print $NF}')"
    DATE_PART="$(echo "$KEY" | sed -n 's/solana_arb-\(.*\)T.*/\1/p')"
    if [[ -z "$DATE_PART" ]]; then continue; fi
    KEY_SECS="$(date -u -d "$DATE_PART" +%s 2>/dev/null || date -u -j -f "%Y-%m-%d" "$DATE_PART" +%s 2>/dev/null || echo 0)"
    if (( KEY_SECS > 0 && KEY_SECS < CUTOFF_SECS )); then
        aws s3 rm "s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX}/${KEY}" --only-show-errors
        PRUNED=$((PRUNED + 1))
    fi
done < <(aws s3 ls "s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX}/" | grep solana_arb-)

if [[ "$PRUNED" -gt 0 ]]; then
    echo "[backup] pruned ${PRUNED} old backup(s) (older than ${BACKUP_RETENTION_DAYS}d)"
fi

echo "[backup] done ${TS}"
