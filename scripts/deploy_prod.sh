#!/usr/bin/env bash
# ============================================================
# Deploy SolanaTrader to the production EC2 instance.
# ============================================================
#
# Usage:
#   ./scripts/deploy_prod.sh            # rsync code, build, restart bot
#   ./scripts/deploy_prod.sh --status   # show health + dashboard + wallet
#   ./scripts/deploy_prod.sh --logs     # tail container logs
#   ./scripts/deploy_prod.sh --sync-env # upload local .env to remote
#   ./scripts/deploy_prod.sh --migrate  # run migrate_db.py remotely
#
# Prerequisites:
#   - SSH key at SSH_KEY (see below) or override via env var
#   - EC2 instance reachable at SOLANA_EC2_HOST (env override allowed)
# ============================================================

set -eo pipefail

EC2_HOST="${SOLANA_EC2_HOST:-arb-trader-solana.yeda-ai.com}"
SSH_KEY="${SOLANA_EC2_KEY:-$HOME/.ssh/arb-trader-solana.pem}"
SSH_USER="${SOLANA_EC2_USER:-ubuntu}"
REMOTE_DIR="${SOLANA_REMOTE_DIR:-/opt/solana-trader}"
SSH_CMD="ssh -i $SSH_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=10 ${SSH_USER}@${EC2_HOST}"
RSYNC_BASE="rsync -az --delete -e \"ssh -i $SSH_KEY -o StrictHostKeyChecking=no\""

GREEN="\033[92m"
RED="\033[91m"
YELLOW="\033[93m"
RESET="\033[0m"

check_ssh() {
    if [[ ! -f "$SSH_KEY" ]]; then
        echo -e "${RED}SSH key not found: $SSH_KEY${RESET}"
        echo "Set SOLANA_EC2_KEY to override."
        exit 1
    fi
    if ! $SSH_CMD "echo ok" &>/dev/null; then
        echo -e "${RED}Cannot reach ${SSH_USER}@${EC2_HOST} via SSH${RESET}"
        exit 1
    fi
}

cmd_status() {
    check_ssh
    echo ""
    echo "============================================================"
    echo "  SolanaTrader production status"
    echo "============================================================"
    PASS=$($SSH_CMD "grep '^DASHBOARD_PASS=' $REMOTE_DIR/.env | cut -d= -f2 | tr -d \"'\"")

    echo ""
    printf "  Health: "
    HEALTH=$($SSH_CMD "curl -sf -u admin:$PASS http://localhost:8000/health" 2>/dev/null || true)
    if [[ -n "$HEALTH" ]]; then
        echo -e "${GREEN}OK${RESET} — $HEALTH"
    else
        echo -e "${RED}DOWN${RESET}"
    fi

    echo ""
    echo "  Wallet:"
    $SSH_CMD "curl -sf -u admin:$PASS http://localhost:8000/wallet/balance" 2>/dev/null \
        | python3 -c "import sys, json; d = json.load(sys.stdin); print('    pubkey:', d.get('address') or '—'); print('    SOL:   ', d.get('balances', {}).get('SOL', '—'))" \
        || echo "    (could not read wallet)"

    echo ""
    echo "  Containers:"
    $SSH_CMD "cd $REMOTE_DIR && docker compose ps" 2>/dev/null || true
}

cmd_logs() {
    check_ssh
    $SSH_CMD "cd $REMOTE_DIR && docker compose logs -f --tail=200 bot"
}

cmd_sync_env() {
    check_ssh
    if [[ ! -f .env ]]; then
        echo -e "${RED}Local .env missing.${RESET}"
        exit 1
    fi
    echo -e "${YELLOW}Uploading .env to ${EC2_HOST}:${REMOTE_DIR}/.env${RESET}"
    eval "$RSYNC_BASE .env ${SSH_USER}@${EC2_HOST}:${REMOTE_DIR}/.env"
    $SSH_CMD "chmod 600 $REMOTE_DIR/.env"
    echo -e "${GREEN}Done.${RESET}"
}

cmd_migrate() {
    check_ssh
    echo -e "${YELLOW}Running migrate_db.py on ${EC2_HOST}${RESET}"
    $SSH_CMD "cd $REMOTE_DIR && docker compose run --rm bot python3 scripts/migrate_db.py"
}

cmd_deploy() {
    check_ssh

    # 1. Push code (src/ config/ scripts/ lib/ plus root files)
    echo -e "${YELLOW}[1/3]${RESET} rsync code to ${EC2_HOST}:${REMOTE_DIR}"
    eval "$RSYNC_BASE --exclude=.git --exclude=__pycache__ --exclude=data --exclude=logs \
        --exclude=.env --exclude=_evm_legacy \
        ./ ${SSH_USER}@${EC2_HOST}:${REMOTE_DIR}/"

    # 2. Build + restart compose.
    echo -e "${YELLOW}[2/3]${RESET} build + restart bot container"
    $SSH_CMD "cd $REMOTE_DIR && docker compose build bot && docker compose up -d bot"

    # 3. Health probe
    echo -e "${YELLOW}[3/3]${RESET} health probe"
    sleep 5
    cmd_status
}

case "${1:-}" in
    --status)     cmd_status   ;;
    --logs)       cmd_logs     ;;
    --sync-env)   cmd_sync_env ;;
    --migrate)    cmd_migrate  ;;
    --help|-h)    sed -n '2,20p' "$0" ;;
    *)            cmd_deploy   ;;
esac
