"""Swap event listener — Python equivalent of the video's ethers.js approach.

The video recommends:
  1. Subscribe to Swap events on the target DEX pools
  2. When a swap fires, re-check profitability across DEXs
  3. Trigger execution only if the opportunity is still profitable after fees

This module uses web3.py to poll for Swap events (HTTP polling, since most
free RPC endpoints don't support WebSocket subscriptions).  For production,
use a WebSocket provider or a service like Alchemy with event streaming.

Architecture:
  - Polls the latest block for Swap events on configured pool(s)
  - When a swap is detected, fetches fresh quotes and evaluates via
    OpportunityScanner (multi-factor ranking + risk filtering)
  - Runs indefinitely until interrupted (Ctrl+C)

Usage::

    export THEGRAPH_API_KEY=...   # optional, only if using subgraph market
    PYTHONPATH=src python -m event_listener \\
        --config config/uniswap_pancake_config.json --dry-run
"""

from __future__ import annotations

import argparse
import time
from decimal import Decimal

from web3 import Web3

from config import BotConfig
from contracts import PUBLIC_RPC_URLS
from env import get_rpc_overrides, load_env
from executor import PaperExecutor
from log import (
    get_logger,
    log_execution,
    log_scan,
    log_swap_event,
    log_summary,
    setup_logging,
)
from models import ZERO
from onchain_market import OnChainMarket
from scanner import OpportunityScanner
from strategy import ArbitrageStrategy

logger = get_logger(__name__)

# Uniswap V3 / PancakeSwap V3 / Sushi V3 Swap event signature.
# event Swap(address indexed sender, address indexed recipient,
#            int256 amount0, int256 amount1,
#            uint160 sqrtPriceX96, uint128 liquidity, int24 tick)
_raw = Web3.keccak(
    text="Swap(address,address,int256,int256,uint160,uint128,int24)"
).hex()
SWAP_EVENT_TOPIC = _raw if _raw.startswith("0x") else f"0x{_raw}"

# Well-known WETH/USDC pool addresses to monitor for swaps.
MONITORED_POOLS: dict[str, list[str]] = {
    "ethereum": [
        "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640",
        "0x8ad599c3A0ff1De082011EFDDc58f1908eb6e6D8",
    ],
    "bsc": [
        "0x36696169C63e42cd08ce11f5deeBbCeBae652050",
    ],
    "arbitrum": [
        "0xC6962004f452bE9203591991D15f6b388e09E8D0",
    ],
    "base": [
        "0xd0b53D9277642d899DF5C87A3966A349A798F224",
    ],
}


