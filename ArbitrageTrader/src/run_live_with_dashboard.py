"""Run live scanning across all chains with the dashboard.

Starts:
  1. FastAPI dashboard on port 8000 (background thread)
  2. Live bot scanning all 12 chains via DeFi Llama
  3. Every scan result is fed through the pipeline → DB → dashboard

Usage:
    PYTHONPATH=src python -m run_live_with_dashboard --iterations 10
"""

from __future__ import annotations

import argparse
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

    # --- Discovery (per video recommendations) ---
    if args.discover:
        from registry.discovery import discover_best_pairs, print_discovery_report
        logger.info("Running pair discovery (sort by volume, multi-exchange, ERC-20)...")
        discovered = discover_best_pairs(
            chains=["ethereum", "arbitrum", "base", "polygon", "optimism", "avalanche"],
            min_volume=100_000,
            min_dex_count=2,
            max_results=15,
        )
        print_discovery_report(discovered)
        logger.info("Discovery complete — %d pairs found", len(discovered))

    # --- Init persistence ---
    conn = init_db()
    repo = Repository(conn)
    metrics = MetricsCollector()

    # --- Risk policy (dry-run: execution disabled) ---
    risk_policy = RiskPolicy(
        execution_enabled=False,  # dry-run: detect + price + risk, don't execute
        min_net_profit=0,  # capture all opportunities for dashboard visibility
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

    # --- Bot ---
    config_path = args.config
    if config_path is None:
        config_path = "config/multichain_onchain_config.json" if args.onchain else "config/live_config.json"
    config = BotConfig.from_file(config_path)

    if args.onchain:
        from onchain_market import OnChainMarket
        from env import get_rpc_overrides
        rpc = get_rpc_overrides()
        market = OnChainMarket(config, rpc_overrides=rpc or None)
        logger.info("[mode] ON-CHAIN — querying DEX contracts via RPC")
    else:
        market = LiveMarket(config)
        logger.info("[mode] LIVE — DeFi Llama aggregated prices")
    scanner = OpportunityScanner(config)

    logger.info("Starting live scan: %d iterations, %d chains, sleep=%.0fs",
                args.iterations, len(config.dexes), args.sleep)
    logger.info("Pairs: %s + %s",
                config.pair,
                ", ".join(p.pair for p in (config.extra_pairs or [])))

    for i in range(1, args.iterations + 1):
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

        # Process same-chain opportunities per chain.
        # Use a zero-threshold config so we capture ALL chains even with tiny spreads.
        processed_chains = set()
        from copy import copy
        from decimal import Decimal as _D
        from strategy import ArbitrageStrategy
        from models import Opportunity, ZERO as _ZERO

        for chain_name, chain_quotes in chain_map.items():
            if len(chain_quotes) < 2:
                continue

            # Find best spread on this chain — even if negative after fees.
            # Sort by raw price difference to find the best buy/sell pair.
            cheapest = min(chain_quotes, key=lambda q: q.buy_price)
            priciest = max(chain_quotes, key=lambda q: q.sell_price)
            if cheapest.dex == priciest.dex:
                continue

            mid = (cheapest.buy_price + priciest.sell_price) / _D("2")
            if mid <= _ZERO:
                continue
            trade_size = config.trade_size
            buy_cost = cheapest.buy_price * trade_size
            sell_proceeds = priciest.sell_price * trade_size

            # Compute realistic costs.
            buy_fee_bps = cheapest.fee_bps
            sell_fee_bps = priciest.fee_bps
            buy_cost_with_fee = buy_cost / (_D("1") - buy_fee_bps / _D("10000"))
            sell_after_fee = sell_proceeds * (_D("1") - sell_fee_bps / _D("10000"))
            flash_fee = buy_cost * (config.flash_loan_fee_bps / _D("10000"))
            slippage = buy_cost * (config.slippage_bps / _D("10000"))
            dex_fee_cost = (buy_cost_with_fee - buy_cost) + (sell_proceeds - sell_after_fee)

            gross_spread = sell_proceeds - buy_cost
            gross_spread_pct = gross_spread / buy_cost * _D("100") if buy_cost > _ZERO else _ZERO
            net_profit_quote = sell_after_fee - buy_cost_with_fee - flash_fee - slippage
            net_profit_base = (net_profit_quote / mid) - config.estimated_gas_cost_base

            # Liquidity assessment.
            min_liq = min(cheapest.liquidity_usd, priciest.liquidity_usd)
            liq_score = 1.0
            if min_liq > _ZERO:
                import math
                liq_score = min(1.0, math.log10(max(float(min_liq), 1)) / 7.0)

            # Warning flags.
            flags = []
            if min_liq > _ZERO and min_liq < _D("100000"):
                flags.append("low_liquidity")
            min_vol = min(cheapest.volume_usd, priciest.volume_usd)
            if min_vol > _ZERO and min_vol < _D("50000"):
                flags.append("thin_market")
            if dex_fee_cost + flash_fee + slippage > _ZERO and gross_spread > _ZERO:
                fee_ratio = (dex_fee_cost + flash_fee + slippage) / gross_spread
                if fee_ratio > _D("0.8"):
                    flags.append("high_fee_ratio")
            if net_profit_base <= _ZERO:
                flags.append("negative_after_costs")

            # Resolve chain from DEX config.
            chain_val = ""
            for dc in config.dexes:
                if dc.name == cheapest.dex and dc.chain:
                    chain_val = dc.chain
                    break

            opp = Opportunity(
                pair=config.pair,
                buy_dex=cheapest.dex,
                sell_dex=priciest.dex,
                trade_size=trade_size,
                cost_to_buy_quote=buy_cost_with_fee,
                proceeds_from_sell_quote=sell_after_fee,
                gross_profit_quote=gross_spread * trade_size,
                net_profit_quote=net_profit_quote,
                net_profit_base=net_profit_base,
                gross_spread_pct=gross_spread_pct,
                dex_fee_cost_quote=dex_fee_cost,
                flash_loan_fee_quote=flash_fee,
                slippage_cost_quote=slippage,
                gas_cost_base=config.estimated_gas_cost_base,
                warning_flags=tuple(flags),
                liquidity_score=liq_score,
                chain=chain_val,
            )

            processed_chains.add(chain_name)
            logger.info(
                "Same-chain [%s]: %s buy=%s sell=%s spread=%.4f%% net=%.6f",
                chain_name, opp.pair, opp.buy_dex, opp.sell_dex,
                float(opp.gross_spread_pct), float(opp.net_profit_base),
            )
            pipeline.process(opp)

        # Also process the overall best (may be cross-chain).
        opp = result.best
        logger.info(
            "Best overall: %s buy=%s sell=%s spread=%.4f%% net=%.6f",
            opp.pair, opp.buy_dex, opp.sell_dex,
            float(opp.gross_spread_pct), float(opp.net_profit_base),
        )
        pipeline_result = pipeline.process(opp)
        logger.info("Pipeline result: %s — %s", pipeline_result.final_status, pipeline_result.reason)
        metrics.record_expected_profit(float(opp.net_profit_base))

        logger.info("Processed %d same-chain + 1 overall opportunity", len(processed_chains))

        # Smart alerting: Telegram for big wins (>5%), hourly email otherwise.
        alerter.check_opportunity(
            spread_pct=opp.gross_spread_pct,
            pair=opp.pair, buy_dex=opp.buy_dex, sell_dex=opp.sell_dex,
            chain=opp.chain, net_profit=float(opp.net_profit_base),
        )
        alerter.maybe_send_hourly()

        if i < args.iterations:
            time.sleep(args.sleep)

    logger.info("Done. Dashboard remains active — press Ctrl+C to exit.")
    logger.info("View at http://localhost:%d/dashboard", args.port)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down.")


if __name__ == "__main__":
    main()
