"""Candidate lifecycle pipeline.

Orchestrates the full arbitrage candidate flow:
  detected → priced → risk_approved/rejected → simulated → submitted → outcome

Six sequential stages. Each persists to the database before the next runs,
so the entire decision is auditable and replayable after the fact.

Stage overview:
  1. DETECT   — always runs.  Creates the opportunity record in DB.
  2. PRICE    — always runs.  Persists the full cost breakdown (fees,
                slippage, gas, net profit) computed by the strategy layer.
  3. RISK     — always runs.  Evaluates 8 sequential rules.  Rejects ~99%
                of opportunities.  This is the core safety gate.
  4. SIMULATE — optional (needs simulator wired).  Free eth_call dry-run
                on-chain.  Catches reverts before spending real gas.
  5. SUBMIT   — optional (needs submitter wired).  Signs the transaction
                and broadcasts via Flashbots (Ethereum) or public mempool (L2s).
  6. VERIFY   — optional (needs verifier wired).  Fetches the transaction
                receipt, extracts realized profit, reconciles vs expected.

Stages 1-3 run inside a single batched DB transaction — either all three
persist or none do (atomic).  The intermediate "priced" status is never
visible to other readers, saving one DB round-trip (~3-4ms on Postgres).

Stages 4-6 persist independently because they involve external calls
(RPC, mempool) that can take seconds.

Wiring modes (controlled by env vars at startup):
  - Simulation only:  simulator=None, submitter=None, verifier=None
    Pipeline exits at stage 3 with "simulation_approved" or "rejected".
  - Dry-run:          simulator=set, submitter=None, verifier=None
    Pipeline exits at stage 4 with "dry_run" or "simulation_failed".
  - Live execution:   simulator=set, submitter=set, verifier=set
    Pipeline runs all 6 stages → "included", "reverted", or "not_included".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol

from alerting.dispatcher import AlertDispatcher
from core.models import ZERO, MarketQuote, Opportunity, OpportunityStatus as Status
from persistence.repository import Repository
from pipeline.verifier import VerificationResult
from risk.policy import RiskPolicy, RiskVerdict

D = Decimal
logger = logging.getLogger(__name__)


class Simulator(Protocol):
    """Protocol for transaction simulation (stage 4).

    Implementor: ChainExecutorSimulator — wraps ChainExecutor._simulate_transaction()
    which calls eth_call (free, no gas) to check if the tx would revert.

    Returns (True, "ok") if simulation passes, (False, reason) if it would revert.
    Common revert reasons: insufficient profit, bad route, token not approved.
    """
    def simulate(self, opportunity: Opportunity) -> tuple[bool, str]: ...


class Submitter(Protocol):
    """Protocol for transaction submission (stage 5).

    Implementor: ChainExecutorSubmitter — signs the tx and broadcasts it.
    On Ethereum: Flashbots private relay (MEV protection, no gas on failure).
    On L2s: public mempool (cheap gas, no Flashbots equivalent).

    Returns (tx_hash, bundle_id, target_block, submission_type).
    """
    def submit(self, opportunity: Opportunity) -> tuple[str, str, int] | tuple[str, str, int, str]:
        ...


class ResultVerifier(Protocol):
    """Protocol for verifying on-chain results (stage 6).

    Implementor: OpportunityAwareVerifier → OnChainVerifier — fetches the
    transaction receipt, extracts the ProfitRealized event from logs,
    calculates gas cost, and reconciles expected vs actual profit.

    Returns VerificationResult with: included, reverted, gas_used,
    realized_profit_quote, gas_cost_base, actual_profit_base.
    """
    def verify(self, tx_hash: str) -> VerificationResult: ...


@dataclass
class PipelineResult:
    """Outcome of processing one candidate through the pipeline."""
    opportunity_id: str
    final_status: str
    reason: str
    net_profit: Decimal = ZERO
    timings: dict | None = None


class CandidatePipeline:
    """Process arbitrage candidates through the full lifecycle.

    Each stage persists its result to the database before proceeding.
    If any stage fails, the pipeline stops and records the rejection.
    """

    def __init__(
        self,
        repo: Repository,
        risk_policy: RiskPolicy,
        simulator: Simulator | None = None,
        submitter: Submitter | None = None,
        verifier: ResultVerifier | None = None,
        dispatcher: AlertDispatcher | None = None,
    ) -> None:
        self.repo = repo
        self.risk_policy = risk_policy
        self.simulator = simulator
        self.submitter = submitter
        self.verifier = verifier
        self.dispatcher = dispatcher or AlertDispatcher()

    def process(self, opportunity: Opportunity) -> PipelineResult:
        """Run a single opportunity through the full pipeline.

        DESIGN DECISION: Each stage persists before proceeding to the next.
        This ensures full auditability — reviewers can see exactly where and
        why a trade was rejected (risk filter? simulation fail? revert?).
        The pipeline stops on any failure because downstream stages depend
        on upstream success (e.g., can't simulate without valid pricing).

        Stages:
          1. Detect & persist
          2. Price & persist
          3. Risk evaluate & persist
          4. Simulate & persist (if simulator available)
          5. Submit & persist (if submitter available)
          6. Verify & persist (if verifier available)
        """
        import time as _time
        _t0 = _time.monotonic()
        _timings: dict[str, float] = {}

        # Batch all DB writes into a single commit for the detect→price→risk path.
        with self.repo.conn.batch():
            return self._process_inner(opportunity, _t0, _timings)

    def _process_inner(self, opportunity: Opportunity, _t0: float, _timings: dict[str, float]) -> PipelineResult:
        import time as _time

        # --- Stage 1: Detection ---
        # Always runs.  Creates the DB record that all subsequent stages
        # reference via opp_id.  This is the point of no return — once
        # created, the opportunity is visible on the dashboard regardless
        # of whether it passes risk evaluation.
        opp_id = self.repo.create_opportunity(
            pair=opportunity.pair,
            chain=opportunity.chain,
            buy_dex=opportunity.buy_dex,
            sell_dex=opportunity.sell_dex,
            spread_bps=opportunity.gross_spread_pct,
        )
        _timings["detect_ms"] = (_time.monotonic() - _t0) * 1000
        logger.info("[pipeline] %s detected: %s buy=%s sell=%s",
                     opp_id, opportunity.pair, opportunity.buy_dex, opportunity.sell_dex)

        # --- Stage 2: Pricing ---
        # Always runs.  Persists the full cost breakdown computed by
        # ArbitrageStrategy.evaluate_pair().  Note: the pricing math
        # already happened in the scanner — this stage just persists it.
        # Values stored: input cost, estimated output, fees, slippage,
        # gas estimate, expected net profit, pool liquidity.
        _t1 = _time.monotonic()
        self.repo.save_pricing(
            opp_id=opp_id,
            input_amount=opportunity.cost_to_buy_quote,
            estimated_output=opportunity.proceeds_from_sell_quote,
            fee_cost=opportunity.dex_fee_cost_quote,
            slippage_cost=opportunity.slippage_cost_quote,
            gas_estimate=opportunity.gas_cost_base,
            expected_net_profit=opportunity.net_profit_base,
            buy_liquidity_usd=opportunity.buy_liquidity_usd,
            sell_liquidity_usd=opportunity.sell_liquidity_usd,
        )
        # Skip intermediate 'priced' status update — the final status
        # (rejected/simulation_approved/approved/dry_run) is set below,
        # and the entire detect→price→risk path runs in a single batch
        # transaction so 'priced' is never visible to other readers.
        # Saves 1 DB round-trip (~3-4ms on Neon Postgres).
        _timings["price_ms"] = (_time.monotonic() - _t1) * 1000
        logger.info("[pipeline] %s priced: net_profit=%.6f", opp_id, float(opportunity.net_profit_base))

        # --- Stage 3: Risk ---
        # Always runs.  The core safety gate — evaluates 8 sequential rules.
        # Any failure is a hard veto.  ~99% of opportunities are rejected here.
        # Three possible outcomes:
        #   - "rejected":           fails a rule → pipeline stops
        #   - "simulation_approved": passes all rules but execution disabled → dry log
        #   - "approved":           passes all rules and execution enabled → proceed
        # The verdict + full analysis snapshot are persisted for auditability.
        # The threshold_snapshot includes all costs, liquidity scores, and
        # warning flags so an engineer can debug why a trade was rejected
        # without re-running the risk policy.
        _t2 = _time.monotonic()
        hour_trades = self.repo.count_opportunities_since(
            _one_hour_ago(), status=Status.SUBMITTED
        )
        verdict = self.risk_policy.evaluate(
            opportunity,
            current_hour_trades=hour_trades,
        )
        self.repo.save_risk_decision(
            opp_id=opp_id,
            approved=verdict.approved,
            reason_code=verdict.reason,
            threshold_snapshot=verdict.details,
        )

        _timings["risk_ms"] = (_time.monotonic() - _t2) * 1000

        if not verdict.approved:
            # Distinguish "simulation_approved" (would trade) from actual rejections.
            if verdict.reason == Status.SIMULATION_APPROVED:
                status = "simulation_approved"
                self.repo.update_opportunity_status(opp_id, status)
                _timings["total_ms"] = (_time.monotonic() - _t0) * 1000
                logger.info("[pipeline] %s SIMULATION APPROVED (would execute): spread=%.4f%% net=%.6f (timings: %s)",
                            opp_id, float(opportunity.gross_spread_pct),
                            float(opportunity.net_profit_base),
                            {k: f"{v:.1f}" for k, v in _timings.items()})
                return PipelineResult(opp_id, Status.SIMULATION_APPROVED, verdict.reason,
                                      opportunity.net_profit_base, timings=_timings)
            else:
                self.repo.update_opportunity_status(opp_id, Status.REJECTED)
                _timings["total_ms"] = (_time.monotonic() - _t0) * 1000
                logger.info("[pipeline] %s rejected: %s (timings: %s)", opp_id, verdict.reason,
                            {k: f"{v:.1f}" for k, v in _timings.items()})
                return PipelineResult(opp_id, Status.REJECTED, verdict.reason, timings=_timings)

        self.repo.update_opportunity_status(opp_id, Status.APPROVED)
        logger.info("[pipeline] %s approved", opp_id)

        # --- Stage 4: Simulation ---
        # Optional — skipped if simulator is None (simulation-only mode).
        # Calls eth_call to dry-run the full flash loan transaction against
        # the current chain state.  This is FREE (no gas spent) and catches:
        #   - Price moved since quote (most common — spread closed)
        #   - Insufficient profit (contract's minProfit check)
        #   - Bad routes (wrong router, unsupported pool)
        #   - Token approval issues
        # Without this stage, failed txs cost gas ($0.05 on L2, $5-50 on L1).
        # With ~95% simulation rejection rate, this saves significant gas.
        _t3 = _time.monotonic()
        if self.simulator is not None:
            sim_ok, sim_reason = self.simulator.simulate(opportunity)
            self.repo.save_simulation(
                opp_id=opp_id,
                success=sim_ok,
                revert_reason=sim_reason if not sim_ok else "",
                expected_net_profit=opportunity.net_profit_base,
            )

            if not sim_ok:
                self.repo.update_opportunity_status(opp_id, Status.SIMULATION_FAILED)
                logger.info("[pipeline] %s simulation failed: %s", opp_id, sim_reason)
                from alerting.dispatcher import opp_dashboard_url
                self.dispatcher.alert("simulation_failed",
                    f"Simulation failed: {opportunity.pair}\n"
                    f"Buy: {opportunity.buy_dex} → Sell: {opportunity.sell_dex}\n"
                    f"Reason: {sim_reason}\n"
                    f"Dashboard: {opp_dashboard_url(opp_id)}",
                    {"pair": opportunity.pair, "reason": sim_reason,
                     "opp_id": opp_id, "dashboard_link": opp_dashboard_url(opp_id)})
                _timings["simulate_ms"] = (_time.monotonic() - _t3) * 1000
                _timings["total_ms"] = (_time.monotonic() - _t0) * 1000
                return PipelineResult(opp_id, Status.SIMULATION_FAILED, sim_reason, timings=_timings)

            self.repo.update_opportunity_status(opp_id, Status.SIMULATED)
            logger.info("[pipeline] %s simulation passed", opp_id)

        _timings["simulate_ms"] = (_time.monotonic() - _t3) * 1000

        # --- Stage 5: Submission ---
        # Optional — skipped if submitter is None (dry-run mode exits here
        # as "dry_run" at the bottom of this method).
        # Signs the transaction and broadcasts it:
        #   - Ethereum mainnet: Flashbots bundle targeting current_block+1.
        #     Private relay prevents front-running.  If not included in
        #     target block, bundle expires harmlessly (no gas cost).
        #   - L2s (Arbitrum, Base, Optimism): public mempool.
        #     No Flashbots equivalent, but gas is cheap (~$0.05).
        # Persists: tx_hash, bundle_id, target_block, submission_type.
        _t4 = _time.monotonic()
        if self.submitter is not None:
            submission = self.submitter.submit(opportunity)
            if len(submission) == 4:
                tx_hash, bundle_id, target_block, submission_type = submission
            else:
                tx_hash, bundle_id, target_block = submission
                submission_type = "flashbots"
            exec_id = self.repo.save_execution_attempt(
                opp_id=opp_id,
                submission_type=submission_type,
                tx_hash=tx_hash,
                bundle_id=bundle_id,
                target_block=target_block,
            )
            self.repo.update_opportunity_status(opp_id, Status.SUBMITTED)
            logger.info("[pipeline] %s submitted: tx=%s block=%d", opp_id, tx_hash, target_block)

            # --- Stage 6: Verification ---
            # Optional — skipped if verifier is None (exits as "submitted").
            # Fetches the transaction receipt and determines the outcome:
            #   - included (status=1, not reverted): extract ProfitRealized
            #     event from logs, calculate gas cost, record realized PnL.
            #   - reverted (status=0): tx was mined but reverted on-chain.
            #     Records gas loss.  Feeds into circuit breaker revert counter.
            #   - not_included: no receipt found (Flashbots bundle expired
            #     or tx dropped from mempool).  No gas cost on Flashbots.
            # Also reconciles expected vs actual profit — deviations >20%
            # are flagged for cost model recalibration.
            if self.verifier is not None:
                verification = self.verifier.verify(tx_hash)
                self.repo.save_trade_result(
                    execution_id=exec_id,
                    included=verification.included,
                    reverted=verification.reverted,
                    gas_used=verification.gas_used,
                    realized_profit_quote=verification.realized_profit_quote,
                    gas_cost_base=verification.gas_cost_base,
                    profit_currency=verification.profit_currency,
                    actual_net_profit=verification.actual_profit_base,
                    block_number=verification.block_number,
                )

                _timings["verify_ms"] = (_time.monotonic() - _t4) * 1000
                _timings["total_ms"] = (_time.monotonic() - _t0) * 1000

                if verification.included and not verification.reverted:
                    self.repo.update_opportunity_status(opp_id, Status.INCLUDED)
                    logger.info("[pipeline] %s included: profit=%.6f", opp_id, float(verification.actual_profit_base))
                    self.dispatcher.trade_executed(
                        pair=opportunity.pair, tx_hash=tx_hash,
                        profit=float(verification.actual_profit_base),
                        opp_id=opp_id, chain=opportunity.chain)
                    return PipelineResult(opp_id, Status.INCLUDED, "success", verification.actual_profit_base, timings=_timings)
                elif verification.reverted:
                    self.repo.update_opportunity_status(opp_id, Status.REVERTED)
                    logger.info("[pipeline] %s reverted", opp_id)
                    self.dispatcher.trade_reverted(
                        pair=opportunity.pair, tx_hash=tx_hash,
                        reason="tx_reverted",
                        opp_id=opp_id, chain=opportunity.chain)
                    return PipelineResult(opp_id, Status.REVERTED, "tx_reverted", timings=_timings)
                else:
                    self.repo.update_opportunity_status(opp_id, Status.NOT_INCLUDED)
                    logger.info("[pipeline] %s not included", opp_id)
                    from alerting.dispatcher import opp_dashboard_url, tx_explorer_url
                    self.dispatcher.alert("trade_not_included",
                        f"Trade not included: {opportunity.pair}\n"
                        f"Bundle expired\n"
                        f"Dashboard: {opp_dashboard_url(opp_id)}",
                        {"pair": opportunity.pair, "tx_hash": tx_hash,
                         "opp_id": opp_id, "dashboard_link": opp_dashboard_url(opp_id)})
                    return PipelineResult(opp_id, Status.NOT_INCLUDED, "bundle_expired", timings=_timings)

            _timings["submit_ms"] = (_time.monotonic() - _t4) * 1000
            _timings["total_ms"] = (_time.monotonic() - _t0) * 1000
            return PipelineResult(opp_id, Status.SUBMITTED, "awaiting_verification", timings=_timings)

        # No submitter wired — the opportunity passed all risk checks and
        # simulation (if available) but there's no execution stack configured.
        # Record as "dry_run" so the dashboard shows it as "would have traded".
        # This is the normal exit path in simulation-only mode.
        self.repo.update_opportunity_status(opp_id, Status.DRY_RUN)
        _timings["total_ms"] = (_time.monotonic() - _t0) * 1000
        logger.info("[pipeline] %s dry_run (timings: %s)", opp_id,
                    {k: f"{v:.1f}" for k, v in _timings.items()})
        return PipelineResult(opp_id, Status.DRY_RUN, "approved_not_submitted",
                              opportunity.net_profit_base, timings=_timings)


def _one_hour_ago() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
