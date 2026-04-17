"""SolanaTrader CLI — parses args, loads .env, selects market source, runs bot."""

from __future__ import annotations

import argparse
import signal

from execution.bot import ArbitrageBot
from core.config import BotConfig
from core.env import (
    get_bot_config_path,
    get_bot_dry_run,
    get_bot_iterations,
    get_bot_mode,
    get_bot_no_sleep,
    load_env,
)
from observability.log import get_logger, setup_logging

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser and load ``.env`` defaults."""
    load_env()

    parser = argparse.ArgumentParser(
        description="Run the SolanaTrader arbitrage scanner.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to a JSON config file (default: BOT_CONFIG from .env).",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Number of scan cycles to run (default: BOT_ITERATIONS from .env).",
    )
    parser.add_argument(
        "--no-sleep",
        action="store_true",
        default=None,
        help="Disable sleeping between scans.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Quote-only mode: log opportunities without executing trades.",
    )
    parser.add_argument(
        "--execute-live",
        action="store_true",
        help="Enable real Solana transaction submission.  Requires "
             "SOLANA_EXECUTION_ENABLED=true and SOLANA_WALLET_KEYPAIR_PATH in .env. "
             "Prompts for confirmation before sending any tx.",
    )

    market_group = parser.add_mutually_exclusive_group()
    market_group.add_argument(
        "--jupiter",
        action="store_true",
        help="Use SolanaMarket (Jupiter v6 quote API). Requires network access.",
    )
    market_group.add_argument(
        "--simulated",
        action="store_true",
        help="Use SimulatedMarket (deterministic synthetic quotes).",
    )
    return parser


def _resolve_mode(args: argparse.Namespace) -> str:
    """Resolve market-data source.  CLI > env > default (simulated)."""
    if args.jupiter:
        return "jupiter"
    if args.simulated:
        return "simulated"
    return get_bot_mode()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging()

    config_path = args.config or get_bot_config_path()
    iterations = args.iterations if args.iterations is not None else get_bot_iterations()
    dry_run = args.dry_run if args.dry_run else get_bot_dry_run()
    no_sleep = args.no_sleep if args.no_sleep else get_bot_no_sleep()

    config = BotConfig.from_file(config_path)
    mode = _resolve_mode(args)

    # --- Market source ---
    market = None
    if mode == "jupiter":
        from market.multi_venue_market import MultiVenueMarket
        from market.solana_market import SolanaMarket
        from market.raydium_market import RaydiumMarket
        from market.orca_market import OrcaMarket

        market = MultiVenueMarket([
            ("Jupiter", SolanaMarket(config)),
            ("Raydium", RaydiumMarket()),
            ("Orca",    OrcaMarket()),
        ])
        logger.info("[mode] MULTI-VENUE — Jupiter + Raydium + Orca (parallel)")
    else:
        logger.info("[mode] SIMULATED")

    # --- Executor ---
    executor = None
    if args.execute_live:
        from execution.solana_executor import SolanaExecutor

        # Extra CLI-level gate: require the operator to type 'yes' on stdin.
        # This is on top of the env-var gate inside SolanaExecutor.__init__.
        print("\n" + "=" * 60)
        print("  ⚠  LIVE SOLANA EXECUTION REQUESTED")
        print("=" * 60)
        print(f"  Config:        {config_path}")
        print(f"  Primary pair:  {config.pair}   (size: {config.trade_size} {config.base_asset})")
        print(f"  Min profit:    {config.min_profit_base} {config.base_asset}")
        print(f"  Priority fee:  {config.priority_fee_lamports} lamports")
        print("=" * 60)
        confirmation = input("  Type 'yes' to enable LIVE execution: ").strip().lower()
        if confirmation != "yes":
            logger.error("Live execution NOT enabled (confirmation was %r)", confirmation)
            return
        executor = SolanaExecutor(config)    # raises if any safety gate fails
        logger.warning("[executor] LIVE — wallet=%s", executor.wallet.pubkey)
    if executor is None:
        logger.info("[executor] PAPER — simulated execution (Phase 1/2)")

    bot = ArbitrageBot(config, market=market, executor=executor)

    def _handle_shutdown(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info("Received %s — requesting graceful shutdown", sig_name)
        bot.request_shutdown()

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    bot.run(iterations=iterations, sleep=not no_sleep, dry_run=dry_run)


if __name__ == "__main__":
    main()
