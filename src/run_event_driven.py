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
import json
import logging
import os
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
from execution.bot import ArbitrageBot
from core.config import BotConfig, PairConfig
from core.env import get_rpc_overrides, load_env
from observability.log import get_logger, setup_logging
from core.models import ZERO, Opportunity, OpportunityStatus as Status
from observability.metrics import MetricsCollector
from market.onchain_market import OnChainMarket
from persistence.db import init_db
from persistence.repository import Repository
from pipeline.lifecycle import CandidatePipeline
from platform_adapters import CandidateQueue
from pipeline.verifier import OnChainVerifier, VerificationResult
from registry.monitored_pools import sync_monitored_pools
from platform_adapters import CircuitBreaker, CircuitBreakerConfig
from risk.policy import RiskPolicy
from strategy.scanner import OpportunityScanner
from strategy.arb_strategy import ArbitrageStrategy
from api.app import create_app

logger = get_logger(__name__)

D = Decimal


def _build_pair_list(config: BotConfig) -> list[PairConfig]:
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


def build_risk_policy(config: BotConfig) -> RiskPolicy:
    """Build a risk policy whose profit floors match the active config.

    The execution pipeline and on-chain contract must agree on the minimum
    profitable trade size. If the risk layer approves opportunities below
    ``config.min_profit_base``, the contract will still reject them via its
    ``minProfit`` check and we burn gas on avoidable reverts.

    To keep approval and execution aligned, every chain present in the loaded
    config inherits the config's ``min_profit_base`` as its minimum net profit.
    Unconfigured chains retain the broader library defaults.
    """
    configured_chains = {
        (dex.chain or "").lower() for dex in config.dexes if dex.chain
    }
    configured_chains.update(
        (pair.chain or "").lower() for pair in (config.extra_pairs or []) if pair.chain
    )

    chain_min_profit = {
        **RiskPolicy().chain_min_net_profit,
        **{chain: config.min_profit_base for chain in configured_chains if chain},
    }
    return RiskPolicy(
        execution_enabled=False,  # global default; per-chain modes override
        min_net_profit=config.min_profit_base,
        chain_min_net_profit=chain_min_profit,
        chain_execution_mode=dict(config.chain_execution_mode or {}),
    )


class ChainExecutorSimulator:
    """Pipeline simulator adapter backed by ChainExecutor's eth_call preflight."""

    def __init__(self, executor: "ChainExecutor") -> None:
        self.executor = executor

    def simulate(self, opportunity: Opportunity) -> tuple[bool, str]:
        tx_data = self.executor._build_transaction(opportunity)
        return self.executor._simulate_transaction(tx_data)


class OpportunityAwareVerifier:
    """Verifier adapter that keeps opportunity context for tx-level PnL conversion."""

    def __init__(self, executor: "ChainExecutor") -> None:
        self.executor = executor
        self._opps_by_tx: dict[str, Opportunity] = {}

    def remember_submission(self, tx_hash: str, opportunity: Opportunity) -> None:
        self._opps_by_tx[tx_hash] = opportunity

    def verify(self, tx_hash: str) -> VerificationResult:
        from core.tokens import token_decimals

        opp = self._opps_by_tx.get(tx_hash)
        quote_asset = opp.pair.split("/", 1)[1] if opp and "/" in opp.pair else self.executor.config.quote_asset
        verifier = OnChainVerifier(
            w3=self.executor.w3,
            contract_address=self.executor.contract_address,
            quote_decimals=token_decimals(quote_asset),
        )
        return verifier.verify(tx_hash, opp)


