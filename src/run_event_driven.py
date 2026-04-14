"""Production event-driven arbitrage scanner.

Architecture (per architecture doc):
  Swap event detected → scanner ranks → queue → pipeline consumer → DB → dashboard

Flow:
  1. Event listener polls blocks for Swap events on monitored pools
  2. On swap: fetch fresh quotes, run scanner, push opportunities to queue
  3. Pipeline consumer thread pops from queue, runs full lifecycle:
     detect → price → risk → simulate → submit → verify
  4. Each stage persists to DB (visible on dashboard)
  5. Circuit breaker monitors for reverts/stale data/RPC errors
  6. Alerting fires on big wins (>5% → Telegram) and hourly (→ Gmail)

This replaces the polling-based run_live_with_dashboard.py with a
production-ready event-driven flow.

Usage::

    PYTHONPATH=src python -m run_event_driven \\
        --config config/multichain_onchain_config.json --port 8000
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
import time
from decimal import Decimal

import uvicorn

from alerting.dispatcher import AlertDispatcher
from alerting.smart_alerts import SmartAlerter
from alerting.telegram import TelegramAlert
from alerting.discord import DiscordAlert
from alerting.gmail import GmailAlert
from bot import ArbitrageBot
from config import BotConfig
from env import get_rpc_overrides, load_env
from log import get_logger, setup_logging
from models import ZERO, Opportunity
from observability.metrics import MetricsCollector
from onchain_market import OnChainMarket
from persistence.db import init_db
from persistence.repository import Repository
from pipeline.lifecycle import CandidatePipeline
from pipeline.queue import CandidateQueue
from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from risk.policy import RiskPolicy
from scanner import OpportunityScanner
from api.app import create_app

logger = get_logger(__name__)

D = Decimal


class PipelineConsumer:
    """Background *consumer* thread that drains the ``CandidateQueue`` and
    processes each opportunity through the full candidate lifecycle pipeline.

    Threading model
    ~~~~~~~~~~~~~~~
    Runs as a single **daemon thread** (``pipeline-consumer``) started via
    ``start()`` and stopped via ``stop()``.  The thread polls the shared
    ``CandidateQueue`` at a configurable interval (default 0.5 s).  Because
    the queue is a thread-safe ``queue.PriorityQueue``, no external locking
    is required between the producer (``EventDrivenScanner``) and this
    consumer.

    Coordination via the queue
    ~~~~~~~~~~~~~~~~~~~~~~~~~~
    The ``EventDrivenScanner`` (running on the **main thread**) pushes
    ``Opportunity`` objects onto the queue with a priority score.  This
    consumer pops the highest-priority item, checks the ``CircuitBreaker``
    (which may block execution if too many reverts or RPC errors have
    occurred), and feeds the opportunity through ``CandidatePipeline`` --
    the full detect -> price -> risk -> simulate -> submit -> verify
    lifecycle.  Results are recorded to metrics, latency tracker, and the
    smart alerter.

    Runs until ``stop()`` sets ``_running = False``, at which point the
    thread drains naturally and joins within 5 seconds.
    """

    def __init__(
        self,
        queue: CandidateQueue,
        pipeline: CandidatePipeline,
        circuit_breaker: CircuitBreaker,
        metrics: MetricsCollector,
        alerter: SmartAlerter,
        latency_tracker: "LatencyTracker | None" = None,
        poll_interval: float = 0.5,
    ) -> None:
        self.queue = queue
        self.pipeline = pipeline
        self.breaker = circuit_breaker
        self.metrics = metrics
        self.alerter = alerter
        self.latency_tracker = latency_tracker
        self.poll_interval = poll_interval
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the consumer thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="pipeline-consumer")
        self._thread.start()
        logger.info("Pipeline consumer started")

    def stop(self) -> None:
        """Stop the consumer thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Pipeline consumer stopped")

    def _run(self) -> None:
        """Main consumer loop — pop from queue, check breaker, process."""
        while self._running:
            candidate = self.queue.pop()
            if candidate is None:
                time.sleep(self.poll_interval)
                continue

            opp = candidate.opportunity

            # Check circuit breaker before processing.
            allowed, reason = self.breaker.allows_execution()
            if not allowed:
                logger.warning("Circuit breaker OPEN (%s) — skipping %s", reason, opp.pair)
                self.metrics.record_opportunity_rejected(f"circuit_breaker:{reason}")
                continue

            # Record fresh quote to keep stale-data timer alive.
            self.breaker.record_fresh_quote()

            # Process through full pipeline.
            start_ms = time.time() * 1000
            result = self.pipeline.process(opp)
            latency_ms = time.time() * 1000 - start_ms
            self.metrics.record_latency_ms(latency_ms)

            # Record to latency.jsonl for detailed analysis.
            if self.latency_tracker:
                self.latency_tracker.record_pipeline(
                    opp_id=result.opportunity_id,
                    pair=opp.pair, chain=opp.chain,
                    buy_dex=opp.buy_dex, sell_dex=opp.sell_dex,
                    spread_pct=float(opp.gross_spread_pct),
                    net_profit=float(opp.net_profit_base),
                    status=result.final_status,
                    pipeline_timings={"total_ms": str(latency_ms)},
                )

            logger.info(
                "Pipeline [%s]: %s %s→%s spread=%.4f%% → %s (%s) [%.0fms]",
                opp.chain or "?", opp.pair, opp.buy_dex, opp.sell_dex,
                float(opp.gross_spread_pct), result.final_status,
                result.reason, latency_ms,
            )

            # Update metrics based on result.
            if result.final_status == "rejected":
                self.metrics.record_opportunity_rejected(result.reason)
            elif result.final_status in ("included", "dry_run"):
                self.metrics.record_expected_profit(float(opp.net_profit_base))
                if result.final_status == "included":
                    self.metrics.record_execution_submitted()
                    self.metrics.record_execution_result(
                        included=True, reverted=False,
                        actual_profit=float(result.net_profit),
                    )
                    self.breaker.record_execution_success()
            elif result.final_status == "reverted":
                self.breaker.record_revert()
                self.metrics.record_execution_result(included=True, reverted=True)

            # Smart alerting.
            self.alerter.check_opportunity(
                spread_pct=opp.gross_spread_pct,
                pair=opp.pair, buy_dex=opp.buy_dex, sell_dex=opp.sell_dex,
                chain=opp.chain, net_profit=float(opp.net_profit_base),
            )
            self.alerter.maybe_send_hourly()


