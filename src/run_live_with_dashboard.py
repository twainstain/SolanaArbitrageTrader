"""Run live scanning across all chains with a web dashboard.

When to use this vs ``run_event_driven``
----------------------------------------
* **run_live_with_dashboard** (this module) -- A simpler, **polling-based**
  scanner aimed at DeFi Llama aggregated prices (``--live`` mode, default) or
  on-chain RPC quotes (``--onchain``).  It runs a fixed number of iterations
  with a configurable sleep between scans.  Best suited for initial
  exploration, dashboards, and dry-run monitoring where you want a predictable
  scan cadence and a quick visual overview.

* **run_event_driven** -- A **producer/consumer** architecture with a
  priority queue.  Designed for production: the scanner pushes opportunities
  onto a thread-safe queue and a background consumer processes them through
  the full lifecycle pipeline.  Supports circuit breakers, latency tracking,
  and higher throughput.  Use this when you need continuous, indefinite
  scanning with robust error handling.

Per-chain scanning logic
------------------------
After fetching quotes from all DEXs (across all configured chains), the
module groups quotes by chain (extracted from the DEX name suffix, e.g.
``"UniswapV3-Ethereum"`` -> ``"ethereum"``).  For each chain with at least
two DEX quotes, it identifies the cheapest buy price and the highest sell
price, then constructs a same-chain ``Opportunity`` with full cost
accounting (DEX fees, flash-loan fees, slippage, gas).

Why same-chain opportunities are built separately
-------------------------------------------------
The ``OpportunityScanner`` evaluates *all* quote pairs globally, which may
produce cross-chain opportunities (e.g. buy on Arbitrum, sell on Ethereum).
Cross-chain arb cannot be executed atomically in a single flash-loan
transaction, so it is out of scope for automatic execution.  By explicitly
grouping quotes per chain and building same-chain opportunities, this module
ensures the dashboard always shows the best *executable* spread on each
chain, alongside the global best (which may or may not be cross-chain).

Starts:
  1. FastAPI dashboard on port 8000 (background thread)
  2. Live bot scanning all configured chains
  3. Every scan result is fed through the pipeline -> DB -> dashboard

Usage::

    PYTHONPATH=src python -m run_live_with_dashboard --iterations 10
"""

from __future__ import annotations

import argparse
import signal
import threading
import time

import uvicorn

from config import BotConfig
from env import load_env
from log import setup_logging, get_logger
from live_market import LiveMarket
from strategy import ArbitrageStrategy
from scanner import OpportunityScanner
from persistence.db import init_db
from persistence.repository import Repository
from pipeline.lifecycle import CandidatePipeline
from risk.policy import RiskPolicy
from risk.circuit_breaker import CircuitBreaker
from observability.metrics import MetricsCollector
from api.app import create_app

logger = get_logger(__name__)


def build_live_config() -> BotConfig:
    """Build a config that scans all 12 chains via DeFi Llama."""
    return BotConfig.from_file("config/live_config.json")


def run_dashboard(app, port: int = 8000) -> None:
    """Run the FastAPI dashboard in a background thread."""
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


def _build_pair_list(config: BotConfig, discovered_pairs=None):
    from config import PairConfig

    if discovered_pairs:
        return list(discovered_pairs)

    pairs = [
        PairConfig(
            pair=config.pair,
            base_asset=config.base_asset,
            quote_asset=config.quote_asset,
            trade_size=config.trade_size,
        )
    ]
    if config.extra_pairs:
        pairs.extend(config.extra_pairs)
    return pairs


