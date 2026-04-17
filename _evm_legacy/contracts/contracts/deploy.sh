#!/usr/bin/env bash
# ============================================================
# FlashArbExecutor Deployment Script
# ============================================================
#
# One-time deployment per chain.  Once deployed, add the contract
# address to .env as EXECUTOR_CONTRACT and restart the bot.
#
# Prerequisites:
#   1. Install Foundry:  curl -L https://foundry.paradigm.xyz | bash && foundryup
#   2. Fund the deployer wallet with native gas tokens (ETH/ARB/etc.)
#   3. Set EXECUTOR_PRIVATE_KEY in .env
#   4. Set RPC_<CHAIN> in .env (Alchemy recommended)
#
# Usage:
#   cd contracts/
#   ./deploy.sh <chain>
#
# Supported chains: ethereum, arbitrum, base, optimism
#
# Example:
#   ./deploy.sh arbitrum        # deploy to Arbitrum (cheapest gas)
#   ./deploy.sh arbitrum --dry  # simulate only, don't broadcast
#
# After deployment, the script prints the contract address.
# Add it to your .env:
#   EXECUTOR_CONTRACT=0x...
# ============================================================

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Load .env from project root (parse safely — .env may have unquoted
# values with spaces or # characters that break `source`).
if [[ -f "$PROJECT_ROOT/.env" ]]; then
    while IFS='=' read -r key value; do
        case "$key" in \#*|"") continue ;; esac
        # Strip inline comments and leading/trailing quotes/spaces
        value="${value%%\#*}"
        value="${value%"${value##*[![:space:]]}"}"
        value="${value#\'}"
        value="${value%\'}"
        export "$key=$value" 2>/dev/null || true
    done < "$PROJECT_ROOT/.env"
fi

# --- Aave V3 Pool addresses (must match chain_executor.py) ---
get_aave_pool() {
    case "$1" in
        ethereum) echo "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2" ;;
        arbitrum) echo "0x794a61358D6845594F94dc1DB02A252b5b4814aD" ;;
        base)     echo "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5" ;;
        optimism) echo "0x794a61358D6845594F94dc1DB02A252b5b4814aD" ;;
        polygon)  echo "0x794a61358D6845594F94dc1DB02A252b5b4814aD" ;;
        *)        echo "" ;;
    esac
}

# --- RPC URL resolution ---
get_rpc_url() {
    case "$1" in
        ethereum) echo "${RPC_ETHEREUM:-}" ;;
        arbitrum) echo "${RPC_ARBITRUM:-}" ;;
        base)     echo "${RPC_BASE:-}" ;;
        optimism) echo "${RPC_OPTIMISM:-}" ;;
        polygon)  echo "${RPC_POLYGON:-}" ;;
        *)        echo "" ;;
    esac
}

# --- Parse args ---
CHAIN="${1:-}"
DRY_RUN=false
if [[ "${2:-}" == "--dry" ]]; then
    DRY_RUN=true
fi

if [[ -z "$CHAIN" ]]; then
    echo "Usage: ./deploy.sh <chain> [--dry]"
    echo ""
    echo "Supported chains: ethereum, arbitrum, base, optimism, polygon"
    echo ""
    echo "Examples:"
    echo "  ./deploy.sh arbitrum        # deploy to Arbitrum"
    echo "  ./deploy.sh arbitrum --dry  # simulate only"
    exit 1
fi

# --- Validate chain ---
AAVE_POOL=$(get_aave_pool "$CHAIN")
if [[ -z "$AAVE_POOL" ]]; then
    echo "ERROR: Unsupported chain '$CHAIN'"
    echo "Supported: ethereum, arbitrum, base"
    exit 1
fi

# --- Resolve RPC URL ---
RPC_URL=$(get_rpc_url "$CHAIN")
if [[ -z "$RPC_URL" ]]; then
    echo "ERROR: RPC_${CHAIN^^} not set in .env"
    exit 1
fi

# --- Validate private key ---
if [[ -z "${EXECUTOR_PRIVATE_KEY:-}" ]]; then
    echo "ERROR: EXECUTOR_PRIVATE_KEY not set in .env"
    echo "This is the wallet that will own the contract and receive profits."
    exit 1
fi

# --- Check Foundry ---
if ! command -v forge &> /dev/null; then
    echo "ERROR: Foundry (forge) not installed."
    echo "Install: curl -L https://foundry.paradigm.xyz | bash && foundryup"
    exit 1
fi

echo "============================================================"
echo "FlashArbExecutor Deployment"
echo "============================================================"
echo "Chain:      $CHAIN"
echo "Aave Pool:  $AAVE_POOL"
echo "RPC:        ${RPC_URL:0:50}..."
echo "Dry run:    $DRY_RUN"
echo "============================================================"

cd "$SCRIPT_DIR"

if $DRY_RUN; then
    echo ""
    echo "--- DRY RUN (simulation only) ---"
    forge create FlashArbExecutor.sol:FlashArbExecutor \
        --rpc-url "$RPC_URL" \
        --private-key "$EXECUTOR_PRIVATE_KEY" \
        --constructor-args "$AAVE_POOL" \
        --simulate
    echo ""
    echo "Simulation passed. Run without --dry to deploy for real."
else
    echo ""
    echo "--- DEPLOYING (this costs real gas) ---"
    echo ""
    read -p "Confirm deployment to $CHAIN mainnet? (yes/no): " CONFIRM
    if [[ "$CONFIRM" != "yes" ]]; then
        echo "Aborted."
        exit 0
    fi

    OUTPUT=$(forge create FlashArbExecutor.sol:FlashArbExecutor \
        --rpc-url "$RPC_URL" \
        --private-key "$EXECUTOR_PRIVATE_KEY" \
        --constructor-args "$AAVE_POOL" \
        --broadcast \
        2>&1)

    echo "$OUTPUT"

    # Extract deployed address
    DEPLOYED=$(echo "$OUTPUT" | grep -i "deployed to:" | awk '{print $NF}')
    if [[ -n "$DEPLOYED" ]]; then
        echo ""
        echo "============================================================"
        echo "SUCCESS! Contract deployed to: $DEPLOYED"
        echo ""
        echo "Add to your .env:"
        echo "  EXECUTOR_CONTRACT=$DEPLOYED"
        echo "============================================================"
    fi
fi