class EventDrivenScanner:
    """**Producer** that polls for swap events, scans for arbitrage
    opportunities, and pushes them onto the shared ``CandidateQueue``.

    Threading model
    ~~~~~~~~~~~~~~~
    Runs on the **main thread** (via ``run()``).  This is intentional: the
    main thread handles OS signals (SIGINT / SIGTERM) for graceful shutdown,
    and Python's ``signal`` module requires signal handlers to be registered
    from the main thread.  The complementary ``PipelineConsumer`` runs as a
    background daemon thread.

    Coordination via the queue
    ~~~~~~~~~~~~~~~~~~~~~~~~~~
    After each poll cycle the scanner:

    1. Fetches fresh ``MarketQuote`` objects from ``OnChainMarket``.
    2. Filters outlier quotes (e.g. a DEX returning a stale price that is
       orders of magnitude off).
    3. Runs the ``OpportunityScanner`` to rank cross-DEX opportunities.
    4. Builds same-chain opportunities by grouping quotes by chain suffix
       and comparing the cheapest buy vs. the most expensive sell within
       each chain (identical logic to ``run_live_with_dashboard.py``).
    5. Pushes all actionable opportunities onto the ``CandidateQueue`` with
       a priority score proportional to expected profit.

    The consumer thread picks these up asynchronously, decoupling the
    scan cadence from the (potentially slower) pipeline processing time.

    In the current implementation, event detection uses **HTTP RPC polling**
    at a configurable interval (default 2 s).  A future enhancement would
    replace this with WebSocket subscriptions for lower latency.
    """

    def __init__(
        self,
        config: BotConfig,
        queue: CandidateQueue,
        scanner: OpportunityScanner,
        market: OnChainMarket,
        metrics: MetricsCollector,
        circuit_breaker: CircuitBreaker,
        poll_interval: float = 2.0,
    ) -> None:
        self.config = config
        self.queue = queue
        self.scanner = scanner
        self.market = market
        self.metrics = metrics
        self.breaker = circuit_breaker
        self.poll_interval = poll_interval
        self._running = False

    def run(self) -> None:
        """Main event loop — poll for swaps, scan, push to queue.

        For now uses polling (HTTP RPC). In production with WebSocket RPCs,
        this would be replaced with async event subscriptions.
        """
        self._running = True
        logger.info(
            "Event scanner started: %d DEXs, poll every %.1fs",
            len(self.config.dexes), self.poll_interval,
        )

        scan_count = 0
        while self._running:
            scan_count += 1
            self.metrics.record_opportunity_detected()

            try:
                quotes = self.market.get_quotes()
                # Filter outliers (Sushi returning $115 when others show $2200).
                quotes = ArbitrageBot._filter_outliers(quotes)
                self.breaker.record_fresh_quote()
            except Exception as exc:
                logger.error("Market error: %s", exc)
                self.breaker.record_rpc_error()
                self.metrics.record_opportunity_rejected("market_error")
                time.sleep(self.poll_interval)
                continue

            if len(quotes) < 2:
                time.sleep(self.poll_interval)
                continue

            # Run scanner — get all ranked opportunities.
            result = self.scanner.scan_and_rank(quotes)

            # Push all actionable opportunities to the queue.
            pushed = 0
            for opp in result.opportunities:
                # Compute a priority score for queue ordering.
                score = float(opp.net_profit_base) * (1 + opp.liquidity_score)
                if self.queue.push(opp, priority=score):
                    pushed += 1

            # Also find same-chain opportunities (like run_live_with_dashboard does).
            chain_map: dict[str, list] = {}
            for q in quotes:
                parts = q.dex.rsplit("-", 1)
                ch = parts[1].lower() if len(parts) == 2 else ""
                if ch:
                    chain_map.setdefault(ch, []).append(q)

            # Use the strategy's evaluate_pair() to compute real costs
            # (DEX fees, flash loan fee, slippage, gas) per the config.
            from strategy import ArbitrageStrategy
            chain_strategy = ArbitrageStrategy(self.config)
            for chain_name, chain_quotes in chain_map.items():
                if len(chain_quotes) < 2:
                    continue
                # Find best same-chain opportunity using the full cost model.
                chain_opp = chain_strategy.find_best_opportunity(chain_quotes)
                if chain_opp is not None:
                    score = float(chain_opp.net_profit_base) * 0.5
                    self.queue.push(chain_opp, priority=score)
                    pushed += 1

            if pushed > 0:
                logger.info(
                    "[scan %d] %d quotes → %d opportunities queued (queue size: %d)",
                    scan_count, len(quotes), pushed, self.queue.size,
                )
            else:
                logger.debug("[scan %d] %d quotes → no opportunities", scan_count, len(quotes))

            time.sleep(self.poll_interval)

    def stop(self) -> None:
        self._running = False


