"""ChainExecutor — sends real on-chain transactions to the FlashArbExecutor contract.

This is Phase 3 from the recommendations: the Python bot detects an opportunity,
then calls the deployed Solidity contract to execute the atomic flash-loan arb.

ERC-20 execution flow (single transaction):
  1. Python bot calls contract.executeArbitrage(params)
  2. Contract requests flash loan from Aave V3 (borrows quote token, e.g. USDC)
  3. Aave calls contract.executeOperation()
  4. Contract: approve + swap quote→base on DEX A (buy cheap)
  5. Contract: approve + swap base→quote on DEX B (sell expensive)
  6. Contract: repay flash loan + 9 bps fee
  7. Contract: transfer profit to owner
  8. If profit < minProfit → entire tx reverts (no loss, only gas)

Requirements:
  - FlashArbExecutor deployed on the target chain
  - Bot wallet has ETH/native token for gas
  - Contract address and ABI set in .env or config
  - Private key set in EXECUTOR_PRIVATE_KEY env var

Usage::

    # Set in .env:
    # EXECUTOR_PRIVATE_KEY=0x...
    # EXECUTOR_CONTRACT=0x...

    # The bot uses ChainExecutor instead of PaperExecutor
    PYTHONPATH=src python -m arbitrage_bot.main --config config/onchain_config.json --onchain
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from web3 import Web3
from web3.middleware import geth_poa_middleware

from arbitrage_bot.config import BotConfig
from arbitrage_bot.contracts import PUBLIC_RPC_URLS
from arbitrage_bot.env import get_rpc_overrides
from arbitrage_bot.log import get_logger, log_execution
from arbitrage_bot.models import ExecutionResult, Opportunity

logger = get_logger(__name__)

# Uniswap V3 / PancakeSwap V3 / Sushi V3 swap router addresses per chain.
SWAP_ROUTERS: dict[str, dict[str, str]] = {
    "ethereum": {
        "uniswap_v3": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "sushi_v3": "0x2E6cd2d30aa43f40aa81619ff4b6E0a41479B13F",
        "pancakeswap_v3": "0x1b81D678ffb9C0263b24A97847620C99d213eB14",
    },
    "arbitrum": {
        "uniswap_v3": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "sushi_v3": "0x8A21F6768C1f8075791D08546Dadf6daA0bE820c",
        "pancakeswap_v3": "0x1b81D678ffb9C0263b24A97847620C99d213eB14",
    },
    "base": {
        "uniswap_v3": "0x2626664c2603336E57B271c5C0b26F421741e481",
        "pancakeswap_v3": "0x1b81D678ffb9C0263b24A97847620C99d213eB14",
    },
    "bsc": {
        "pancakeswap_v3": "0x1b81D678ffb9C0263b24A97847620C99d213eB14",
    },
}

# Aave V3 Pool addresses per chain (for flash loans).
AAVE_V3_POOL: dict[str, str] = {
    "ethereum": "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
    "arbitrum": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    "base": "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
}

# Minimal ABI for calling executeArbitrage on FlashArbExecutor.
EXECUTOR_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "baseToken", "type": "address"},
                    {"name": "quoteToken", "type": "address"},
                    {"name": "routerA", "type": "address"},
                    {"name": "routerB", "type": "address"},
                    {"name": "feeA", "type": "uint24"},
                    {"name": "feeB", "type": "uint24"},
                    {"name": "amountIn", "type": "uint256"},
                    {"name": "minProfit", "type": "uint256"},
                ],
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "executeArbitrage",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


class ChainExecutorError(Exception):
    pass


class ChainExecutor:
    """Execute arbitrage on-chain via the deployed FlashArbExecutor contract.

    Replaces PaperExecutor for real trading.  Builds and sends the
    transaction, waits for confirmation, and returns the result.
    """

    def __init__(self, config: BotConfig) -> None:
        self.config = config

        # Read from environment.
        self.private_key = os.environ.get("EXECUTOR_PRIVATE_KEY", "")
        self.contract_address = os.environ.get("EXECUTOR_CONTRACT", "")

        if not self.private_key:
            raise ChainExecutorError(
                "EXECUTOR_PRIVATE_KEY not set.  "
                "Add your wallet private key to .env (never commit this!)."
            )
        if not self.contract_address:
            raise ChainExecutorError(
                "EXECUTOR_CONTRACT not set.  "
                "Deploy FlashArbExecutor.sol and add the address to .env."
            )

        # Determine chain from the first DEX config.
        self.chain = config.dexes[0].chain or "ethereum"
        rpc_overrides = get_rpc_overrides()
        rpc_url = rpc_overrides.get(self.chain, PUBLIC_RPC_URLS.get(self.chain, ""))

        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        # Needed for PoA chains like BSC.
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)

        self.account = self.w3.eth.account.from_key(self.private_key)
        self.contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.contract_address),
            abi=EXECUTOR_ABI,
        )

        logger.info(
            "ChainExecutor ready: chain=%s, wallet=%s, contract=%s",
            self.chain, self.account.address, self.contract_address,
        )

    def execute(self, opportunity: Opportunity) -> ExecutionResult:
        """Simulate, then send the on-chain arbitrage transaction.

        Flow:
          1. Build the transaction
          2. Simulate via eth_call (dry-run on-chain) — if it reverts, skip
          3. Sign and send the real transaction
          4. Wait for confirmation

        Returns ExecutionResult with success=True if the tx was mined
        successfully, or success=False with the revert/simulation reason.
        """
        try:
            tx_data = self._build_transaction(opportunity)

            # Step 1: Simulate via eth_call before spending gas.
            sim_ok, sim_reason = self._simulate_transaction(tx_data)
            if not sim_ok:
                logger.warning("Simulation failed, skipping execution: %s", sim_reason)
                return ExecutionResult(
                    success=False,
                    reason=f"simulation_failed:{sim_reason}",
                    realized_profit_base=0.0,
                    opportunity=opportunity,
                )
            logger.info("Simulation passed — sending real transaction")

            # Step 2: Sign and send.
            tx_hash = self._sign_and_send(tx_data)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if receipt["status"] == 1:
                logger.info("TX confirmed: %s (block %d)", tx_hash.hex(), receipt["blockNumber"])
                return ExecutionResult(
                    success=True,
                    reason=f"tx:{tx_hash.hex()}",
                    realized_profit_base=opportunity.net_profit_base,
                    opportunity=opportunity,
                )
            else:
                logger.warning("TX reverted: %s", tx_hash.hex())
                return ExecutionResult(
                    success=False,
                    reason=f"tx_reverted:{tx_hash.hex()}",
                    realized_profit_base=0.0,
                    opportunity=opportunity,
                )

        except Exception as exc:
            logger.error("Execution failed: %s", exc)
            return ExecutionResult(
                success=False,
                reason=f"error:{exc}",
                realized_profit_base=0.0,
                opportunity=opportunity,
            )

    def _simulate_transaction(self, tx_data: dict) -> tuple[bool, str]:
        """Simulate the transaction via eth_call (no gas spent).

        Returns (True, "ok") if simulation succeeds, or (False, reason) if it
        would revert.  This catches: insufficient profit, bad routes, token
        approval issues, etc. before real gas is spent.
        """
        try:
            self.w3.eth.call({
                "from": tx_data["from"],
                "to": tx_data["to"],
                "data": tx_data["data"],
                "value": tx_data.get("value", 0),
            })
            return True, "ok"
        except Exception as exc:
            # Extract revert reason if available.
            reason = str(exc)
            if "profit below minimum" in reason.lower():
                return False, "profit_below_minimum"
            return False, reason

    def _build_transaction(self, opportunity: Opportunity) -> dict:
        """Build the executeArbitrage transaction (without signing)."""
        from arbitrage_bot.tokens import CHAIN_TOKENS

        tokens = CHAIN_TOKENS[self.chain]

        base_token = tokens.weth
        quote_token = tokens.usdc

        router_a = self._resolve_router(opportunity.buy_dex)
        router_b = self._resolve_router(opportunity.sell_dex)

        amount_in_raw = int(opportunity.cost_to_buy_quote * 10**6)
        min_profit_raw = int(self.config.min_profit_base * opportunity.cost_to_buy_quote)

        fee_a = self._resolve_fee(opportunity.buy_dex)
        fee_b = self._resolve_fee(opportunity.sell_dex)

        return self.contract.functions.executeArbitrage((
            Web3.to_checksum_address(base_token),
            Web3.to_checksum_address(quote_token),
            Web3.to_checksum_address(router_a),
            Web3.to_checksum_address(router_b),
            fee_a,
            fee_b,
            amount_in_raw,
            min_profit_raw,
        )).build_transaction({
            "from": self.account.address,
            "nonce": self.w3.eth.get_transaction_count(self.account.address),
            "gas": 500_000,
            "maxFeePerGas": self.w3.eth.gas_price * 2,
            "maxPriorityFeePerGas": self.w3.to_wei(1, "gwei"),
        })

    def _sign_and_send(self, tx_data: dict) -> bytes:
        """Sign and broadcast a built transaction."""
        signed = self.w3.eth.account.sign_transaction(tx_data, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        logger.info("TX sent: %s", tx_hash.hex())
        return tx_hash

    def _resolve_router(self, dex_name: str) -> str:
        """Map a DEX name from the opportunity to its swap router address."""
        chain_routers = SWAP_ROUTERS.get(self.chain, {})
        # Try to match by dex_type from config.
        for dex in self.config.dexes:
            if dex.name == dex_name and dex.dex_type:
                router = chain_routers.get(dex.dex_type)
                if router:
                    return router
        raise ChainExecutorError(
            f"No swap router for DEX '{dex_name}' on chain '{self.chain}'."
        )

    def _resolve_fee(self, dex_name: str) -> int:
        """Get the on-chain fee tier for a DEX (in hundredths of a bip)."""
        for dex in self.config.dexes:
            if dex.name == dex_name:
                # Convert bps to Uniswap fee tier format.
                # 30 bps → 3000, 5 bps → 500, 25 bps → 2500
                return int(dex.fee_bps * 100)
        return 3000  # default 0.30%
