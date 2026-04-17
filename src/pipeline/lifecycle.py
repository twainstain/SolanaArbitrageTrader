"""Candidate lifecycle pipeline — SolanaTrader implementation of BasePipeline.

Orchestrates the candidate flow:
  detected → priced → risk_approved/rejected → simulated → submitted → confirmed/reverted/dropped

Solana-native shape vs the legacy EVM pipeline:
  - submission returns a SubmissionRef with signature/kind instead of
    (tx_hash, bundle_id, target_block)
  - verification uses VerificationResult (slot, fee_paid_lamports, actual_profit_base)
  - no cross-chain / per-chain logic
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
from trading_platform.pipeline.base_pipeline import BasePipeline

D = Decimal
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubmissionRef:
    """Solana submission reference.

    ``signature`` is the base58 tx signature returned by ``sendTransaction``.
    ``kind`` distinguishes regular RPC from Jito bundles so the verifier
    knows which mechanism to poll.
    """
    signature: str
    kind: str = "rpc"           # "rpc" | "jito-bundle" | "paper"
    metadata: dict | None = None


class Simulator(Protocol):
    def simulate(self, opportunity: Opportunity) -> tuple[bool, str]: ...


class Submitter(Protocol):
    def submit(self, opportunity: Opportunity) -> SubmissionRef: ...


class ResultVerifier(Protocol):
    def verify(self, signature: str) -> VerificationResult: ...


@dataclass
class PipelineResult:
    """Outcome of processing one candidate through the pipeline."""
    opportunity_id: str
    final_status: str
    reason: str
    net_profit: Decimal = ZERO
    timings: dict | None = None


class CandidatePipeline(BasePipeline):
    """SolanaTrader pipeline — subclasses BasePipeline with Solana-specific stages."""

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
    # Alerting (Phase 4)
    # ------------------------------------------------------------------

    def _safe_alert(
        self,
        event_type: str,
        pair: str,
        signature: str,
        reason: str,
        opp_id: str = "",
    ) -> None:
        """Fan out a trade_reverted / trade_dropped alert without raising.

        Pipeline correctness must not depend on the alerting path — a
        Discord/Gmail outage should log and move on, not crash the scanner.
        The dispatcher already swallows per-backend errors, but we wrap
        anyway so a malformed message or missing backend can't bring down
        the verifier loop.
        """
        try:
            if event_type == "trade_reverted":
                self.dispatcher.trade_reverted(
                    pair=pair, tx_hash=signature, reason=reason,
                    opp_id=opp_id, chain="solana",
                )
            elif event_type == "trade_dropped":
                # dispatcher has no trade_dropped helper yet; use generic
                # alert() with a consistent event_type for downstream filters.
                from alerting.dispatcher import opp_dashboard_url, tx_explorer_url
                details: dict = {"pair": pair, "signature": signature, "reason": reason}
                msg_lines = [
                    f"Trade DROPPED: {pair}",
                    f"TX (attempted): {signature}" if signature else "TX: —",
                    f"Reason: {reason}",
                ]
                if signature:
                    details["tx_link"] = tx_explorer_url("solana", signature)
                    msg_lines.append(f"Explorer: {details['tx_link']}")
                if opp_id:
                    link = opp_dashboard_url(opp_id)
                    details["opp_id"] = opp_id
                    details["dashboard_link"] = link
                    msg_lines.append(f"Dashboard: {link}")
                self.dispatcher.alert("trade_dropped", "\n".join(msg_lines), details)
            else:
                self.dispatcher.alert(event_type, reason, {"pair": pair, "signature": signature})
        except Exception as exc:
            logger.warning("[pipeline] alert fan-out failed for %s: %s", event_type, exc)

    # ------------------------------------------------------------------
    # Stage implementations
    # ------------------------------------------------------------------

    def detect(self, candidate: Any) -> str:
        opp: Opportunity = candidate
        return self.repo.create_opportunity(
            pair=opp.pair,
            buy_venue=opp.buy_venue, sell_venue=opp.sell_venue,
            spread_bps=opp.gross_spread_pct,
        )

    def price(self, candidate_id: str, candidate: Any) -> None:
        opp: Opportunity = candidate
        self.repo.save_pricing(
            opp_id=candidate_id,
            input_amount=opp.cost_to_buy_quote,
            estimated_output=opp.proceeds_from_sell_quote,
            fee_cost=opp.venue_fee_cost_quote,
            slippage_cost=opp.slippage_cost_quote,
            fee_estimate_base=opp.fee_cost_base,
            expected_net_profit=opp.net_profit_base,
            buy_liquidity_usd=opp.buy_liquidity_usd,
            sell_liquidity_usd=opp.sell_liquidity_usd,
        )

    def evaluate_risk(self, candidate: Any) -> RiskVerdict:
        opp: Opportunity = candidate
        hour_trades = self.repo.count_opportunities_since(
            _one_hour_ago(), status=Status.SUBMITTED,
        )
        return self.risk_policy.evaluate(opp, current_hour_trades=hour_trades)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def on_rejected(self, candidate_id: str, reason: str, candidate: Any) -> None:
        if reason == Status.SIMULATION_APPROVED:
            self.repo.update_opportunity_status(candidate_id, "simulation_approved")
        else:
            self.repo.update_opportunity_status(candidate_id, Status.REJECTED)

    def on_approved(self, candidate_id: str, candidate: Any) -> None:
        self.repo.update_opportunity_status(candidate_id, Status.APPROVED)

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def process(self, opportunity: Opportunity) -> PipelineResult:
        t0 = time.monotonic()
        timings: dict[str, float] = {}
        with self.repo.conn.batch():
            return self._process_inner(opportunity, t0, timings)

    def _process_inner(
        self, opportunity: Opportunity, t0: float, timings: dict[str, float],
    ) -> PipelineResult:
        # Stage 1: Detect
        opp_id = self.detect(opportunity)
        timings["detect_ms"] = (time.monotonic() - t0) * 1000
        logger.info(
            "[pipeline] %s detected: %s buy=%s sell=%s",
            opp_id, opportunity.pair, opportunity.buy_venue, opportunity.sell_venue,
        )

        # Stage 2: Price
        t1 = time.monotonic()
        self.price(opp_id, opportunity)
        timings["price_ms"] = (time.monotonic() - t1) * 1000

        # Stage 3: Risk
        t2 = time.monotonic()
        verdict = self.evaluate_risk(opportunity)
        self.repo.save_risk_decision(
            opp_id=opp_id, approved=verdict.approved,
            reason_code=verdict.reason,
            threshold_snapshot=verdict.details,
        )
        timings["risk_ms"] = (time.monotonic() - t2) * 1000

        if not verdict.approved:
            self.on_rejected(opp_id, verdict.reason, opportunity)
            timings["total_ms"] = (time.monotonic() - t0) * 1000
            if verdict.reason == Status.SIMULATION_APPROVED:
                return PipelineResult(
                    opp_id, Status.SIMULATION_APPROVED, verdict.reason,
                    opportunity.net_profit_base, timings=timings,
                )
            return PipelineResult(opp_id, Status.REJECTED, verdict.reason, timings=timings)

        self.on_approved(opp_id, opportunity)
        logger.info("[pipeline] %s approved", opp_id)

        # Stage 4: Simulation (optional)
        t3 = time.monotonic()
        if self.simulator is not None:
            sim_ok, sim_reason = self.simulator.simulate(opportunity)
            self.repo.save_simulation(
                opp_id=opp_id, success=sim_ok,
                revert_reason=sim_reason if not sim_ok else "",
                expected_net_profit=opportunity.net_profit_base,
            )
            if not sim_ok:
                self.repo.update_opportunity_status(opp_id, Status.SIMULATION_FAILED)
                timings["simulate_ms"] = (time.monotonic() - t3) * 1000
                timings["total_ms"] = (time.monotonic() - t0) * 1000
                logger.info("[pipeline] %s simulation failed: %s", opp_id, sim_reason)
                return PipelineResult(opp_id, Status.SIMULATION_FAILED, sim_reason, timings=timings)
            self.repo.update_opportunity_status(opp_id, Status.SIMULATED)
            logger.info("[pipeline] %s simulation passed", opp_id)
        timings["simulate_ms"] = (time.monotonic() - t3) * 1000

        # Stage 5: Submission (Phase 3+).  In Phase 1 submitter is always None.
        t4 = time.monotonic()
        if self.submitter is not None:
            ref = self.submitter.submit(opportunity)
            exec_id = self.repo.save_execution_attempt(
                opp_id=opp_id, submission_kind=ref.kind,
                signature=ref.signature, metadata=ref.metadata or {},
            )
            self.repo.update_opportunity_status(opp_id, Status.SUBMITTED)
            logger.info("[pipeline] %s submitted: sig=%s kind=%s", opp_id, ref.signature, ref.kind)

            # Stage 6: Verification
            if self.verifier is not None:
                verification = self.verifier.verify(ref.signature)
                self.repo.save_trade_result(
                    execution_id=exec_id,
                    included=verification.included,
                    reverted=verification.reverted,
                    dropped=verification.dropped,
                    fee_paid_lamports=verification.fee_paid_lamports,
                    realized_profit_quote=verification.realized_profit_quote,
                    fee_paid_base=verification.fee_paid_base,
                    actual_net_profit=verification.actual_profit_base,
                    confirmation_slot=verification.confirmation_slot,
                    profit_currency=verification.profit_currency,
                )
                timings["verify_ms"] = (time.monotonic() - t4) * 1000
                timings["total_ms"] = (time.monotonic() - t0) * 1000

                if verification.included and not verification.reverted:
                    self.repo.update_opportunity_status(opp_id, Status.CONFIRMED)
                    logger.info(
                        "[pipeline] %s confirmed: profit=%.8f slot=%d",
                        opp_id, float(verification.actual_profit_base),
                        verification.confirmation_slot,
                    )
                    return PipelineResult(
                        opp_id, Status.CONFIRMED, "success",
                        verification.actual_profit_base, timings=timings,
                    )
                if verification.reverted:
                    self.repo.update_opportunity_status(opp_id, Status.REVERTED)
                    # Phase 4: alert on reverted trades. Fire-and-forget — a
                    # dispatcher backend that can't send shouldn't block the
                    # pipeline. _safe_alert logs but never raises.
                    self._safe_alert(
                        "trade_reverted",
                        opportunity.pair, verification.signature, "on-chain revert",
                        opp_id=opp_id,
                    )
                    return PipelineResult(opp_id, Status.REVERTED, "tx_reverted", timings=timings)
                if verification.dropped:
                    self.repo.update_opportunity_status(opp_id, Status.DROPPED)
                    self._safe_alert(
                        "trade_dropped",
                        opportunity.pair, verification.signature or "", "tx dropped (blockhash expired)",
                        opp_id=opp_id,
                    )
                    return PipelineResult(opp_id, Status.DROPPED, "tx_dropped", timings=timings)
            timings["submit_ms"] = (time.monotonic() - t4) * 1000
            timings["total_ms"] = (time.monotonic() - t0) * 1000
            return PipelineResult(opp_id, Status.SUBMITTED, "awaiting_verification", timings=timings)

        # No submitter — scanner-only (Phase 1) dry run.
        self.repo.update_opportunity_status(opp_id, Status.DRY_RUN)
        timings["total_ms"] = (time.monotonic() - t0) * 1000
        logger.info(
            "[pipeline] %s dry_run (timings: %s)",
            opp_id, {k: f"{v:.1f}" for k, v in timings.items()},
        )
        return PipelineResult(
            opp_id, Status.DRY_RUN, "approved_not_submitted",
            opportunity.net_profit_base, timings=timings,
        )


def _one_hour_ago() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
