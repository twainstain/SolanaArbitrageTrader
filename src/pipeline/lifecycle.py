"""Candidate lifecycle pipeline.

Orchestrates the full arbitrage candidate flow per the architecture doc:
  detected → priced → risk_approved/rejected → simulated → submitted → outcome

Each stage is persisted to the database so the entire decision is
auditable and replayable after the fact.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from alerting.dispatcher import AlertDispatcher
from models import ZERO, MarketQuote, Opportunity
from persistence.repository import Repository
from risk.policy import RiskPolicy, RiskVerdict

D = Decimal
logger = logging.getLogger(__name__)


class Simulator(Protocol):
    """Protocol for transaction simulation."""
    def simulate(self, opportunity: Opportunity) -> tuple[bool, str]: ...


class Submitter(Protocol):
    """Protocol for transaction submission."""
    def submit(self, opportunity: Opportunity) -> tuple[str, str, int]:
        """Returns (tx_hash, bundle_id, target_block)."""
        ...


class ResultVerifier(Protocol):
    """Protocol for verifying on-chain results."""
    def verify(self, tx_hash: str) -> tuple[bool, bool, int, Decimal]:
        """Returns (included, reverted, gas_used, actual_profit)."""
        ...


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
        _t1 = _time.monotonic()
        self.repo.save_pricing(
            opp_id=opp_id,
            input_amount=opportunity.cost_to_buy_quote,
            estimated_output=opportunity.proceeds_from_sell_quote,
            fee_cost=opportunity.dex_fee_cost_quote,
            slippage_cost=opportunity.slippage_cost_quote,
            gas_estimate=opportunity.gas_cost_base,
            expected_net_profit=opportunity.net_profit_base,
        )
        self.repo.update_opportunity_status(opp_id, "priced")
        _timings["price_ms"] = (_time.monotonic() - _t1) * 1000
        logger.info("[pipeline] %s priced: net_profit=%.6f", opp_id, float(opportunity.net_profit_base))

        # --- Stage 3: Risk ---
        _t2 = _time.monotonic()
        hour_trades = self.repo.count_opportunities_since(
            _one_hour_ago(), status="submitted"
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
            if verdict.reason == "simulation_approved":
                status = "simulation_approved"
                self.repo.update_opportunity_status(opp_id, status)
                _timings["total_ms"] = (_time.monotonic() - _t0) * 1000
                logger.info("[pipeline] %s SIMULATION APPROVED (would execute): spread=%.4f%% net=%.6f (timings: %s)",
                            opp_id, float(opportunity.gross_spread_pct),
                            float(opportunity.net_profit_base),
                            {k: f"{v:.1f}" for k, v in _timings.items()})
                return PipelineResult(opp_id, "simulation_approved", verdict.reason,
                                      opportunity.net_profit_base, timings=_timings)
            else:
                self.repo.update_opportunity_status(opp_id, "rejected")
                _timings["total_ms"] = (_time.monotonic() - _t0) * 1000
                logger.info("[pipeline] %s rejected: %s (timings: %s)", opp_id, verdict.reason,
                            {k: f"{v:.1f}" for k, v in _timings.items()})
                return PipelineResult(opp_id, "rejected", verdict.reason, timings=_timings)

        self.repo.update_opportunity_status(opp_id, "approved")
        logger.info("[pipeline] %s approved", opp_id)

        # --- Stage 4: Simulation ---
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
                self.repo.update_opportunity_status(opp_id, "simulation_failed")
                logger.info("[pipeline] %s simulation failed: %s", opp_id, sim_reason)
                self.dispatcher.alert("simulation_failed",
                    f"Simulation failed: {opportunity.pair}\n"
                    f"Buy: {opportunity.buy_dex} → Sell: {opportunity.sell_dex}\n"
                    f"Reason: {sim_reason}",
                    {"pair": opportunity.pair, "reason": sim_reason})
                _timings["simulate_ms"] = (_time.monotonic() - _t3) * 1000
                _timings["total_ms"] = (_time.monotonic() - _t0) * 1000
                return PipelineResult(opp_id, "simulation_failed", sim_reason, timings=_timings)

            self.repo.update_opportunity_status(opp_id, "simulated")
            logger.info("[pipeline] %s simulation passed", opp_id)

        _timings["simulate_ms"] = (_time.monotonic() - _t3) * 1000

        # --- Stage 5: Submission ---
        _t4 = _time.monotonic()
        if self.submitter is not None:
            tx_hash, bundle_id, target_block = self.submitter.submit(opportunity)
            exec_id = self.repo.save_execution_attempt(
                opp_id=opp_id,
                submission_type="flashbots",
                tx_hash=tx_hash,
                bundle_id=bundle_id,
                target_block=target_block,
            )
            self.repo.update_opportunity_status(opp_id, "submitted")
            logger.info("[pipeline] %s submitted: tx=%s block=%d", opp_id, tx_hash, target_block)

            # --- Stage 6: Verification ---
            if self.verifier is not None:
                included, reverted, gas_used, actual_profit = self.verifier.verify(tx_hash)
                self.repo.save_trade_result(
                    execution_id=exec_id,
                    included=included,
                    reverted=reverted,
                    gas_used=gas_used,
                    actual_net_profit=actual_profit,
                )

                _timings["verify_ms"] = (_time.monotonic() - _t4) * 1000
                _timings["total_ms"] = (_time.monotonic() - _t0) * 1000

                if included and not reverted:
                    self.repo.update_opportunity_status(opp_id, "included")
                    logger.info("[pipeline] %s included: profit=%.6f", opp_id, float(actual_profit))
                    self.dispatcher.trade_executed(
                        pair=opportunity.pair, tx_hash=tx_hash,
                        profit=float(actual_profit))
                    return PipelineResult(opp_id, "included", "success", actual_profit, timings=_timings)
                elif reverted:
                    self.repo.update_opportunity_status(opp_id, "reverted")
                    logger.info("[pipeline] %s reverted", opp_id)
                    self.dispatcher.trade_reverted(
                        pair=opportunity.pair, tx_hash=tx_hash,
                        reason="tx_reverted")
                    return PipelineResult(opp_id, "reverted", "tx_reverted", timings=_timings)
                else:
                    self.repo.update_opportunity_status(opp_id, "not_included")
                    logger.info("[pipeline] %s not included", opp_id)
                    self.dispatcher.alert("trade_not_included",
                        f"Trade not included: {opportunity.pair}\nBundle expired",
                        {"pair": opportunity.pair, "tx_hash": tx_hash})
                    return PipelineResult(opp_id, "not_included", "bundle_expired", timings=_timings)

            _timings["submit_ms"] = (_time.monotonic() - _t4) * 1000
            _timings["total_ms"] = (_time.monotonic() - _t0) * 1000
            return PipelineResult(opp_id, "submitted", "awaiting_verification", timings=_timings)

        # No submitter — dry run
        self.repo.update_opportunity_status(opp_id, "dry_run")
        _timings["total_ms"] = (_time.monotonic() - _t0) * 1000
        logger.info("[pipeline] %s dry_run (timings: %s)", opp_id,
                    {k: f"{v:.1f}" for k, v in _timings.items()})
        return PipelineResult(opp_id, "dry_run", "approved_not_submitted",
                              opportunity.net_profit_base, timings=_timings)


def _one_hour_ago() -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