def main() -> None:
    load_env()
    setup_logging()

    parser = argparse.ArgumentParser(description="Event-driven arbitrage scanner with dashboard")
    parser.add_argument("--config", default="config/multichain_onchain_config.json")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--poll-interval", type=float, default=8.0,
                        help="Seconds between scans (default: 8)")
    parser.add_argument("--queue-size", type=int, default=100)
    args = parser.parse_args()

    config = BotConfig.from_file(args.config)

    # --- Infrastructure ---
    conn = init_db()
    repo = Repository(conn)
    metrics = MetricsCollector()
    queue = CandidateQueue(max_size=args.queue_size)

    # --- Risk ---
    risk_policy = RiskPolicy(
        execution_enabled=False,  # dry-run by default
        min_net_profit=0.0005,    # low for testing (~$1). Production: 0.005 (~$10)
    )
    breaker = CircuitBreaker(CircuitBreakerConfig(
        max_reverts=3, revert_window_seconds=300,
        max_rpc_errors=10, rpc_error_window_seconds=60,
        max_stale_seconds=120,
        cooldown_seconds=300,
    ))

    # --- Pipeline ---
    pipeline = CandidatePipeline(repo=repo, risk_policy=risk_policy)

    # --- Alerting ---
    telegram = TelegramAlert()
    discord = DiscordAlert()
    gmail = GmailAlert()
    dispatcher = AlertDispatcher()
    for backend in [telegram, discord, gmail]:
        if backend.configured:
            dispatcher.add_backend(backend)
            logger.info("Alerting: %s enabled", backend.name)
    if dispatcher.backend_count == 0:
        logger.warning("Alerting: no backends configured")

    dashboard_url = f"http://localhost:{args.port}/dashboard"
    alerter = SmartAlerter(repo=repo, telegram=telegram, gmail=gmail, dashboard_url=dashboard_url)
    alerter.start_background_hourly()

    # --- Market ---
    rpc = get_rpc_overrides()
    market = OnChainMarket(config, rpc_overrides=rpc or None)
    scanner = OpportunityScanner(config)

    # --- Dashboard ---
    app = create_app(risk_policy=risk_policy, repo=repo, metrics=metrics)
    dashboard_thread = threading.Thread(
        target=lambda: uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning"),
        daemon=True,
    )
    dashboard_thread.start()
    logger.info("Dashboard at http://localhost:%d/dashboard", args.port)

    # --- Latency tracker ---
    from observability.latency_tracker import LatencyTracker
    latency_tracker = LatencyTracker()
    logger.info("Latency tracking → logs/latency.jsonl")

    # --- Consumer (queue → pipeline) ---
    consumer = PipelineConsumer(
        queue=queue, pipeline=pipeline, circuit_breaker=breaker,
        metrics=metrics, alerter=alerter, latency_tracker=latency_tracker,
    )
    consumer.start()

    # --- Producer (events → scanner → queue) ---
    event_scanner = EventDrivenScanner(
        config=config, queue=queue, scanner=scanner,
        market=market, metrics=metrics, circuit_breaker=breaker,
        poll_interval=args.poll_interval,
    )

    # Register scanner with API so it can be controlled via /scanner/start|stop.
    from api.app import set_scanner_ref
    set_scanner_ref(event_scanner)

    # Graceful shutdown.
    def _shutdown(sig, frame):
        logger.info("Shutting down (signal %d)...", sig)
        event_scanner.stop()
        consumer.stop()
        alerter.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info(
        "Event-driven scanner ready: %d DEXs, queue=%d, poll=%.1fs",
        len(config.dexes), args.queue_size, args.poll_interval,
    )
    logger.info("Flow: events → scanner → queue → pipeline → DB → dashboard")

    # Run the event scanner in the main thread.
    try:
        event_scanner.run()
    except KeyboardInterrupt:
        pass
    finally:
        consumer.stop()
        alerter.stop()
        logger.info("Shutdown complete. Queue stats: %s", queue.stats())
        logger.info("Metrics: %s", metrics.snapshot())


if __name__ == "__main__":
    main()
