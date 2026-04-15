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
    PYTHONPATH=src python -m main --config config/onchain_config.json --onchain
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal

import requests
from web3 import Web3
try:
    from web3.middleware import ExtraDataToPOAMiddleware as geth_poa_middleware
except ImportError:
    from web3.middleware import geth_poa_middleware

from config import BotConfig
from contracts import PUBLIC_RPC_URLS
from env import get_rpc_overrides
from log import get_logger, log_execution
from models import ZERO, ExecutionResult, Opportunity

D = Decimal

logger = get_logger(__name__)

# Flashbots relay endpoint for Ethereum mainnet.
FLASHBOTS_RELAY_URL = "https://relay.flashbots.net"

# Chains where Flashbots bundle submission is available.
# Other chains have their own MEV protection (e.g. Arbitrum sequencer ordering).
FLASHBOTS_CHAINS = frozenset({"ethereum"})
SUPPORTED_LIVE_DEX_TYPES = frozenset({
    "uniswap_v3", "sushi_v3", "pancakeswap_v3",
    "velodrome_v2", "aerodrome",
})

# Swap type constants — must match FlashArbExecutor.sol.
SWAP_TYPE_V3 = 0
SWAP_TYPE_VELO = 1

# V3 DEX types (use exactInputSingle).
V3_DEX_TYPES = frozenset({"uniswap_v3", "sushi_v3", "pancakeswap_v3"})
# Solidly-fork DEX types (use swapExactTokensForTokens).
VELO_DEX_TYPES = frozenset({"velodrome_v2", "aerodrome"})

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
        "aerodrome": "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43",
    },
    "optimism": {
        "uniswap_v3": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "sushi_v3": "0x8A21F6768C1f8075791D08546Dadf6daA0bE820c",
        "velodrome_v2": "0xa062aE8A9c5e11aaA026fc2670B0D65cCc8B2858",
    },
    "bsc": {
        "pancakeswap_v3": "0x1b81D678ffb9C0263b24A97847620C99d213eB14",
    },
}

# Velodrome/Aerodrome pool factory addresses per chain.
VELO_FACTORIES: dict[str, dict[str, str]] = {
    "optimism": {
        "velodrome_v2": "0xF1046053aa5682b4F9a81b5481394DA16BE5FF5a",
    },
    "base": {
        "aerodrome": "0x420DD381b31aEf6683db6B902084cB0FFECe40Da",
    },
}

# Aave V3 Pool addresses per chain (for flash loans).
AAVE_V3_POOL: dict[str, str] = {
    "ethereum": "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
    "arbitrum": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    "base": "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
    "optimism": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
}