class ChainExecutorSubmitter:
    """Pipeline submitter adapter backed by ChainExecutor broadcast logic."""

    def __init__(
        self,
        executor: "ChainExecutor",
        verifier: OpportunityAwareVerifier | None = None,
    ) -> None:
        self.executor = executor
        self.verifier = verifier

    def submit(self, opportunity: Opportunity) -> tuple[str, str, int, str]:
        tx_data = self.executor._build_transaction(opportunity)
        tx_hash = self.executor._sign_and_send(tx_data)
        tx_hash_hex = tx_hash.hex()
        if self.verifier is not None:
            self.verifier.remember_submission(tx_hash_hex, opportunity)
        target_block = self.executor.w3.eth.block_number + 1 if self.executor.use_flashbots else 0
        bundle_id = f"flashbots:{target_block}" if self.executor.use_flashbots else ""
        submission_type = "flashbots" if self.executor.use_flashbots else "public"
        return tx_hash_hex, bundle_id, target_block, submission_type


class MultiChainSimulator:
    """Dispatch simulation to the correct per-chain ChainExecutor."""

    def __init__(self, simulators: dict[str, ChainExecutorSimulator]) -> None:
        self._by_chain = simulators

    def simulate(self, opportunity: Opportunity) -> tuple[bool, str]:
        chain = opportunity.chain.lower() if opportunity.chain else ""
        sim = self._by_chain.get(chain)
        if sim is None:
            return False, f"no_executor_for_chain:{chain}"
        return sim.simulate(opportunity)


class MultiChainSubmitter:
    """Dispatch submission to the correct per-chain ChainExecutor."""

    def __init__(self, submitters: dict[str, ChainExecutorSubmitter]) -> None:
        self._by_chain = submitters

    def submit(self, opportunity: Opportunity) -> tuple[str, str, int, str]:
        chain = opportunity.chain.lower() if opportunity.chain else ""
        sub = self._by_chain.get(chain)
        if sub is None:
            raise RuntimeError(f"No executor configured for chain '{chain}'")
        return sub.submit(opportunity)


class MultiChainVerifier:
    """Dispatch verification to the correct per-chain verifier."""

    def __init__(self, verifiers: dict[str, OpportunityAwareVerifier]) -> None:
        self._by_chain = verifiers

    def verify(self, tx_hash: str) -> VerificationResult:
        # Try each chain's verifier — the tx_hash is globally unique.
        for verifier in self._by_chain.values():
            if tx_hash in verifier._opps_by_tx:
                return verifier.verify(tx_hash)
        # Fallback: use first verifier (will likely fail gracefully).
        first = next(iter(self._by_chain.values()))
        return first.verify(tx_hash)


def build_execution_stack(
    config: BotConfig,
) -> tuple[MultiChainSimulator | None, MultiChainSubmitter | None, MultiChainVerifier | None]:
    """Build per-chain execution adapters and wrap in multi-chain dispatchers.

    Creates one ChainExecutor per chain that has:
      - EXECUTOR_PRIVATE_KEY set
      - EXECUTOR_CONTRACT or EXECUTOR_CONTRACT_{CHAIN} set
      - A valid RPC endpoint

    Chains that fail initialization are logged and skipped — the bot can
    still scan them but won't attempt live execution.
    """
    if not os.environ.get("EXECUTOR_PRIVATE_KEY"):
        logger.info("Live execution stack not configured — missing EXECUTOR_PRIVATE_KEY")
        return None, None, None

    from execution.chain_executor import ChainExecutor, ChainExecutorError

    # Collect all chains from config.
    chains: set[str] = set()
    for dex in config.dexes:
        if dex.chain:
            chains.add(dex.chain.lower())

    simulators: dict[str, ChainExecutorSimulator] = {}
    submitters: dict[str, ChainExecutorSubmitter] = {}
    verifiers: dict[str, OpportunityAwareVerifier] = {}

    for chain in sorted(chains):
        try:
            executor = ChainExecutor(config, chain=chain)
            verifier = OpportunityAwareVerifier(executor)
            simulators[chain] = ChainExecutorSimulator(executor)
            submitters[chain] = ChainExecutorSubmitter(executor, verifier=verifier)
            verifiers[chain] = verifier
            logger.info("Execution stack ready for chain=%s", chain)
        except ChainExecutorError as exc:
            logger.info("Execution stack skipped for chain=%s: %s", chain, exc)
        except Exception as exc:
            logger.warning("Execution stack failed for chain=%s: %s", chain, exc)

    if not simulators:
        logger.info("No chains have live execution configured")
        return None, None, None

    logger.info(
        "Multi-chain execution stack ready: %s",
        ", ".join(sorted(simulators.keys())),
    )
    return (
        MultiChainSimulator(simulators),
        MultiChainSubmitter(submitters),
        MultiChainVerifier(verifiers),
    )


