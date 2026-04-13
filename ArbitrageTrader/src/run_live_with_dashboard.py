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
                        help="Config file (default: live_config or multichain_onchain_config)")
    args = parser.parse_args()

    # --- Init persistence ---
    conn = init_db()
    repo = Repository(conn)
    metrics = MetricsCollector()

    # --- Risk policy (dry-run: execution disabled) ---
    risk_policy = RiskPolicy(
        execution_enabled=False,  # dry-run: detect + price + risk, don't execute
        min_net_profit=0.0005,
    )

    # --- Pipeline ---
    pipeline = CandidatePipeline(repo=repo, risk_policy=risk_policy)

    # --- Smart Alerting ---
    from alerting.smart_alerts import SmartAlerter
    dashboard_url = f"http://localhost:{args.port}/dashboard"
    alerter = SmartAlerter(repo=repo, dashboard_url=dashboard_url)
    alerter.start_background_hourly()

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
            if i < args.iterations:
                time.sleep(args.sleep)
            continue

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
        processed_chains = set()
        from strategy import ArbitrageStrategy
        chain_strategy = ArbitrageStrategy(config)

        for chain_name, chain_quotes in chain_map.items():
            if len(chain_quotes) < 2:
                continue
            chain_opp = chain_strategy.find_best_opportunity(chain_quotes)
            if chain_opp is not None:
                processed_chains.add(chain_name)
                logger.info(
                    "Same-chain [%s]: %s buy=%s sell=%s spread=%.4f%% net=%.6f",
                    chain_name, chain_opp.pair, chain_opp.buy_dex, chain_opp.sell_dex,
                    float(chain_opp.gross_spread_pct), float(chain_opp.net_profit_base),
                )
                pipeline.process(chain_opp)

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
