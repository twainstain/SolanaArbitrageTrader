"""Base pipeline — generic 6-stage candidate lifecycle.

Products subclass this and override detect/price/evaluate_risk (required)
and simulate/submit/verify (optional) to plug in product-specific logic.

Stages 1-3 run inside a batched DB transaction for atomicity.
Stages 4-6 involve external calls and persist independently.

Each stage is timed. The pipeline stops on any failure.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

logger = logging.getLogger(__name__)

ZERO = Decimal("0")


@dataclass
class PipelineResult:
    """Outcome of processing one candidate through the pipeline."""
    candidate_id: str
    final_status: str
    reason: str
    net_profit: Decimal = ZERO
    timings: dict = field(default_factory=dict)


class Simulator(Protocol):
    """Protocol for stage 4 — simulation (free dry-run)."""
    def simulate(self, candidate: Any) -> tuple[bool, str]: ...


class Submitter(Protocol):
    """Protocol for stage 5 — transaction/order submission."""
    def submit(self, candidate: Any) -> dict: ...


class Verifier(Protocol):
    """Protocol for stage 6 — outcome verification."""
    def verify(self, submission_ref: str) -> dict: ...


class BasePipeline:
    """Generic 6-stage pipeline. Products override stage methods.

    The pipeline enforces:
      - Sequential execution (stage N depends on stage N-1)
      - Persistence at every stage (full audit trail)
      - Fail-fast on any rejection or error
      - Timing instrumentation on every stage

    Products must implement:
      - detect(candidate) -> str (candidate_id)
      - price(candidate_id, candidate) -> None
      - evaluate_risk(candidate) -> RiskVerdict

    Products may wire:
      - simulator: Simulator protocol (stage 4)
      - submitter: Submitter protocol (stage 5)
      - verifier: Verifier protocol (stage 6)
    """

    def __init__(
        self,
        simulator: Simulator | None = None,
        submitter: Submitter | None = None,
        verifier: Verifier | None = None,
    ) -> None:
        self.simulator = simulator
        self.submitter = submitter
        self.verifier = verifier

    # -- Override these in your product --

    def detect(self, candidate: Any) -> str:
        """Stage 1: Persist the candidate and return its ID."""
        raise NotImplementedError

    def price(self, candidate_id: str, candidate: Any) -> None:
        """Stage 2: Persist the cost/pricing breakdown."""
        raise NotImplementedError

    def evaluate_risk(self, candidate: Any) -> Any:
        """Stage 3: Evaluate risk rules. Return a verdict with .approved and .reason."""
        raise NotImplementedError

    def on_approved(self, candidate_id: str, candidate: Any) -> None:
        """Called when risk approves. Override to persist status."""
        pass

    def on_rejected(self, candidate_id: str, reason: str, candidate: Any) -> None:
        """Called when risk rejects. Override to persist status."""
        pass

    def on_simulated(self, candidate_id: str, success: bool, reason: str) -> None:
        """Called after simulation. Override to persist result."""
        pass

    def on_submitted(self, candidate_id: str, submission: dict) -> None:
        """Called after submission. Override to persist tx/order details."""
        pass

    def on_verified(self, candidate_id: str, result: dict) -> None:
        """Called after verification. Override to persist outcome."""
        pass

    # -- Pipeline execution --

    def process(self, candidate: Any) -> PipelineResult:
        """Run a candidate through all stages."""
        t0 = time.monotonic()
        timings: dict[str, float] = {}

        # Stage 1: Detect
        candidate_id = self.detect(candidate)
        timings["detect_ms"] = (time.monotonic() - t0) * 1000

        # Stage 2: Price
        t1 = time.monotonic()
        self.price(candidate_id, candidate)
        timings["price_ms"] = (time.monotonic() - t1) * 1000

        # Stage 3: Risk
        t2 = time.monotonic()
        verdict = self.evaluate_risk(candidate)
        timings["risk_ms"] = (time.monotonic() - t2) * 1000

        if not verdict.approved:
            self.on_rejected(candidate_id, verdict.reason, candidate)
            timings["total_ms"] = (time.monotonic() - t0) * 1000
            return PipelineResult(candidate_id, verdict.reason, verdict.reason, timings=timings)

        self.on_approved(candidate_id, candidate)

        # Stage 4: Simulate (optional)
        t3 = time.monotonic()
        if self.simulator is not None:
            sim_ok, sim_reason = self.simulator.simulate(candidate)
            self.on_simulated(candidate_id, sim_ok, sim_reason)
            timings["simulate_ms"] = (time.monotonic() - t3) * 1000
            if not sim_ok:
                timings["total_ms"] = (time.monotonic() - t0) * 1000
                return PipelineResult(candidate_id, "simulation_failed", sim_reason, timings=timings)
        else:
            timings["simulate_ms"] = 0

        # Stage 5: Submit (optional)
        t4 = time.monotonic()
        if self.submitter is not None:
            submission = self.submitter.submit(candidate)
            self.on_submitted(candidate_id, submission)
            timings["submit_ms"] = (time.monotonic() - t4) * 1000

            # Stage 6: Verify (optional)
            if self.verifier is not None:
                ref = submission.get("tx_hash") or submission.get("order_id") or ""
                result = self.verifier.verify(ref)
                self.on_verified(candidate_id, result)
                timings["verify_ms"] = (time.monotonic() - t4) * 1000 - timings["submit_ms"]
                timings["total_ms"] = (time.monotonic() - t0) * 1000
                status = result.get("status", "verified")
                profit = Decimal(str(result.get("net_profit", 0)))
                return PipelineResult(candidate_id, status, "verified", profit, timings)

            timings["total_ms"] = (time.monotonic() - t0) * 1000
            return PipelineResult(candidate_id, "submitted", "awaiting_verification", timings=timings)

        # No submitter — dry run
        timings["total_ms"] = (time.monotonic() - t0) * 1000
        return PipelineResult(candidate_id, "dry_run", "approved_not_submitted", timings=timings)