def compute_live_execution_summary(config: BotConfig) -> dict[str, object]:
    """Summarize what part of the config is truly executable live today."""
    from execution.chain_executor import SUPPORTED_LIVE_DEX_TYPES

    executable_dexes = [
        dex for dex in config.dexes
        if dex.chain and dex.dex_type in SUPPORTED_LIVE_DEX_TYPES
    ]
    executable_chains = sorted({dex.chain for dex in executable_dexes if dex.chain})
    executable_dex_names = [dex.name for dex in executable_dexes]
    rollout_target = "arbitrum" if "arbitrum" in executable_chains else (executable_chains[0] if executable_chains else "")
    return {
        "executable_chains": executable_chains,
        "executable_dex_names": executable_dex_names,
        "rollout_target": rollout_target,
    }


def assess_launch_readiness(
    config: BotConfig,
    *,
    live_stack_ready: bool,
    target_chain: str = "arbitrum",
) -> dict[str, object]:
    """Check whether the current config/env is ready for a narrow live rollout."""
    from execution.chain_executor import AAVE_V3_POOL, SUPPORTED_LIVE_DEX_TYPES, SWAP_ROUTERS

    rpc_overrides = get_rpc_overrides()
    executor_key_configured = bool(os.environ.get("EXECUTOR_PRIVATE_KEY"))
    executor_contract_configured = bool(os.environ.get("EXECUTOR_CONTRACT"))
    dedicated_rpc_configured = bool(rpc_overrides.get(target_chain))

    blockers: list[str] = []
    target_dexes = [dex for dex in config.dexes if (dex.chain or "").lower() == target_chain]
    off_target_dexes = [dex.name for dex in config.dexes if (dex.chain or "").lower() != target_chain]
    unsupported_target_dexes = [
        dex.name for dex in target_dexes if dex.dex_type not in SUPPORTED_LIVE_DEX_TYPES
    ]
    off_target_pairs = [
        pair.pair for pair in (config.extra_pairs or [])
        if pair.chain and pair.chain.lower() != target_chain
    ]

    if not target_dexes:
        blockers.append(f"no_{target_chain}_dexes_configured")
    if off_target_dexes:
        blockers.append(f"off_target_dexes:{','.join(off_target_dexes)}")
    if unsupported_target_dexes:
        blockers.append(f"unsupported_dexes:{','.join(unsupported_target_dexes)}")
    if off_target_pairs:
        blockers.append(f"off_target_pairs:{','.join(off_target_pairs)}")
    if not executor_key_configured:
        blockers.append("missing_executor_private_key")
    if not executor_contract_configured:
        blockers.append("missing_executor_contract")
    if not dedicated_rpc_configured:
        blockers.append(f"missing_rpc_{target_chain}")
    if target_chain not in AAVE_V3_POOL:
        blockers.append(f"missing_aave_pool:{target_chain}")
    if target_chain not in SWAP_ROUTERS:
        blockers.append(f"missing_swap_router_registry:{target_chain}")
    if not live_stack_ready:
        blockers.append("live_stack_unavailable")

    return {
        "launch_chain": target_chain,
        "launch_ready": not blockers,
        "launch_blockers": blockers,
        "executor_key_configured": executor_key_configured,
        "executor_contract_configured": executor_contract_configured,
        "rpc_configured": dedicated_rpc_configured,
        "configured_dex_count": len(target_dexes),
    }


