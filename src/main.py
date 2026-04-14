"""CLI entrypoint -- parses args, loads .env, selects market source, runs bot."""

from __future__ import annotations

import argparse
import signal

from bot import ArbitrageBot
from config import BotConfig
from env import (
    get_bot_config_path,
    get_bot_dry_run,
    get_bot_iterations,
    get_bot_mode,
    get_bot_no_sleep,
    get_rpc_overrides,
    load_env,
)
from log import get_logger, setup_logging

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser and load ``.env`` defaults.

    Loads environment variables from ``.env`` (via ``load_env()``) before
    constructing the parser so that default values can reference env vars.

    The parser exposes:
    * ``--config`` / ``--iterations`` / ``--no-sleep`` / ``--dry-run`` --
      general run parameters whose defaults come from ``.env`` when the CLI
      flag is not provided (resolved in ``main()``).
    * ``--execute`` -- opt-in to real on-chain execution via ChainExecutor.
    * ``--discover`` / ``--discover-chain`` / ``--discover-min-volume`` --
      DexScreener-based live pair discovery.
    * Market source group (mutually exclusive): ``--live``, ``--onchain``,
      ``--subgraph``, ``--historical``.  Only one may be specified; if none
      is given, ``_resolve_mode()`` falls back to the ``BOT_MODE`` env var
      (default: simulated).
    """
    load_env()

    parser = argparse.ArgumentParser(description="Run the Python arbitrage bot repro.")
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
        "--execute",
        action="store_true",
        help="Use ChainExecutor for real on-chain ERC-20 execution. "
             "Requires EXECUTOR_PRIVATE_KEY and EXECUTOR_CONTRACT in .env. "
             "Without this flag, PaperExecutor is always used (safe default).",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Query DexScreener at startup to discover live cross-DEX pairs. "
             "Discovered pairs are passed directly to the bot.",
    )
    parser.add_argument(
        "--discover-chain",
        default=None,
        help="Filter discovery to a specific chain (default: all chains).",
    )
    parser.add_argument(
        "--discover-min-volume",
        type=float,
        default=50_000,
        help="Minimum 24h volume for --discover (default: 50000).",
    )

    market_group = parser.add_mutually_exclusive_group()
    market_group.add_argument(
        "--live",
        action="store_true",
        help="Use LiveMarket (DeFi Llama aggregated prices per chain).",
    )
    market_group.add_argument(
        "--onchain",
        action="store_true",
        help="Use OnChainMarket (web3.py RPC calls to DEX contracts).",
    )
    market_group.add_argument(
        "--subgraph",
        action="store_true",
        help="Use SubgraphMarket (The Graph subgraph queries). Requires THEGRAPH_API_KEY.",
    )
    market_group.add_argument(
        "--historical",
        nargs="+",
        metavar="FILE",
        help="Use HistoricalMarket — replay downloaded JSON data files for backtesting.",
    )
    return parser


def _resolve_mode(args: argparse.Namespace) -> str:
    """Determine the market data mode with a clear priority chain.

    Resolution order (first match wins):

    1. **CLI flag** -- ``--live``, ``--onchain``, ``--subgraph``, or
       ``--historical`` explicitly set on the command line.
    2. **Environment variable** -- ``BOT_MODE`` in ``.env`` (read by
       ``get_bot_mode()``).  Accepted values: ``"live"``, ``"onchain"``,
       ``"subgraph"``, ``"historical"``, ``"simulated"``.
    3. **Default** -- ``"simulated"`` (returned by ``get_bot_mode()`` when
       ``BOT_MODE`` is unset).

    This ensures that one-off CLI invocations always override persistent env
    config, while ``.env`` provides convenient defaults for repeated runs.
    """
    if args.live:
        return "live"
    if args.onchain:
        return "onchain"
    if args.subgraph:
        return "subgraph"
    if args.historical:
        return "historical"
    return get_bot_mode()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging()

    # Apply .env defaults where CLI didn't provide a value.
    config_path = args.config or get_bot_config_path()
    iterations = args.iterations if args.iterations is not None else get_bot_iterations()
    dry_run = args.dry_run if args.dry_run else get_bot_dry_run()
    no_sleep = args.no_sleep if args.no_sleep else get_bot_no_sleep()

    config = BotConfig.from_file(config_path)
    mode = _resolve_mode(args)

    # --- Live pair discovery (passed directly to bot, not through config) ---
    discovered_pairs = None
    if args.discover:
        from pair_scanner import discover_pairs_for_bot

        discovered_pairs = discover_pairs_for_bot(
            chain=args.discover_chain,
            min_volume=args.discover_min_volume,
        )
        if discovered_pairs:
            logger.info("[discover] %d live pairs will be scanned", len(discovered_pairs))
        else:
            logger.info("[discover] No pairs discovered — falling back to config pairs")
            discovered_pairs = None

    # --- Market source ---
    market = None
    if mode == "live":
        from live_market import LiveMarket

        # Pass discovered pairs to LiveMarket so it can price ANY token.
        market = LiveMarket(config, pairs=discovered_pairs)
        logger.info("[mode] LIVE — fetching prices from DeFi Llama")
    elif mode == "onchain":
        from onchain_market import OnChainMarket

        rpc = get_rpc_overrides()
        market = OnChainMarket(config, rpc_overrides=rpc or None, pairs=discovered_pairs)
        logger.info("[mode] ON-CHAIN — querying DEX contracts via RPC")
    elif mode == "subgraph":
        from subgraph_market import SubgraphMarket

        market = SubgraphMarket(config)
        logger.info("[mode] SUBGRAPH — querying The Graph for per-DEX pool prices")
    elif mode == "historical":
        from historical_market import HistoricalMarket

        market = HistoricalMarket(config, data_files=args.historical)
        iterations = min(iterations, market.total_ticks)
        logger.info(
            "[mode] HISTORICAL — replaying %d ticks from %d data file(s)",
            market.total_ticks, len(args.historical),
        )
    else:
        logger.info("[mode] SIMULATED")

    # --- Executor ---
    executor = None
    if args.execute:
        if dry_run:
            logger.warning("--execute and --dry-run both set — dry-run takes precedence, "
                           "no real transactions will be sent.")
        else:
            from chain_executor import ChainExecutor

            executor = ChainExecutor(config)
            logger.info("[executor] ON-CHAIN — real ERC-20 execution via FlashArbExecutor")
    if executor is None:
        logger.info("[executor] PAPER — simulated execution")

    bot = ArbitrageBot(config, market=market, executor=executor, pairs=discovered_pairs)

    def _handle_shutdown(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info("Received %s — requesting graceful shutdown", sig_name)
        bot.request_shutdown()

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    bot.run(iterations=iterations, sleep=not no_sleep, dry_run=dry_run)


if __name__ == "__main__":
    main()
