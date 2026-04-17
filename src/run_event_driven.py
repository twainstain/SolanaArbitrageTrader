"""Production scanner loop for SolanaTrader (Phase 1).

Polls Jupiter for quotes on a short interval, runs the scanner, queues
detected opportunities, and processes them through the full pipeline
(detect → price → risk) with full latency tracking.

No on-chain execution.  The submitter/verifier slots are left unset so
``CandidatePipeline`` lands every approved candidate in the ``dry_run``
terminal state.  Phase 3 wires in a real submitter and verifier.

Usage::

    PYTHONPATH=src python -m run_event_driven --config config/example_config.json

Signals: SIGINT/SIGTERM trigger a graceful shutdown; the current scan
completes before the loop exits.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
import time
from decimal import Decimal
from queue import Queue, Empty

from alerting.dispatcher import AlertDispatcher
from control_state import get_control
from core.config import BotConfig
from core.env import get_bot_config_path, get_bot_mode, load_env
from market.sim_market import SimulatedMarket
from observability.latency_tracker import LatencyTracker
from observability.log import get_logger, setup_logging
from observability.metrics import MetricsCollector
from persistence.db import init_db
from persistence.repository import Repository
from pipeline.lifecycle import CandidatePipeline
from risk.policy import RiskPolicy
from strategy.arb_strategy import ArbitrageStrategy
from strategy.scanner import OpportunityScanner

try:
    import uvicorn
    from api.app import create_app, set_metrics_ref, set_scanner_ref
    _HAS_API = True
except ImportError:
    _HAS_API = False

D = Decimal
logger = get_logger(__name__)


def build_risk_policy(config: BotConfig) -> RiskPolicy:
    return RiskPolicy(
        min_net_profit=config.min_profit_base,
        min_spread_pct=D("0.05"),
        max_slippage_bps=config.slippage_bps,
        min_liquidity_usd=D("100000"),
        max_quote_age_seconds=10.0,
        max_fee_profit_ratio=D("0.5"),
        max_warning_flags=1,
        max_trades_per_hour=100,
        max_exposure_per_pair=D("100"),
        min_liquidity_score=0.3,
        execution_enabled=False,   # Phase 1: scanner-only
    )


def _build_market(config: BotConfig, mode: str):
    if mode == "jupiter":
        # "jupiter" kept as the flag name for backward compat, but the
        # market is actually Jupiter + Raydium + Orca fanned out in parallel.
        from market.multi_venue_market import MultiVenueMarket
        from market.solana_market import SolanaMarket
        from market.raydium_market import RaydiumMarket
        from market.orca_market import OrcaMarket
        return MultiVenueMarket([
            ("Jupiter", SolanaMarket(config)),
            ("Raydium", RaydiumMarket()),
            ("Orca",    OrcaMarket()),
        ])
    return SimulatedMarket(config)


class Consumer(threading.Thread):
    """Pipeline consumer — pops opportunities off the queue and processes them."""

    def __init__(
        self,
        queue: Queue,
        pipeline: CandidatePipeline,
        tracker: LatencyTracker,
        metrics: MetricsCollector,
        stop: threading.Event,
    ) -> None:
        super().__init__(daemon=True, name="pipeline-consumer")
        self.queue = queue
        self.pipeline = pipeline
        self.tracker = tracker
        self.metrics = metrics
        self.stop = stop

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                item = self.queue.get(timeout=0.5)
            except Empty:
                continue
            opp, scan_marks = item
            try:
                result = self.pipeline.process(opp)
                self.metrics.record_opportunity_detected()
                if result.final_status == "rejected":
                    self.metrics.record_opportunity_rejected(result.reason)
                self.tracker.record_pipeline(
                    opp_id=result.opportunity_id,
                    pair=opp.pair,
                    buy_venue=opp.buy_venue,
                    sell_venue=opp.sell_venue,
                    spread_pct=float(opp.gross_spread_pct),
                    net_profit=float(opp.net_profit_base),
                    status=result.final_status,
                    pipeline_timings=result.timings or {},
                    scan_marks=scan_marks,
                )
            except Exception as exc:
                logger.exception("[consumer] pipeline failed: %s", exc)


def run(
    config_path: str,
    mode: str,
    iterations: int,
    sleep_seconds: float | None,
    api_port: int | None = None,
) -> None:
    config = BotConfig.from_file(config_path)
    sleep_s = sleep_seconds if sleep_seconds is not None else config.poll_interval_seconds

    db = init_db()
    repo = Repository(db)
    tracker = LatencyTracker()
    metrics = MetricsCollector()
    market = _build_market(config, mode)
    strategy = ArbitrageStrategy(config)
    scanner = OpportunityScanner(config, strategy=strategy)
    policy = build_risk_policy(config)
    dispatcher = AlertDispatcher()
    pipeline = CandidatePipeline(
        repo=repo,
        risk_policy=policy,
        simulator=None,
        submitter=None,    # Phase 1: scanner-only
        verifier=None,
        dispatcher=dispatcher,
    )

    queue: Queue = Queue(maxsize=256)
    stop = threading.Event()
    consumer = Consumer(queue, pipeline, tracker, metrics, stop)
    consumer.start()

    # --- Optional HTTP dashboard -----------------------------------
    api_thread: threading.Thread | None = None
    if api_port and _HAS_API:
        app = create_app(repo=repo, risk_policy=policy)
        set_metrics_ref(metrics)
        set_scanner_ref(scanner)

        def _serve():
            uvicorn.run(app, host="0.0.0.0", port=api_port, log_level="warning")

        api_thread = threading.Thread(target=_serve, daemon=True, name="api-server")
        api_thread.start()
        logger.info(
            "[api] dashboard running at http://localhost:%d/dashboard  (user=%s)",
            api_port, os.environ.get("DASHBOARD_USER", "admin"),
        )

    def _shutdown(signum, frame):
        logger.info("Received %s — shutting down", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info(
        "[loop] Starting scanner-only run: mode=%s pair=%s iterations=%s sleep=%.2fs",
        mode, config.pair, iterations, sleep_s,
    )
    control = get_control()
    scan_count = 0
    try:
        while not stop.is_set() and (iterations == 0 or scan_count < iterations):
            scan_count += 1
            tracker.start_scan()

            # Pause gate — operator-triggered, via API or the kill-switch file.
            if control.paused:
                logger.debug("[loop] paused — skipping scan %d", scan_count)
                if stop.wait(sleep_s):
                    break
                continue

            # Stage 1 — RPC fetch
            try:
                quotes = market.get_quotes()
            except Exception as exc:
                logger.warning("[loop] market fetch failed: %s", exc)
                dispatcher.system_error("market", str(exc))
                if stop.wait(sleep_s):
                    break
                continue
            # Venue / pair runtime disable — drop quotes we've been told
            # to skip before they enter the scanner.
            if control.disabled_venues or control.disabled_pairs:
                quotes = [
                    q for q in quotes
                    if control.venue_enabled(q.venue)
                    and control.pair_enabled(q.pair)
                ]
            tracker.mark("rpc_fetch")

            # Stage 2 — scan
            result = scanner.scan_and_rank(quotes)
            tracker.mark("scanner")

            # Persist scan history (fire and forget on error)
            records = scanner.drain_scan_records()
            if records:
                try:
                    repo.save_scan_history(records)
                except Exception as exc:
                    logger.debug("[loop] scan_history save failed: %s", exc)

            # Stage 3 — queue opportunities for the pipeline
            for opp in result.opportunities:
                scan_marks = tracker.get_scan_marks()
                try:
                    queue.put_nowait((opp, scan_marks))
                except Exception:
                    logger.warning("[loop] queue full — dropping opportunity %s", opp.pair)

            status = "queued" if result.opportunities else "no_opportunity"
            tracker.record_scan_summary(
                quote_count=len(quotes),
                opp_count=len(result.opportunities),
                rejected_count=result.rejected_count,
                status=status,
            )

            if stop.wait(sleep_s):
                break
    finally:
        logger.info("[loop] stopping consumer …")
        stop.set()
        consumer.join(timeout=5.0)
        tracker.close()
        logger.info("[loop] done. scans=%d metrics=%s", scan_count, metrics.snapshot())


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description="SolanaTrader scanner loop")
    parser.add_argument("--config", default=None)
    parser.add_argument("--iterations", type=int, default=0, help="0 = run until stopped")
    parser.add_argument("--sleep", type=float, default=None, help="override poll interval")
    parser.add_argument(
        "--mode",
        choices=["simulated", "jupiter"],
        default=None,
        help="market source (default: BOT_MODE env or 'simulated')",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="HTTP port for the dashboard/API.  Omit to disable the server.",
    )
    args = parser.parse_args()

    setup_logging(level=logging.INFO)
    config_path = args.config or get_bot_config_path()
    mode = args.mode or get_bot_mode() or "simulated"
    port = args.port if args.port else int(os.environ.get("DASHBOARD_PORT", "0")) or None
    run(config_path=config_path, mode=mode, iterations=args.iterations,
        sleep_seconds=args.sleep, api_port=port)


if __name__ == "__main__":
    main()