def main() -> None:
    load_env()
    setup_logging()

    parser = argparse.ArgumentParser(description="Live scanning with dashboard")
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--sleep", type=float, default=30.0, help="Seconds between scans")
    parser.add_argument("--onchain", action="store_true",
                        help="Use on-chain RPC quotes (per-DEX) instead of DeFi Llama (aggregated)")
    parser.add_argument("--config", default=None,
                        help="Config file (default: live_config or multichain_discovery_config)")
    parser.add_argument("--discover", action="store_true",
                        help="Run DexScreener discovery first — find best pairs by volume + multi-DEX presence")
    args = parser.parse_args()

    # --- Bot ---
    config_path = args.config
    if config_path is None:
        config_path = "config/multichain_onchain_config.json" if args.onchain else "config/live_config.json"
    config = BotConfig.from_file(config_path)

    # --- Discovery (per video recommendations) ---
    discovered_pairs = None
    if args.discover:
        from registry.discovery import discover_best_pairs, print_discovery_report
        from config import PairConfig
        logger.info("Running pair discovery (sort by volume, multi-exchange, ERC-20)...")
        discovered = discover_best_pairs(
            chains=["ethereum", "arbitrum", "base", "polygon", "optimism", "avalanche"],
            min_volume=100_000,
            min_dex_count=2,
            max_results=15,
        )
        print_discovery_report(discovered)
        logger.info("Discovery complete — %d pairs found", len(discovered))
        discovered_pairs = [
            PairConfig(
                pair=f"{dp.base_symbol}/{dp.quote_symbol}",
                base_asset=dp.base_symbol,
                quote_asset=dp.quote_symbol,
                trade_size=config.trade_size,
                base_address=dp.base_address or None,
                quote_address=dp.quote_address or None,
                chain=dp.chain,
            )
            for dp in discovered
        ]

    # --- Init persistence ---
    conn = init_db()
    repo = Repository(conn)
    metrics = MetricsCollector()

    # --- Risk policy (dry-run: execution disabled) ---
    risk_policy = RiskPolicy(
        execution_enabled=False,  # dry-run: detect + price + risk, don't execute
        min_net_profit=0.0005,  # low for testing (~$1). Production: 0.005 (~$10)
    )

    # --- Pipeline (dispatcher wired after alerting init below) ---
    pipeline = None  # initialized after dispatcher

    # --- Alerting: full dispatcher + smart rules ---
    from alerting.dispatcher import AlertDispatcher
    from alerting.telegram import TelegramAlert
    from alerting.discord import DiscordAlert
    from alerting.gmail import GmailAlert
    from alerting.smart_alerts import SmartAlerter

    telegram = TelegramAlert()
    discord = DiscordAlert()
    gmail = GmailAlert()

    dispatcher = AlertDispatcher()
    if telegram.configured:
        dispatcher.add_backend(telegram)
        logger.info("Alerting: Telegram enabled")
    if discord.configured:
        dispatcher.add_backend(discord)
        logger.info("Alerting: Discord enabled")
    if gmail.configured:
        dispatcher.add_backend(gmail)
        logger.info("Alerting: Gmail enabled")
    if dispatcher.backend_count == 0:
        logger.warning("Alerting: no backends configured (set TELEGRAM_BOT_TOKEN, DISCORD_WEBHOOK_URL, or GMAIL_ADDRESS)")

    dashboard_url = f"http://localhost:{args.port}/dashboard"
    alerter = SmartAlerter(repo=repo, telegram=telegram, gmail=gmail, dashboard_url=dashboard_url)
    alerter.start_background_hourly()

    # --- Pipeline (with dispatcher for alerts on reverts/failures) ---
    pipeline = CandidatePipeline(repo=repo, risk_policy=risk_policy, dispatcher=dispatcher)

    # --- Dashboard ---
    app = create_app(risk_policy=risk_policy, repo=repo, metrics=metrics)
    dashboard_thread = threading.Thread(
        target=run_dashboard, args=(app, args.port), daemon=True,
    )
    dashboard_thread.start()
    logger.info("Dashboard running at http://localhost:%d/dashboard", args.port)

    # --- Graceful shutdown ---
    shutdown_requested = False

    def _handle_shutdown(signum, frame):
        nonlocal shutdown_requested
        sig_name = signal.Signals(signum).name
        logger.info("Received %s — draining current scan then stopping", sig_name)
        shutdown_requested = True
        alerter.stop()

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    all_pairs = _build_pair_list(config, discovered_pairs)

    if args.onchain:
        from onchain_market import OnChainMarket
        from env import get_rpc_overrides
        from observability.quote_diagnostics import QuoteDiagnostics
        rpc = get_rpc_overrides()
        diagnostics = QuoteDiagnostics()
        market = OnChainMarket(
            config,
            rpc_overrides=rpc or None,
            pairs=all_pairs,
            diagnostics=diagnostics,
        )
        logger.info("[mode] ON-CHAIN — querying DEX contracts via RPC")
    else:
        market = LiveMarket(config, pairs=all_pairs)
        logger.info("[mode] LIVE — DeFi Llama aggregated prices")
    scanner = OpportunityScanner(config, pairs=all_pairs)

    logger.info("Starting live scan: %d iterations, %d chains, sleep=%.0fs",
                args.iterations, len(config.dexes), args.sleep)
    logger.info("Pairs: %s", ", ".join(p.pair for p in all_pairs))

    for i in range(1, args.iterations + 1):
        if shutdown_requested:
            logger.info("Shutdown requested — stopping before scan %d", i)
            break

        logger.info("--- Scan %d/%d ---", i, args.iterations)
        metrics.record_opportunity_detected()  # track scan count

        try:
            quotes = market.get_quotes()
            logger.info("Got %d quotes across %d venues", len(quotes), len(config.dexes))
        except Exception as exc:
            logger.error("Market error: %s", exc)
            metrics.record_opportunity_rejected("market_error")
            dispatcher.system_error("market", str(exc))
            if i < args.iterations:
                time.sleep(args.sleep)
            continue

        # Filter outlier quotes (e.g., Sushi returning $115 when others show $2200).
        from bot import ArbitrageBot
        quotes = ArbitrageBot._filter_outliers(quotes)

        # Run scanner — get ALL opportunities, not just the best.
        result = scanner.scan_and_rank(quotes)

        if not result.opportunities:
            logger.info("No opportunity (evaluated %d candidates, rejected %d)",
                        result.rejected_count, result.rejected_count)
            if i < args.iterations:
                time.sleep(args.sleep)
            continue

        # In on-chain mode, also find best same-chain opportunities per chain.
        # Group quotes by chain, find same-chain spreads.
        chain_map: dict[str, list] = {}
        for q in quotes:
            # Extract chain from DEX name (e.g., "Uniswap-Ethereum" → "ethereum")
            parts = q.dex.rsplit("-", 1)
            ch = parts[1].lower() if len(parts) == 2 else ""
            if ch:
                chain_map.setdefault(ch, []).append(q)

        # Process same-chain opportunities per chain using the full cost model
        # (DEX fees, flash loan fee, slippage, gas) from strategy.evaluate_pair().
        processed_chains = set()
        from strategy import ArbitrageStrategy
        chain_strategy = ArbitrageStrategy(config, pairs=all_pairs)

        for chain_name, chain_quotes in chain_map.items():
            if len(chain_quotes) < 2:
                continue

            # Use the strategy's evaluate_pair which computes real costs.
            opp = chain_strategy.find_best_opportunity(chain_quotes)
            if opp is None:
                continue

            processed_chains.add(chain_name)
            logger.info(
                "Same-chain [%s]: %s buy=%s sell=%s spread=%.4f%% net=%.6f",
                chain_name, opp.pair, opp.buy_dex, opp.sell_dex,
                float(opp.gross_spread_pct), float(opp.net_profit_base),
            )
            pipeline.process(opp)

        # Process the overall best through the pipeline.
        # Cross-chain opportunities get recorded but rejected so they show
        # on the dashboard with a clear reason.
        opp = result.best
        buy_chain = opp.buy_dex.rsplit("-", 1)[-1].lower() if "-" in opp.buy_dex else opp.buy_dex.lower()
        sell_chain = opp.sell_dex.rsplit("-", 1)[-1].lower() if "-" in opp.sell_dex else opp.sell_dex.lower()
        is_cross_chain = buy_chain != sell_chain

        if is_cross_chain:
            logger.info(
                "Best overall is CROSS-CHAIN: %s buy=%s sell=%s spread=%.4f%%",
                opp.pair, opp.buy_dex, opp.sell_dex,
                float(opp.gross_spread_pct),
            )
            # Record in DB as rejected so it shows on dashboard
            opp_id = repo.create_opportunity(
                pair=opp.pair, chain=opp.chain,
                buy_dex=opp.buy_dex, sell_dex=opp.sell_dex,
                spread_bps=opp.gross_spread_pct,
            )
            repo.save_pricing(
                opp_id=opp_id,
                input_amount=opp.cost_to_buy_quote,
                estimated_output=opp.proceeds_from_sell_quote,
                fee_cost=opp.dex_fee_cost_quote,
                slippage_cost=opp.slippage_cost_quote,
                gas_estimate=opp.gas_cost_base,
                expected_net_profit=opp.net_profit_base,
                buy_liquidity_usd=opp.buy_liquidity_usd,
                sell_liquidity_usd=opp.sell_liquidity_usd,
            )
            repo.save_risk_decision(
                opp_id=opp_id, approved=False,
                reason_code="cross_chain",
                threshold_snapshot=f"buy_chain={buy_chain}, sell_chain={sell_chain}",
            )
            repo.update_opportunity_status(opp_id, "rejected")
        else:
            logger.info(
                "Best overall (same-chain): %s buy=%s sell=%s spread=%.4f%% net=%.6f",
                opp.pair, opp.buy_dex, opp.sell_dex,
                float(opp.gross_spread_pct), float(opp.net_profit_base),
            )
            pipeline_result = pipeline.process(opp)
            logger.info("Pipeline result: %s — %s", pipeline_result.final_status, pipeline_result.reason)
            metrics.record_expected_profit(float(opp.net_profit_base))

        logger.info("Processed %d same-chain + %d cross-chain opportunities",
                     len(processed_chains), 1 if is_cross_chain else 0)

        # Smart alerting: Telegram for big wins (>5%), hourly email otherwise.
        alerter.check_opportunity(
            spread_pct=opp.gross_spread_pct,
            pair=opp.pair, buy_dex=opp.buy_dex, sell_dex=opp.sell_dex,
            chain=opp.chain, net_profit=float(opp.net_profit_base),
        )
        alerter.maybe_send_hourly()

        if i < args.iterations:
            time.sleep(args.sleep)

    alerter.stop()
    logger.info("Scanning complete. Dashboard remains active — send SIGTERM or Ctrl+C to exit.")
    logger.info("View at http://localhost:%d/dashboard", args.port)

    if not shutdown_requested:
        try:
            while not shutdown_requested:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    logger.info("Shutting down gracefully.")


if __name__ == "__main__":
    main()
