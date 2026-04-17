"""Solana transaction verifier — polls until landed or timed out.

Implements the ``ResultVerifier`` protocol used by ``CandidatePipeline``.
``verify(signature)`` returns a ``VerificationResult`` with Solana-native
fields (``confirmation_slot``, ``fee_paid_lamports``) — no EVM fields.

Lifecycle we encode
-------------------

1. Poll ``getSignatureStatuses`` every 500 ms.
2. If ``confirmationStatus`` reaches ``confirmed`` or ``finalized`` we
   call ``getTransaction`` once to read the final err + meta + post
   balances.
3. If the signature is never seen within ``timeout_seconds`` we mark it
   dropped (blockhash expired, never landed, etc.).
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any

from market.solana_rpc import SolanaRPC
from pipeline.verifier import VerificationResult

logger = logging.getLogger(__name__)

D = Decimal


class TxVerifier:
    """Poll ``getSignatureStatuses`` and resolve to a VerificationResult."""

    def __init__(
        self,
        rpc: SolanaRPC | None = None,
        timeout_seconds: float = 60.0,
        poll_interval: float = 0.5,
    ) -> None:
        self.rpc = rpc or SolanaRPC()
        self.timeout = timeout_seconds
        self.poll_interval = poll_interval

    def verify(self, signature: str) -> VerificationResult:
        deadline = time.monotonic() + self.timeout
        status: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            resp = self._call_statuses(signature)
            if resp is not None:
                status = resp
                conf = status.get("confirmationStatus") or ""
                if conf in ("confirmed", "finalized"):
                    break
                if status.get("err") is not None:
                    break
            time.sleep(self.poll_interval)

        if status is None:
            logger.info("[verifier] %s timed out (dropped)", signature[:12])
            return VerificationResult(
                included=False, reverted=False, dropped=True,
                signature=signature,
            )

        err = status.get("err")
        if err is not None:
            logger.info("[verifier] %s reverted: %s", signature[:12], err)
            return VerificationResult(
                included=True, reverted=True, dropped=False,
                signature=signature,
                confirmation_slot=int(status.get("slot", 0)),
            )

        # Included + no error — fetch the full tx to extract fee + realized PnL.
        return self._full_result(signature, status)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _call_statuses(self, signature: str) -> dict[str, Any] | None:
        try:
            result = self.rpc._call(
                "getSignatureStatuses",
                [[signature], {"searchTransactionHistory": False}],
            )
        except Exception as exc:
            logger.debug("[verifier] getSignatureStatuses error: %s", exc)
            return None
        value = (result or {}).get("value") or []
        return value[0] if value else None

    def _full_result(self, signature: str, status: dict[str, Any]) -> VerificationResult:
        slot = int(status.get("slot", 0))
        fee_lamports = 0
        try:
            tx = self.rpc._call(
                "getTransaction",
                [signature, {
                    "commitment": "confirmed",
                    "encoding": "json",
                    "maxSupportedTransactionVersion": 0,
                }],
            )
            meta = (tx or {}).get("meta") or {}
            fee_lamports = int(meta.get("fee", 0))
        except Exception as exc:
            logger.debug("[verifier] getTransaction fallback: %s", exc)

        fee_base = D(fee_lamports) / D(10**9)
        logger.info(
            "[verifier] %s included: slot=%d fee=%d lamports",
            signature[:12], slot, fee_lamports,
        )
        return VerificationResult(
            included=True, reverted=False, dropped=False,
            signature=signature,
            confirmation_slot=slot,
            fee_paid_lamports=fee_lamports,
            fee_paid_base=fee_base,
        )
