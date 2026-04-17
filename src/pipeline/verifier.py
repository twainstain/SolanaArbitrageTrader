"""Solana verification contracts and helpers.

Phase 1 is scanner-only so no real submitter or verifier is wired.  These
types exist to:

1. Match the ``ResultVerifier`` protocol used by ``CandidatePipeline`` so
   the abstract path still type-checks.
2. Provide a ``PnLReconciler`` that compares expected vs realised profit
   once Phase 3 fills in actual execution data.
3. Define ``VerificationResult`` with Solana-native fields
   (``signature``, ``confirmation_slot``, ``fee_paid_lamports``) instead of
   EVM's ``tx_hash`` / ``block_number`` / ``gas_used``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

D = Decimal
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VerificationResult:
    """Solana verification outcome.

    ``included``/``reverted``/``dropped`` encode the three post-submit
    end-states: landed + ok, landed + errored, or never landed (blockhash
    expired, etc.).  ``signature`` and ``confirmation_slot`` replace the
    legacy EVM tx_hash/block_number.  ``fee_paid_lamports`` replaces
    ``gas_used``.
    """
    included: bool = False
    reverted: bool = False
    dropped: bool = False
    signature: str = ""
    confirmation_slot: int = 0
    fee_paid_lamports: int = 0
    realized_profit_quote: Decimal = D("0")
    fee_paid_base: Decimal = D("0")       # lamports / 10^9 for convenience
    actual_profit_base: Decimal = D("0")
    profit_currency: str = ""


class PaperVerifier:
    """No-op verifier for scanner-phase (Phase 1).

    Accepts any signature and returns an empty VerificationResult.  Pipeline
    callers that set ``submitter=None`` skip verification entirely; this
    class only exists so tests can exercise the happy path.
    """

    def verify(self, signature: str) -> VerificationResult:
        logger.debug("[verifier] paper verify %s", signature)
        return VerificationResult(included=False, signature=signature)


class PnLReconciler:
    """Compare actual on-chain profit with expected off-chain estimates."""

    def __init__(self, deviation_threshold_pct: float = 20.0) -> None:
        self.deviation_threshold_pct = deviation_threshold_pct
        self._reconciliations: list[dict] = []

    def reconcile(
        self,
        opp_id: str,
        expected_profit: Decimal,
        actual_profit: Decimal,
        fee_paid_lamports: int,
        estimated_fee_lamports: int,
    ) -> dict:
        deviation = D("0")
        deviation_pct = 0.0
        if expected_profit > D("0"):
            deviation = actual_profit - expected_profit
            deviation_pct = float(deviation / expected_profit * D("100"))

        fee_deviation = 0.0
        if estimated_fee_lamports > 0:
            fee_deviation = float(
                (fee_paid_lamports - estimated_fee_lamports) / estimated_fee_lamports * 100,
            )

        significant = abs(deviation_pct) > self.deviation_threshold_pct
        report = {
            "opp_id": opp_id,
            "expected_profit": str(expected_profit),
            "actual_profit": str(actual_profit),
            "deviation": str(deviation),
            "deviation_pct": round(deviation_pct, 2),
            "fee_paid_lamports": fee_paid_lamports,
            "estimated_fee_lamports": estimated_fee_lamports,
            "fee_deviation_pct": round(fee_deviation, 2),
            "significant_deviation": significant,
        }
        self._reconciliations.append(report)
        if significant:
            logger.warning(
                "PnL deviation for %s: expected=%s actual=%s (%.1f%%)",
                opp_id, expected_profit, actual_profit, deviation_pct,
            )
        return report

    @property
    def recent_reconciliations(self) -> list[dict]:
        return self._reconciliations[-100:]

    @property
    def summary(self) -> dict:
        if not self._reconciliations:
            return {"total": 0}
        deviations = [r["deviation_pct"] for r in self._reconciliations]
        significant = [r for r in self._reconciliations if r["significant_deviation"]]
        return {
            "total": len(self._reconciliations),
            "significant_deviations": len(significant),
            "avg_deviation_pct": round(sum(deviations) / len(deviations), 2),
            "max_deviation_pct": round(max(deviations, key=abs), 2),
        }