def enforce_safe_execution_mode(
    risk_policy: RiskPolicy,
    launch_readiness: dict[str, object],
) -> bool:
    """Force simulation mode when startup readiness is not satisfied."""
    if risk_policy.execution_enabled and not bool(launch_readiness.get("launch_ready")):
        risk_policy.execution_enabled = False
        logger.warning(
            "Execution forced to simulation mode: launch not ready (%s)",
            ", ".join(launch_readiness.get("launch_blockers", [])) or "unknown",
        )
        return False
    return bool(risk_policy.execution_enabled)


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

            # Record to latency.jsonl with per-stage timings.
            if self.latency_tracker:
                timings = result.timings or {}
                self.latency_tracker.record_pipeline(
                    opp_id=result.opportunity_id,
                    pair=opp.pair, chain=opp.chain,
                    buy_dex=opp.buy_dex, sell_dex=opp.sell_dex,
                    spread_pct=float(opp.gross_spread_pct),
                    net_profit=float(opp.net_profit_base),
                    status=result.final_status,
                    pipeline_timings={k: round(v, 2) for k, v in timings.items()},
                    scan_marks=candidate.scan_marks,
                )

            logger.info(
                "Pipeline [%s]: %s %s→%s spread=%.4f%% → %s (%s) [%.0fms]",
                opp.chain or "?", opp.pair, opp.buy_dex, opp.sell_dex,
                float(opp.gross_spread_pct), result.final_status,
                result.reason, latency_ms,
            )

            # Update metrics based on result.
            if result.final_status == Status.REJECTED:
                self.metrics.record_opportunity_rejected(result.reason)
            elif result.final_status in (Status.INCLUDED, Status.DRY_RUN):
                self.metrics.record_expected_profit(float(opp.net_profit_base))
                if result.final_status == Status.INCLUDED:
                    self.metrics.record_execution_submitted()
                    self.metrics.record_execution_result(
                        included=True, reverted=False,
                        actual_profit=float(result.net_profit),
                    )
                    self.breaker.record_execution_success()
            elif result.final_status == Status.SUBMITTED:
                self.metrics.record_execution_submitted()
            elif result.final_status == Status.REVERTED:
                self.metrics.record_execution_submitted()
                self.breaker.record_revert()
                self.metrics.record_execution_result(included=True, reverted=True)
            elif result.final_status == Status.NOT_INCLUDED:
                self.metrics.record_execution_submitted()
                self.metrics.record_execution_result(included=False, reverted=False)

            # Smart alerting.
            self.alerter.check_opportunity(
                spread_pct=opp.gross_spread_pct,
                pair=opp.pair, buy_dex=opp.buy_dex, sell_dex=opp.sell_dex,
                chain=opp.chain, net_profit=float(opp.net_profit_base),
                opp_id=result.opp_id,
            )
            self.alerter.maybe_send_hourly()
            self.alerter.maybe_send_daily()


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
        latency_tracker: "LatencyTracker | None" = None,
    ) -> None:
        self.config = config
        self.queue = queue
        self.scanner = scanner
        self.market = market
        self.metrics = metrics
        self.breaker = circuit_breaker
        self.poll_interval = poll_interval
        self.latency_tracker = latency_tracker
        self._running = False
        self._pairs = _build_pair_list(config)
        self._chain_strategy = ArbitrageStrategy(config, pairs=self._pairs)

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

            if self.latency_tracker:
                self.latency_tracker.start_scan()

            try:
                quotes = self.market.get_quotes()
                # Filter outliers (Sushi returning $115 when others show $2200).
                quotes = ArbitrageBot._filter_outliers(quotes)
                self.breaker.record_fresh_quote()
            except Exception as exc:
                logger.error("Market error: %s", exc)
                self.breaker.record_rpc_error()
                self.metrics.record_opportunity_rejected("market_error")
                if self.latency_tracker:
                    self.latency_tracker.mark("rpc_fetch")
                    self.latency_tracker.record_scan_summary(0, 0, status="market_error")
                time.sleep(self.poll_interval)
                continue

            if self.latency_tracker:
                self.latency_tracker.mark("rpc_fetch")

            if len(quotes) < 2:
                if self.latency_tracker:
                    self.latency_tracker.record_scan_summary(len(quotes), 0, status="insufficient_quotes")
                time.sleep(self.poll_interval)
                continue

            # Run scanner — get all ranked opportunities.
            result = self.scanner.scan_and_rank(quotes)

            # Flush scan history to DB async (background thread, no pipeline latency).
            scan_records = self.scanner.drain_scan_records()
            if scan_records:
                import threading
                from persistence.db import get_db
                from persistence.repository import Repository
                def _flush_scan_history(records):
                    try:
                        conn = get_db()
                        if conn is not None:
                            Repository(conn).save_scan_history(records)
                    except Exception as exc:
                        logger.warning("scan_history flush failed: %s", exc)
                threading.Thread(
                    target=_flush_scan_history,
                    args=(scan_records,),
                    daemon=True,
                ).start()

            if self.latency_tracker:
                self.latency_tracker.mark("scanner")

            # Snapshot scan marks so the consumer thread has them even
            # after this thread starts a new scan (fixes empty scan_marks_ms).
            scan_marks = self.latency_tracker.get_scan_marks() if self.latency_tracker else {}

            # Push all actionable opportunities to the queue.
            # Track (pair, buy_dex, sell_dex) keys to avoid enqueueing the
            # same opportunity twice (scanner + same-chain pass).
            pushed = 0
            _seen: set[tuple[str, str, str]] = set()
            for opp in result.opportunities:
                _key = (opp.pair, opp.buy_dex, opp.sell_dex)
                _seen.add(_key)
                # Compute a priority score for queue ordering.
                score = float(opp.net_profit_base) * (1 + opp.liquidity_score)
                if self.queue.push(opp, priority=score, scan_marks=scan_marks):
                    pushed += 1
                    self.metrics.record_opportunity_detected()

            # Also find same-chain opportunities (like run_live_with_dashboard does).
            chain_map: dict[str, list] = {}
            for q in quotes:
                parts = q.dex.rsplit("-", 1)
                ch = parts[1].lower() if len(parts) == 2 else ""
                if ch:
                    chain_map.setdefault(ch, []).append(q)

            # Use the strategy's evaluate_pair() to compute real costs
            # (DEX fees, flash loan fee, slippage, gas) per the config.
            for chain_name, chain_quotes in chain_map.items():
                if len(chain_quotes) < 2:
                    continue
                # Find best same-chain opportunity using the full cost model.
                chain_opp = self._chain_strategy.find_best_opportunity(chain_quotes)
                if chain_opp is None:
                    continue
                # Skip if already enqueued by the scanner pass.
                _key = (chain_opp.pair, chain_opp.buy_dex, chain_opp.sell_dex)
                if _key in _seen:
                    continue
                _seen.add(_key)
                # Duplicate the scanner's liquidity filter here because the
                # same-chain strategy pass bypasses scanner._find_all_opportunities.
                # Without this, thin-pool false positives (e.g., Camelot WETH/USDT
                # with $0 liquidity vs. Uniswap $22M) would reach the pipeline.
                # See scanner.py _find_all_opportunities for the full rationale.
                buy_liq = chain_opp.buy_liquidity_usd
                sell_liq = chain_opp.sell_liquidity_usd
                min_liq = min(buy_liq, sell_liq)
                max_liq = max(buy_liq, sell_liq)
                from core.config import BotConfig as _BC
                _min_liq_threshold = _BC.min_liquidity_for_chain(chain_name)
                if min_liq > D("0") and min_liq < _min_liq_threshold:
                    continue
                if min_liq == D("0") and max_liq > D("0"):
                    continue
                score = float(chain_opp.net_profit_base) * 0.5
                if self.queue.push(chain_opp, priority=score, scan_marks=scan_marks):
                    pushed += 1
                    self.metrics.record_opportunity_detected()

            if pushed > 0:
                logger.info(
                    "[scan %d] %d quotes → %d opportunities queued (queue size: %d)",
                    scan_count, len(quotes), pushed, self.queue.size,
                )
            else:
                logger.debug("[scan %d] %d quotes → no opportunities", scan_count, len(quotes))

            # Record scan summary for EVERY cycle — not just pipeline hits.
            if self.latency_tracker:
                self.latency_tracker.record_scan_summary(
                    quote_count=len(quotes),
                    opp_count=pushed,
                    rejected_count=result.rejected_count,
                    status="queued" if pushed > 0 else "no_opportunity",
                )

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
    parser.add_argument("--queue-size", type=int, default=333)
    args = parser.parse_args()

    config = BotConfig.from_file(args.config)

    # --- Infrastructure ---
    conn = init_db()
    repo = Repository(conn)
    synced_pools = sync_monitored_pools(repo)
    repo.set_checkpoint("monitored_pools_synced", str(synced_pools))
    logger.info(
        "Persistence backend=%s, monitored pools synced=%d, total enabled pools=%d",
        conn.backend, synced_pools, repo.count_enabled_pools(),
    )
    metrics = MetricsCollector()
    queue = CandidateQueue(max_size=args.queue_size)

    # --- Risk ---
    chain_modes = config.chain_execution_mode or {}
    any_live = any(m == "live" for m in chain_modes.values())
    risk_policy = build_risk_policy(config)
    if chain_modes:
        logger.info("Chain execution modes from config: %s", chain_modes)
    breaker = CircuitBreaker(CircuitBreakerConfig(
        max_reverts=3, revert_window_seconds=300,
        max_rpc_errors=10, rpc_error_window_seconds=60,
        max_stale_seconds=120,
        cooldown_seconds=300,
    ))

    # --- Pipeline ---
    simulator, submitter, verifier = build_execution_stack(config)
    live_summary = compute_live_execution_summary(config)
    launch_readiness = assess_launch_readiness(
        config,
        live_stack_ready=submitter is not None,
        target_chain=str(live_summary["rollout_target"] or "arbitrum"),
    )
    repo.set_checkpoint("live_stack_ready", "1" if submitter is not None else "0")
    repo.set_checkpoint("live_executable_chains", ",".join(live_summary["executable_chains"]))  # type: ignore[arg-type]
    repo.set_checkpoint("live_executable_dexes", ",".join(live_summary["executable_dex_names"]))  # type: ignore[arg-type]
    repo.set_checkpoint("live_rollout_target", str(live_summary["rollout_target"]))
    repo.set_checkpoint("launch_chain", str(launch_readiness["launch_chain"]))
    repo.set_checkpoint("launch_ready", "1" if launch_readiness["launch_ready"] else "0")
    repo.set_checkpoint("launch_blockers", json.dumps(launch_readiness["launch_blockers"]))
    repo.set_checkpoint("executor_key_configured", "1" if launch_readiness["executor_key_configured"] else "0")
    repo.set_checkpoint(
        "executor_contract_configured",
        "1" if launch_readiness["executor_contract_configured"] else "0",
    )
    repo.set_checkpoint("rpc_configured", "1" if launch_readiness["rpc_configured"] else "0")
    enforce_safe_execution_mode(risk_policy, launch_readiness)
    pipeline = CandidatePipeline(
        repo=repo,
        risk_policy=risk_policy,
        simulator=simulator,
        submitter=submitter,
        verifier=verifier,
    )
    logger.info(
        "Live execution readiness: stack=%s target=%s chains=%s",
        "ready" if submitter is not None else "simulation_only",
        live_summary["rollout_target"] or "none",
        ", ".join(live_summary["executable_chains"]) or "none",
    )
    logger.info(
        "Launch readiness: chain=%s ready=%s blockers=%s",
        launch_readiness["launch_chain"],
        "yes" if launch_readiness["launch_ready"] else "no",
        ", ".join(launch_readiness["launch_blockers"]) or "none",
    )

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

    dashboard_url = os.environ.get("DASHBOARD_URL", "")
    if not dashboard_url:
        # Auto-detect LAN IP so email links work from any device on the network.
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            s.close()
            dashboard_url = f"http://{lan_ip}:{args.port}/dashboard"
        except Exception:
            dashboard_url = f"http://localhost:{args.port}/dashboard"
        logger.info("Dashboard URL (auto-detected): %s", dashboard_url)
    alerter = SmartAlerter(repo=repo, telegram=telegram, discord=discord, gmail=gmail, dashboard_url=dashboard_url)
    alerter.start_background_hourly()

    # --- Auto-discover top pairs by volume/liquidity ---
    from registry.pair_refresher import PairRefresher
    pair_refresher = PairRefresher(
        chains=["ethereum", "arbitrum", "base", "polygon", "optimism", "bsc", "avax"],
        min_volume=100_000,
        min_dex_count=2,
        max_results=15,
        interval_seconds=3600,  # refresh every hour
        repository=repo,
    )
    pair_refresher.start()
    repo.set_checkpoint("discovery_snapshot_source", pair_refresher.snapshot_source)
    repo.set_checkpoint("discovery_pair_count", str(repo.count_discovered_pairs()))
    logger.info(
        "Discovery snapshot source=%s, cached discovered pairs=%d",
        pair_refresher.snapshot_source,
        repo.count_discovered_pairs(),
    )

    discovered = pair_refresher.get_pairs()
    if discovered:
        logger.info("Auto-discovered %d pairs — adding to scan list", len(discovered))
        from core.config import PairConfig
        extra = config.extra_pairs or []
        seen = {config.pair} | {p.pair for p in extra}
        for dp in discovered:
            pair_name = f"{dp.base_symbol}/{dp.quote_symbol}"
            if pair_name not in seen:
                extra.append(PairConfig(
                    pair=pair_name,
                    base_asset=dp.base_symbol,
                    quote_asset=dp.quote_symbol,
                    trade_size=config.trade_size,
                    base_address=dp.base_address or None,
                    quote_address=dp.quote_address or None,
                    chain=dp.chain,
                ))
                seen.add(pair_name)
                logger.info("  + %s on %s (%d DEXes, $%.0f vol)",
                           pair_name, dp.chain, dp.dex_count, dp.total_volume_24h)
        object.__setattr__(config, 'extra_pairs', extra)
    else:
        logger.warning("Pair discovery returned 0 pairs — using config pairs only")

    all_pairs = _build_pair_list(config)

    # --- Factory pool discovery (runs once — pool addresses are immutable) ---
    rpc = get_rpc_overrides()
    try:
        from registry.pool_discovery import discover_and_persist_pools
        factory_count = discover_and_persist_pools(
            repo=repo,
            chains=["ethereum", "arbitrum", "base", "optimism"],
            pairs=all_pairs,
            rpc_overrides=rpc,
        )
        repo.set_checkpoint("factory_discovery_count", str(factory_count))
        logger.info("Factory pool discovery: %d new pools found", factory_count)
    except Exception as exc:
        logger.warning("Factory pool discovery failed (non-fatal): %s", exc)

    # --- Quote diagnostics ---
    from observability.quote_diagnostics import QuoteDiagnostics
    diagnostics = QuoteDiagnostics()
    diagnostics.start_periodic_flush(repo, interval_seconds=300.0)

    # --- Market ---
    market = OnChainMarket(config, rpc_overrides=rpc or None, pairs=all_pairs, diagnostics=diagnostics)
    scanner = OpportunityScanner(config, pairs=all_pairs)

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
        latency_tracker=latency_tracker,
    )

    # Register scanner and diagnostics with API.
    from api.app import set_scanner_ref, set_diagnostics_ref
    set_scanner_ref(event_scanner)
    set_diagnostics_ref(diagnostics)

    # Graceful shutdown.
    def _shutdown(sig, frame):
        logger.info("Shutting down (signal %d)...", sig)
        event_scanner.stop()
        consumer.stop()
        alerter.stop()
        pair_refresher.stop()

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