# Minimal ABI for calling executeArbitrage on FlashArbExecutor (v2 with swap types).
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
                    {"name": "swapTypeA", "type": "uint8"},
                    {"name": "swapTypeB", "type": "uint8"},
                    {"name": "factoryA", "type": "address"},
                    {"name": "factoryB", "type": "address"},
                    {"name": "stableA", "type": "bool"},
                    {"name": "stableB", "type": "bool"},
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

        # Per-chain contract: EXECUTOR_CONTRACT_ARBITRUM, EXECUTOR_CONTRACT_BASE, etc.
        # Falls back to generic EXECUTOR_CONTRACT.
        chain_key = config.dexes[0].chain or "ethereum"
        self.contract_address = (
            os.environ.get(f"EXECUTOR_CONTRACT_{chain_key.upper()}", "")
            or os.environ.get("EXECUTOR_CONTRACT", "")
        )

        if not self.private_key:
            raise ChainExecutorError(
                "EXECUTOR_PRIVATE_KEY not set.  "
                "Add your wallet private key to .env (never commit this!)."
            )
        if not self.contract_address:
            raise ChainExecutorError(
                f"EXECUTOR_CONTRACT not set for chain '{chain_key}'.  "
                f"Set EXECUTOR_CONTRACT_{chain_key.upper()} or EXECUTOR_CONTRACT in .env."
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

        # Determine if Flashbots private submission is available.
        self.use_flashbots = self.chain in FLASHBOTS_CHAINS
        fb_status = "ENABLED (private relay)" if self.use_flashbots else "DISABLED (public mempool)"
        logger.info(
            "ChainExecutor ready: chain=%s, wallet=%s, contract=%s, flashbots=%s",
            self.chain, self.account.address, self.contract_address, fb_status,
        )

    def execute(self, opportunity: Opportunity) -> ExecutionResult:
        """Simulate, then send the on-chain arbitrage transaction.

        CRITICAL: Always simulate before real execution. eth_call is free and
        catches reverts (profit below min, bad routes, approval issues) before
        spending real gas. This saves ~$5-50 per avoided failed transaction.

        Flow:
          1. Build the transaction
          2. Simulate via eth_call (dry-run on-chain) — if it reverts, skip
          3. Sign and send the real transaction
          4. Wait for confirmation

        Returns ExecutionResult with success=True if the tx was mined
        successfully, or success=False with the revert/simulation reason.
        """
        if opportunity.is_cross_chain:
            return ExecutionResult(
                success=False,
                reason="cross_chain_execution_not_supported",
                realized_profit_base=ZERO,
                opportunity=opportunity,
            )
        try:
            supported, reason = self._supports_live_execution(opportunity)
            if not supported:
                return ExecutionResult(
                    success=False,
                    reason=reason,
                    realized_profit_base=ZERO,
                    opportunity=opportunity,
                )
            tx_data = self._build_transaction(opportunity)

            # Step 1: Simulate via eth_call before spending gas.
            sim_ok, sim_reason = self._simulate_transaction(tx_data)
            if not sim_ok:
                logger.warning("Simulation failed, skipping execution: %s", sim_reason)
                return ExecutionResult(
                    success=False,
                    reason=f"simulation_failed:{sim_reason}",
                    realized_profit_base=ZERO,
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
                    realized_profit_base=ZERO,
                    opportunity=opportunity,
                )

        except Exception as exc:
            logger.error("Execution failed: %s", exc)
            return ExecutionResult(
                success=False,
                reason=f"error:{exc}",
                realized_profit_base=ZERO,
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
        """Build the executeArbitrage transaction (without signing).

        Resolves token addresses dynamically from the pair's base/quote
        assets rather than hardcoding WETH/USDC.
        """
        supported, reason = self._supports_live_execution(opportunity)
        if not supported:
            raise ChainExecutorError(reason)
        from tokens import resolve_token_address, token_decimals

        pair_parts = opportunity.pair.split("/", 1)
        if len(pair_parts) != 2:
            raise ChainExecutorError(f"Opportunity pair '{opportunity.pair}' is not base/quote formatted.")
        base_asset, quote_asset = pair_parts

        base_token = resolve_token_address(self.chain, base_asset)
        quote_token = resolve_token_address(self.chain, quote_asset)

        if not base_token:
            raise ChainExecutorError(
                f"Cannot resolve base asset '{base_asset}' "
                f"on chain '{self.chain}'."
            )
        if not quote_token:
            raise ChainExecutorError(
                f"Cannot resolve quote asset '{quote_asset}' "
                f"on chain '{self.chain}'."
            )

        router_a = self._resolve_router(opportunity.buy_dex)
        router_b = self._resolve_router(opportunity.sell_dex)

        quote_decimals = token_decimals(quote_asset)
        amount_in_raw = int(opportunity.cost_to_buy_quote * D(str(10 ** quote_decimals)))
        min_profit_raw = int(self.config.min_profit_base * opportunity.cost_to_buy_quote)

        fee_a = self._resolve_fee(opportunity.buy_dex)
        fee_b = self._resolve_fee(opportunity.sell_dex)

        buy_type = self._resolve_dex_type(opportunity.buy_dex)
        sell_type = self._resolve_dex_type(opportunity.sell_dex)
        swap_type_a = SWAP_TYPE_VELO if buy_type in VELO_DEX_TYPES else SWAP_TYPE_V3
        swap_type_b = SWAP_TYPE_VELO if sell_type in VELO_DEX_TYPES else SWAP_TYPE_V3
        factory_a = self._resolve_velo_factory(opportunity.buy_dex) if swap_type_a == SWAP_TYPE_VELO else "0x" + "00" * 20
        factory_b = self._resolve_velo_factory(opportunity.sell_dex) if swap_type_b == SWAP_TYPE_VELO else "0x" + "00" * 20

        call_data = self.contract.functions.executeArbitrage((
            Web3.to_checksum_address(base_token),
            Web3.to_checksum_address(quote_token),
            Web3.to_checksum_address(router_a),
            Web3.to_checksum_address(router_b),
            fee_a,
            fee_b,
            amount_in_raw,
            min_profit_raw,
            swap_type_a,
            swap_type_b,
            Web3.to_checksum_address(factory_a),
            Web3.to_checksum_address(factory_b),
            False,  # stableA — volatile pools for arb
            False,  # stableB
        ))

        nonce = self.w3.eth.get_transaction_count(self.account.address)

        # Dynamic gas estimation with 20% safety buffer.
        # Why 1.2x: gas can vary ±10% between estimate and execution because
        # other transactions in the same block change storage slot costs
        # (cold→warm SLOAD, SSTORE refunds).  1.2x covers the worst case
        # without overpaying.  Too high a buffer wastes gas; too low risks
        # out-of-gas reverts (lost gas + failed trade).
        #
        # 500K fallback: flash arb execution typically uses 300-400K gas
        # (flash loan callback + 2 swaps + approvals + repay).  500K
        # provides headroom for complex routing.  On L2s (Arbitrum, Base)
        # gas is cheap so overshooting doesn't matter much.
        try:
            estimated_gas = call_data.estimate_gas({"from": self.account.address})
            gas_limit = int(estimated_gas * 1.2)
        except Exception:
            gas_limit = 500_000

        # EIP-1559 fee estimation from recent block history.
        max_fee, priority_fee = self._estimate_gas_fees()

        return call_data.build_transaction({
            "from": self.account.address,
            "nonce": nonce,
            "gas": gas_limit,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority_fee,
        })

    def _estimate_gas_fees(self) -> tuple[int, int]:
        """Estimate EIP-1559 gas fees from recent block history.

        Formula: maxFeePerGas = 2 * baseFee + priorityFee
        Why 2x baseFee: provides headroom if baseFee increases next block
        (Ethereum baseFee can increase up to 12.5% per block).
        Why median tips: 50th percentile avoids overpaying while still being
        competitive for inclusion.

        Returns (maxFeePerGas, maxPriorityFeePerGas) in wei.
        Uses eth_feeHistory to get the recent base fee and priority fee
        percentiles, then adds a buffer for inclusion reliability.
        """
        try:
            fee_history = self.w3.eth.fee_history(
                block_count=5,
                newest_block="latest",
                reward_percentiles=[25, 50, 75],
            )

            # Use the latest base fee and add headroom for the next block.
            base_fees = fee_history.get("baseFeePerGas", [])
            latest_base_fee = base_fees[-1] if base_fees else self.w3.eth.gas_price

            # Use the median (50th percentile) priority fee from recent blocks.
            rewards = fee_history.get("reward", [])
            if rewards:
                median_tips = [r[1] for r in rewards if len(r) > 1]
                priority_fee = sum(median_tips) // len(median_tips) if median_tips else self.w3.to_wei(1, "gwei")
            else:
                priority_fee = self.w3.to_wei(1, "gwei")

            # maxFeePerGas = 2 * baseFee + priorityFee (standard formula).
            max_fee = 2 * latest_base_fee + priority_fee

            return max_fee, priority_fee

        except Exception:
            # Fallback to simple gas_price * 1.5 if fee_history is unavailable.
            gas_price = self.w3.eth.gas_price
            return int(gas_price * 1.5), self.w3.to_wei(1, "gwei")

    def _sign_and_send(self, tx_data: dict) -> bytes:
        """Sign and broadcast a built transaction.

        On Ethereum mainnet, uses Flashbots private relay to avoid front-running.
        On other chains, falls back to regular mempool submission.
        """
        signed = self.w3.eth.account.sign_transaction(tx_data, self.private_key)

        if self.use_flashbots:
            return self._send_flashbots_bundle(signed)

        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        logger.info("TX sent (public mempool): %s", tx_hash.hex())
        return tx_hash

    def _send_flashbots_bundle(self, signed_tx: object) -> bytes:
        """Submit a signed transaction as a Flashbots bundle.

        Flashbots bundles are sent to a private relay and are not visible
        in the public mempool, preventing front-running and sandwich attacks.

        The bundle targets the next block (~12s on Ethereum). If not included,
        it expires harmlessly (no gas cost for failed inclusion). This is the key
        advantage over public mempool: failed Flashbots bundles cost nothing.

        Falls back to public mempool if Flashbots relay is unreachable — this
        exposes to MEV but ensures the trade still lands if relay is down.
        """
        raw_tx = signed_tx.rawTransaction.hex()  # type: ignore[union-attr]
        # Target next block (current + 1) for maximum inclusion probability.
        # Why not current + 2 or later: arb opportunities are time-sensitive —
        # by block N+2 (~24s later), other bots will have closed the spread.
        # Flashbots bundles targeting a specific block are automatically dropped
        # if not included in that block — there is no automatic retry.
        # This is safe: if our bundle misses, we detect the spread again on
        # the next scan cycle and submit a fresh bundle.
        target_block = self.w3.eth.block_number + 1

        # Flashbots eth_sendBundle JSON-RPC.
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_sendBundle",
            "params": [
                {
                    "txs": [raw_tx],
                    "blockNumber": hex(target_block),
                }
            ],
        }

        # Sign the payload with the searcher key for Flashbots authentication.
        # The X-Flashbots-Signature header uses the format: {address}:{signature}
        from eth_account.messages import encode_defunct
        body_bytes = str(payload).encode("utf-8")
        message = encode_defunct(text=Web3.keccak(body_bytes).hex())
        signature = self.w3.eth.account.sign_message(message, self.private_key)
        flashbots_header = f"{self.account.address}:{signature.signature.hex()}"

        try:
            resp = requests.post(
                FLASHBOTS_RELAY_URL,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Flashbots-Signature": flashbots_header,
                },
                timeout=10,
            )
            resp.raise_for_status()
            result = resp.json()

            if "error" in result:
                logger.warning(
                    "Flashbots relay error: %s — falling back to public mempool",
                    result["error"],
                )
                tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)  # type: ignore[union-attr]
                logger.info("TX sent (public fallback): %s", tx_hash.hex())
                return tx_hash

            bundle_hash = result.get("result", {}).get("bundleHash", "unknown")
            logger.info(
                "Flashbots bundle submitted: hash=%s, target_block=%d",
                bundle_hash, target_block,
            )

        except requests.RequestException as exc:
            logger.warning(
                "Flashbots relay unreachable: %s — falling back to public mempool",
                exc,
            )
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)  # type: ignore[union-attr]
            logger.info("TX sent (public fallback): %s", tx_hash.hex())
            return tx_hash

        # Return the tx hash from the signed transaction.
        tx_hash = Web3.keccak(signed_tx.rawTransaction)  # type: ignore[union-attr]
        return tx_hash

    # DEX types not yet supported in execution.
    _UNSUPPORTED_EXEC_TYPES = frozenset({"curve", "traderjoe_lb"})

    def _resolve_router(self, dex_name: str) -> str:
        """Map a DEX name from the opportunity to its swap router address."""
        chain_routers = SWAP_ROUTERS.get(self.chain, {})
        for dex in self.config.dexes:
            if dex.name == dex_name and dex.dex_type:
                if dex.dex_type in self._UNSUPPORTED_EXEC_TYPES:
                    raise ChainExecutorError(
                        f"DEX '{dex_name}' ({dex.dex_type}) cannot be used for execution."
                    )
                router = chain_routers.get(dex.dex_type)
                if router:
                    return router
        raise ChainExecutorError(
            f"No swap router for DEX '{dex_name}' on chain '{self.chain}'."
        )

    def _resolve_velo_factory(self, dex_name: str) -> str:
        """Resolve the Velodrome/Aerodrome pool factory for a DEX."""
        chain_factories = VELO_FACTORIES.get(self.chain, {})
        for dex in self.config.dexes:
            if dex.name == dex_name and dex.dex_type:
                factory = chain_factories.get(dex.dex_type)
                if factory:
                    return factory
        raise ChainExecutorError(
            f"No Velo factory for DEX '{dex_name}' on chain '{self.chain}'."
        )

    def _resolve_dex_type(self, dex_name: str) -> str:
        for dex in self.config.dexes:
            if dex.name == dex_name and dex.dex_type:
                return dex.dex_type
        raise ChainExecutorError(f"No dex_type configured for DEX '{dex_name}'.")

    def _supports_live_execution(self, opportunity: Opportunity) -> tuple[bool, str]:
        buy_type = self._resolve_dex_type(opportunity.buy_dex)
        sell_type = self._resolve_dex_type(opportunity.sell_dex)
        unsupported = [
            dex_type for dex_type in (buy_type, sell_type)
            if dex_type not in SUPPORTED_LIVE_DEX_TYPES
        ]
        if unsupported:
            return False, f"unsupported_live_venue:{','.join(sorted(set(unsupported)))}"
        return True, "ok"

    def _resolve_fee(self, dex_name: str) -> int:
        """Get the on-chain fee tier for a DEX (in hundredths of a bip)."""
        for dex in self.config.dexes:
            if dex.name == dex_name:
                # Convert bps to Uniswap fee tier format.
                # 30 bps → 3000, 5 bps → 500, 25 bps → 2500
                return int(dex.fee_bps * 100)
        return 3000  # default 0.30%
