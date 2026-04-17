#!/usr/bin/env bash
# ============================================================
# Deploy to Production EC2
# ============================================================
#
# Usage:
#   ./scripts/deploy_prod.sh              # pull, build, restart
#   ./scripts/deploy_prod.sh --status     # check health + execution status
#   ./scripts/deploy_prod.sh --logs       # tail container logs
#   ./scripts/deploy_prod.sh --sync-env   # sync local .env keys to prod
#
# Prerequisites:
#   - SSH key at ~/.ssh/arb-trader-key.pem
#   - EC2 instance at 18.215.6.141
# ============================================================

set -eo pipefail

EC2_HOST="18.215.6.141"
SSH_KEY="$HOME/.ssh/arb-trader-key.pem"
SSH_CMD="ssh -i $SSH_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=10 ubuntu@$EC2_HOST"
REMOTE_DIR="/opt/arb-trader"

# Colors
GREEN="\033[92m"
RED="\033[91m"
YELLOW="\033[93m"
RESET="\033[0m"

check_ssh() {
    if [[ ! -f "$SSH_KEY" ]]; then
        echo -e "${RED}SSH key not found: $SSH_KEY${RESET}"
        exit 1
    fi
    if ! $SSH_CMD "echo ok" &>/dev/null; then
        echo -e "${RED}Cannot connect to EC2 at $EC2_HOST${RESET}"
        exit 1
    fi
}

cmd_status() {
    echo ""
    echo "============================================================"
    echo "  Production Status"
    echo "============================================================"
    PASS=$($SSH_CMD "grep '^DASHBOARD_PASS=' $REMOTE_DIR/.env | cut -d= -f2")
    echo ""

    echo -n "  Health: "
    HEALTH=$($SSH_CMD "curl -sf -u admin:$PASS http://localhost:8000/health" 2>/dev/null)
    if [[ -n "$HEALTH" ]]; then
        echo -e "${GREEN}OK${RESET} — $HEALTH"
    else
        echo -e "${RED}DOWN${RESET}"
        return 1
    fi

    echo ""
    echo "  Wallet:"
    $SSH_CMD "curl -sf -u admin:$PASS http://localhost:8000/wallet/balance" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'    Address: {d[\"address\"]}')
for chain, bal in d.get('balances', {}).items():
    if bal is not None:
        print(f'    {chain}: {bal:.6f} ETH')
" 2>/dev/null || echo "    (unavailable)"

    echo ""
    echo "  Execution:"
    $SSH_CMD "curl -sf -u admin:$PASS http://localhost:8000/execution" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
for chain, info in d.get('chains', {}).items():
    mode = info['mode'].upper()
    exe = 'executable' if info['executable'] else 'not ready'
    color = '\033[92m' if mode == 'LIVE' else '\033[93m' if mode == 'SIMULATED' else '\033[91m'
    print(f'    {chain}: {color}{mode}\033[0m ({exe})')
" 2>/dev/null || echo "    (unavailable)"
    echo ""
}

cmd_logs() {
    $SSH_CMD "cd $REMOTE_DIR && docker compose logs bot --tail 50 -f" 2>/dev/null
}

cmd_sync_env() {
    echo "Syncing executor keys + RPC to prod .env..."
    local EXEC_KEY=$(grep "^EXECUTOR_PRIVATE_KEY=" .env | cut -d= -f2)
    local EXEC_CONTRACT=$(grep "^EXECUTOR_CONTRACT=" .env | cut -d= -f2)
    local RPC_OPT=$(grep "^RPC_OPTIMISM=" .env | cut -d= -f2)

    $SSH_CMD "cd $REMOTE_DIR && \
        sed -i 's|^# EXECUTOR_PRIVATE_KEY=.*|EXECUTOR_PRIVATE_KEY=${EXEC_KEY}|' .env && \
        sed -i 's|^EXECUTOR_PRIVATE_KEY=.*|EXECUTOR_PRIVATE_KEY=${EXEC_KEY}|' .env && \
        sed -i 's|^# EXECUTOR_CONTRACT=.*|EXECUTOR_CONTRACT=${EXEC_CONTRACT}|' .env && \
        sed -i 's|^EXECUTOR_CONTRACT=.*|EXECUTOR_CONTRACT=${EXEC_CONTRACT}|' .env && \
        sed -i 's|^RPC_OPTIMISM=.*|RPC_OPTIMISM=${RPC_OPT}|' .env"

    echo -e "${GREEN}Done.${RESET} Restart with: ./scripts/deploy_prod.sh"
}

cmd_deploy() {
    echo ""
    echo "============================================================"
    echo "  Deploying to Production"
    echo "============================================================"
    echo ""

    # Step 1: Pull (including submodules for trading_platform)
    echo -e "  ${YELLOW}[1/4]${RESET} Pulling latest code..."
    $SSH_CMD "cd $REMOTE_DIR && git pull origin master 2>&1 && git submodule update --init --recursive 2>&1"
    echo ""

    # Step 2: Build
    echo -e "  ${YELLOW}[2/4]${RESET} Building Docker image..."
    $SSH_CMD "cd $REMOTE_DIR && docker compose build --no-cache bot 2>&1" | tail -5
    echo ""

    # Step 3: Restart
    echo -e "  ${YELLOW}[3/4]${RESET} Restarting bot..."
    $SSH_CMD "cd $REMOTE_DIR && docker compose up -d --force-recreate bot 2>&1"
    echo ""

    # Step 4: Verify
    echo -e "  ${YELLOW}[4/4]${RESET} Waiting for health check..."
    sleep 12
    PASS=$($SSH_CMD "grep '^DASHBOARD_PASS=' $REMOTE_DIR/.env | cut -d= -f2")
    HEALTH=$($SSH_CMD "curl -sf -u admin:$PASS http://localhost:8000/health" 2>/dev/null)
    if [[ -n "$HEALTH" ]]; then
        echo -e "  ${GREEN}Deploy successful!${RESET}"
        echo "  $HEALTH"
    else
        echo -e "  ${RED}Health check failed!${RESET}"
        echo "  Check logs: ./scripts/deploy_prod.sh --logs"
        exit 1
    fi

    echo ""
    echo "  Dashboard: https://arb-trader.yeda-ai.com/dashboard"
    echo "  Analytics: https://arb-trader.yeda-ai.com/analytics"
    echo "============================================================"
    echo ""
}

# --- Main ---
check_ssh

case "${1:-}" in
    --status)  cmd_status ;;
    --logs)    cmd_logs ;;
    --sync-env) cmd_sync_env ;;
    *)         cmd_deploy ;;
esac