class SwapEventListener:
    """Poll for Swap events and trigger opportunity evaluation.

    Uses OpportunityScanner for multi-factor ranking and risk filtering,
    matching the scanner doc's recommendation for actionable, net-positive,
    risk-filtered opportunities.
    """

    def __init__(
        self,
        config: BotConfig,
        dry_run: bool = True,
        poll_interval: float = 2.0,
    ) -> None:
        self.config = config
        self.dry_run = dry_run
        self.poll_interval = poll_interval

        rpc_overrides = get_rpc_overrides()

        self.chain = config.dexes[0].chain or "ethereum"
        rpc_url = rpc_overrides.get(self.chain, PUBLIC_RPC_URLS.get(self.chain, ""))
        if not rpc_url:
            raise ValueError(f"No RPC URL for chain '{self.chain}'.")

        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.market = OnChainMarket(config, rpc_overrides=rpc_overrides or None)

        # Use OpportunityScanner instead of raw ArbitrageStrategy.
        # This adds multi-factor ranking, risk filtering, and alert thresholds.
        self.scanner = OpportunityScanner(
            config,
            alert_min_net_profit=config.min_profit_base,
            alert_max_warning_flags=1,
        )
        self.executor = PaperExecutor(config)

        self._last_block = 0
        self._swap_count = 0
        self._opportunity_count = 0
        self._executed_count = 0
        self._total_realized_profit = ZERO
        self._scan_index = 0

    @property
    def pools_to_monitor(self) -> list[str]:
        return MONITORED_POOLS.get(self.chain, [])

    def run(self) -> None:
        """Poll for swaps and evaluate opportunities indefinitely."""
        pools = self.pools_to_monitor
        if not pools:
            logger.warning("No monitored pools for chain '%s'.", self.chain)
            return

        pool_addrs = [Web3.to_checksum_address(p) for p in pools]
        logger.info(
            "Monitoring %d pool(s) on %s for Swap events (poll every %.1fs)",
            len(pool_addrs), self.chain, self.poll_interval,
        )
        logger.info("Dry-run: %s", self.dry_run)

        try:
            self._last_block = self.w3.eth.block_number
            logger.info("Starting from block %d", self._last_block)

            while True:
                self._poll_once(pool_addrs)
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            logger.info(
                "Stopped. Swaps detected: %d, Opportunities: %d, Executed: %d",
                self._swap_count, self._opportunity_count, self._executed_count,
            )
            log_summary(
                logger, "EVENT-LISTENER", self._scan_index,
                self._opportunity_count, self._executed_count,
                self._total_realized_profit, self.config.base_asset,
            )

    def _poll_once(self, pool_addrs: list[str]) -> None:
        """Check for new Swap events since the last polled block."""
        current_block = self.w3.eth.block_number
        if current_block <= self._last_block:
            return

        try:
            logs = self.w3.eth.get_logs({
                "fromBlock": self._last_block + 1,
                "toBlock": current_block,
                "address": pool_addrs,
                "topics": [SWAP_EVENT_TOPIC],
            })
        except Exception as exc:
            logger.error("Log fetch error: %s", exc)
            self._last_block = current_block
            return

        self._last_block = current_block

        if not logs:
            return

        self._swap_count += len(logs)
        self._scan_index += 1
        logger.info(
            "%d swap(s) in blocks %d-%d",
            len(logs), current_block - len(logs) + 1, current_block,
        )
        log_swap_event(logger, self.chain, current_block, len(logs))

        # Re-check profitability after the swap(s) using the full scanner
        # pipeline (multi-factor ranking + risk filtering).
        try:
            quotes = self.market.get_quotes()
        except Exception as exc:
            logger.error("Quote fetch error: %s", exc)
            return

        scan_result = self.scanner.scan_and_rank(quotes)
        opportunity = scan_result.best

        if opportunity is None:
            logger.info(
                "[scan %d] no actionable opportunity (%d evaluated, %d rejected)",
                self._scan_index, len(scan_result.opportunities) + scan_result.rejected_count,
                scan_result.rejected_count,
            )
            log_scan(logger, self._scan_index, quotes, None, "no_opportunity")
            return

        self._opportunity_count += 1
        logger.info(
            "OPPORTUNITY [rank 1/%d]: buy on %s, sell on %s, net=%.6f %s, "
            "liq_score=%.2f, flags=%s",
            len(scan_result.opportunities),
            opportunity.buy_dex, opportunity.sell_dex,
            float(opportunity.net_profit_base), self.config.base_asset,
            opportunity.liquidity_score,
            list(opportunity.warning_flags) or "none",
        )

        if self.dry_run:
            logger.info("dry-run: skipping execution")
            log_scan(logger, self._scan_index, quotes, opportunity, "dry_run_skip")
        else:
            result = self.executor.execute(opportunity)
            if result.success:
                self._executed_count += 1
                self._total_realized_profit += result.realized_profit_base
                logger.info(
                    "EXECUTED: realized profit=%.6f %s",
                    float(result.realized_profit_base), self.config.base_asset,
                )
                log_scan(logger, self._scan_index, quotes, opportunity, "executed")
            else:
                logger.info("skipped: %s", result.reason)
                log_scan(logger, self._scan_index, quotes, opportunity, f"skipped:{result.reason}")
            log_execution(logger, self._scan_index, result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Listen for DEX swap events and trigger arbitrage evaluation."
    )
    parser.add_argument(
        "--config",
        default="config/uniswap_pancake_config.json",
        help="Path to a JSON config file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Log opportunities without executing (default: true).",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between block polls (default: 2).",
    )
    return parser


def main() -> None:
    load_env()
    setup_logging()
    args = build_parser().parse_args()
    config = BotConfig.from_file(args.config)

    listener = SwapEventListener(
        config=config,
        dry_run=args.dry_run,
        poll_interval=args.poll_interval,
    )
    listener.run()


if __name__ == "__main__":
    main()
