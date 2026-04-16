"""Candidate lifecycle pipeline — ArbitrageTrader implementation of BasePipeline.

Orchestrates the full arbitrage candidate flow:
  detected → priced → risk_approved/rejected → simulated → submitted → outcome

Subclasses trading_platform's BasePipeline to establish the stage protocol
while handling AT-specific concerns:
  - DB batching across detect→price→risk (single transaction)
  - simulation_approved vs rejected status distinction
  - AT's Submitter returning tuples (not SubmissionRef)
  - Alert dispatching at simulation/execution stages

Other bots (SolanaTrader, PolymarketTrader) can use BasePipeline.process()
directly since they won't have these legacy concerns.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol

from alerting.dispatcher import AlertDispatcher
from core.models import ZERO, Opportunity, OpportunityStatus as Status
from persistence.repository import Repository
from pipeline.verifier import VerificationResult
from risk.policy import RiskPolicy, RiskVerdict
from trading_platform.pipeline.base_pipeline import (
    BasePipeline,
    PipelineResult as _PlatformResult,
)

D = Decimal
logger = logging.getLogger(__name__)


# Keep AT's Protocol definitions for backward compatibility.
class Simulator(Protocol):
    def simulate(self, opportunity: Opportunity) -> tuple[bool, str]: ...

class Submitter(Protocol):
    def submit(self, opportunity: Opportunity) -> tuple[str, str, int] | tuple[str, str, int, str]: ...

class ResultVerifier(Protocol):
    def verify(self, tx_hash: str) -> VerificationResult: ...


@dataclass
class PipelineResult:
    """Outcome of processing one candidate through the pipeline."""
    opportunity_id: str
    final_status: str
    reason: str
    net_profit: Decimal = ZERO
    timings: dict | None = None


class CandidatePipeline(BasePipeline):
    """ArbitrageTrader pipeline — subclasses BasePipeline with AT-specific stages.

    Stage implementations (detect, price, evaluate_risk) follow the BasePipeline
    protocol. process() is overridden for DB batching and simulation_approved handling.
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
        super().__init__(simulator=simulator, submitter=submitter, verifier=verifier)
        self.repo = repo
        self.risk_policy = risk_policy
        self.dispatcher = dispatcher or AlertDispatcher()

    # ------------------------------------------------------------------
    # Stage implementations (BasePipeline abstract methods)
    # ------------------------------------------------------------------

    def detect(self, candidate: Any) -> str:
        """Stage 1: Create opportunity record in DB."""
        opp: Opportunity = candidate
        return self.repo.create_opportunity(
            pair=opp.pair, chain=opp.chain,
            buy_dex=opp.buy_dex, sell_dex=opp.sell_dex,
            spread_bps=opp.gross_spread_pct,
        )

    def price(self, candidate_id: str, candidate: Any) -> None:
        """Stage 2: Persist full cost breakdown."""
        opp: Opportunity = candidate
        self.repo.save_pricing(
            opp_id=candidate_id,
            input_amount=opp.cost_to_buy_quote,
            estimated_output=opp.proceeds_from_sell_quote,
            fee_cost=opp.dex_fee_cost_quote,
            slippage_cost=opp.slippage_cost_quote,
            gas_estimate=opp.gas_cost_base,
            expected_net_profit=opp.net_profit_base,
            buy_liquidity_usd=opp.buy_liquidity_usd,
            sell_liquidity_usd=opp.sell_liquidity_usd,
        )

    def evaluate_risk(self, candidate: Any) -> RiskVerdict:
        """Stage 3: Run risk policy rules."""
        opp: Opportunity = candidate
        hour_trades = self.repo.count_opportunities_since(
            _one_hour_ago(), status=Status.SUBMITTED,
        )
        return self.risk_policy.evaluate(opp, current_hour_trades=hour_trades)

    # ------------------------------------------------------------------
    # Callbacks (BasePipeline hooks)
    # ------------------------------------------------------------------

    def on_rejected(self, candidate_id: str, reason: str, candidate: Any) -> None:
        if reason == Status.SIMULATION_APPROVED:
            self.repo.update_opportunity_status(candidate_id, "simulation_approved")
        else:
            self.repo.update_opportunity_status(candidate_id, Status.REJECTED)

    def on_approved(self, candidate_id: str, candidate: Any) -> None:
        self.repo.update_opportunity_status(candidate_id, Status.APPROVED)

    # ------------------------------------------------------------------
    # Orchestration — overrides BasePipeline.process() for AT specifics:
    #   - DB batching (stages 1-3 in single transaction)
    #   - simulation_approved vs rejected distinction
    #   - AT Submitter tuple return format
    #   - Alert dispatching
    # ------------------------------------------------------------------

    def process(self, opportunity: Opportunity) -> PipelineResult:
        """Run a single opportunity through the full pipeline."""
        _t0 = time.monotonic()
        _timings: dict[str, float] = {}

        with self.repo.conn.batch():
            return self._process_inner(opportunity, _t0, _timings)

    def _process_inner(self, opportunity: Opportunity, _t0: float, _timings: dict[str, float]) -> PipelineResult:
        # --- Stage 1: Detect ---
        opp_id = self.detect(opportunity)
        _timings["detect_ms"] = (time.monotonic() - _t0) * 1000
        logger.info("[pipeline] %s detected: %s buy=%s sell=%s",
                     opp_id, opportunity.pair, opportunity.buy_dex, opportunity.sell_dex)

        # --- Stage 2: Price ---
        _t1 = time.monotonic()
        self.price(opp_id, opportunity)
        _timings["price_ms"] = (time.monotonic() - _t1) * 1000
        logger.info("[pipeline] %s priced: net_profit=%.6f", opp_id, float(opportunity.net_profit_base))

        # --- Stage 3: Risk ---
        _t2 = time.monotonic()
        verdict = self.evaluate_risk(opportunity)
        self.repo.save_risk_decision(
            opp_id=opp_id, approved=verdict.approved,
            reason_code=verdict.reason,
            threshold_snapshot=verdict.details,
        )
        _timings["risk_ms"] = (time.monotonic() - _t2) * 1000

        if not verdict.approved:
            self.on_rejected(opp_id, verdict.reason, opportunity)
            _timings["total_ms"] = (time.monotonic() - _t0) * 1000

            if verdict.reason == Status.SIMULATION_APPROVED:
                logger.info("[pipeline] %s SIMULATION APPROVED: spread=%.4f%% net=%.6f (timings: %s)",
                            opp_id, float(opportunity.gross_spread_pct),
                            float(opportunity.net_profit_base),
                            {k: f"{v:.1f}" for k, v in _timings.items()})
                return PipelineResult(opp_id, Status.SIMULATION_APPROVED, verdict.reason,
                                      opportunity.net_profit_base, timings=_timings)
            else:
                logger.info("[pipeline] %s rejected: %s (timings: %s)", opp_id, verdict.reason,
                            {k: f"{v:.1f}" for k, v in _timings.items()})
                return PipelineResult(opp_id, Status.REJECTED, verdict.reason, timings=_timings)

        self.on_approved(opp_id, opportunity)
        logger.info("[pipeline] %s approved", opp_id)

        # --- Stage 4: Simulation ---
        _t3 = time.monotonic()
        if self.simulator is not None:
            sim_ok, sim_reason = self.simulator.simulate(opportunity)
            self.repo.save_simulation(
                opp_id=opp_id, success=sim_ok,
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
                _timings["simulate_ms"] = (time.monotonic() - _t3) * 1000
                _timings["total_ms"] = (time.monotonic() - _t0) * 1000
                return PipelineResult(opp_id, Status.SIMULATION_FAILED, sim_reason, timings=_timings)

            self.repo.update_opportunity_status(opp_id, Status.SIMULATED)
            logger.info("[pipeline] %s simulation passed", opp_id)

        _timings["simulate_ms"] = (time.monotonic() - _t3) * 1000

        # --- Stage 5: Submission ---
        _t4 = time.monotonic()
        if self.submitter is not None:
            submission = self.submitter.submit(opportunity)
            if len(submission) == 4:
                tx_hash, bundle_id, target_block, submission_type = submission
            else:
                tx_hash, bundle_id, target_block = submission
                submission_type = "flashbots"
            exec_id = self.repo.save_execution_attempt(
                opp_id=opp_id, submission_type=submission_type,
                tx_hash=tx_hash, bundle_id=bundle_id, target_block=target_block,
            )
            self.repo.update_opportunity_status(opp_id, Status.SUBMITTED)
            logger.info("[pipeline] %s submitted: tx=%s block=%d", opp_id, tx_hash, target_block)

            # --- Stage 6: Verification ---
            if self.verifier is not None:
                verification = self.verifier.verify(tx_hash)
                self.repo.save_trade_result(
                    execution_id=exec_id, included=verification.included,
                    reverted=verification.reverted, gas_used=verification.gas_used,
                    realized_profit_quote=verification.realized_profit_quote,
                    gas_cost_base=verification.gas_cost_base,
                    profit_currency=verification.profit_currency,
                    actual_net_profit=verification.actual_profit_base,
                    block_number=verification.block_number,
                )
                _timings["verify_ms"] = (time.monotonic() - _t4) * 1000
                _timings["total_ms"] = (time.monotonic() - _t0) * 1000

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
                        pair=opportunity.pair, tx_hash=tx_hash, reason="tx_reverted",
                        opp_id=opp_id, chain=opportunity.chain)
                    return PipelineResult(opp_id, Status.REVERTED, "tx_reverted", timings=_timings)
                else:
                    self.repo.update_opportunity_status(opp_id, Status.NOT_INCLUDED)
                    logger.info("[pipeline] %s not included", opp_id)
                    from alerting.dispatcher import opp_dashboard_url
                    self.dispatcher.alert("trade_not_included",
                        f"Trade not included: {opportunity.pair}\nBundle expired\n"
                        f"Dashboard: {opp_dashboard_url(opp_id)}",
                        {"pair": opportunity.pair, "tx_hash": tx_hash,
                         "opp_id": opp_id, "dashboard_link": opp_dashboard_url(opp_id)})
                    return PipelineResult(opp_id, Status.NOT_INCLUDED, "bundle_expired", timings=_timings)

            _timings["submit_ms"] = (time.monotonic() - _t4) * 1000
            _timings["total_ms"] = (time.monotonic() - _t0) * 1000
            return PipelineResult(opp_id, Status.SUBMITTED, "awaiting_verification", timings=_timings)

        # No submitter — dry run.
        self.repo.update_opportunity_status(opp_id, Status.DRY_RUN)
        _timings["total_ms"] = (time.monotonic() - _t0) * 1000
        logger.info("[pipeline] %s dry_run (timings: %s)", opp_id,
                    {k: f"{v:.1f}" for k, v in _timings.items()})
        return PipelineResult(opp_id, Status.DRY_RUN, "approved_not_submitted",
                              opportunity.net_profit_base, timings=_timings)


def _one_hour_ago() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
