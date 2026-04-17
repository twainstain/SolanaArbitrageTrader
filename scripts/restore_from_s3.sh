#!/usr/bin/env bash
# Restore a Postgres dump from S3 into the local solana-postgres container.
#
# Usage:
#   ./scripts/restore_from_s3.sh daily/solana_arb-2026-04-20T030000Z.sql.gz
#
# REHEARSE THIS on a scratch DB before you need it in anger.
set -eo pipefail

KEY="${1:?Usage: $0 <s3-key>}"

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source <(grep -E '^(POSTGRES_|BACKUP_)' .env)
    set +a
fi

BUCKET="${BACKUP_S3_BUCKET:?set BACKUP_S3_BUCKET}"
CONTAINER="${POSTGRES_CONTAINER:-solana-postgres}"
DB="${POSTGRES_DB:-solana_arb}"
USER="${POSTGRES_USER:-solana}"

case "$KEY" in
  *.sql.gz) PLAIN=true  ;;
  *.dump)   PLAIN=false ;;
  *)
    echo "ERROR: unrecognized dump format: $KEY (expect .sql.gz or .dump)" >&2
    exit 2
    ;;
esac

TMP="/tmp/restore-$(date +%s)"
echo "==> Fetching s3://${BUCKET}/${KEY}"
aws s3 cp "s3://${BUCKET}/${KEY}" "$TMP"

if [ "$PLAIN" = true ]; then
    echo "==> Restoring plain SQL dump via psql (drops recreated via DROP ... IF EXISTS)"
    docker exec -i -e PGPASSWORD="${POSTGRES_PASSWORD}" "$CONTAINER" \
        psql -U "$USER" -d "$DB" < <(gunzip -c "$TMP")
else
    echo "==> Restoring pg_dump custom-format archive"
    docker cp "$TMP" "$CONTAINER:/tmp/restore.dump"
    docker exec -e PGPASSWORD="${POSTGRES_PASSWORD}" "$CONTAINER" pg_restore \
        --clean --if-exists --no-owner --no-privileges \
        -U "$USER" -d "$DB" /tmp/restore.dump
    docker exec "$CONTAINER" rm /tmp/restore.dump
fi

rm -f "$TMP"

echo "==> Post-restore row counts:"
docker exec -e PGPASSWORD="${POSTGRES_PASSWORD}" "$CONTAINER" \
    psql -U "$USER" -d "$DB" -c "
    SELECT schemaname, relname AS tablename, n_live_tup
    FROM pg_stat_user_tables ORDER BY relname;
"
echo "==> Restored $KEY"
