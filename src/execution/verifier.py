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

    def verify(
        self,
        signature: str,
        wallet_pubkey: str | None = None,
        base_mint: str | None = None,
    ) -> VerificationResult:
        """Poll until landed-or-dropped, then resolve to VerificationResult.

        Phase 3b: when ``wallet_pubkey`` and ``base_mint`` are supplied,
        parse realized profit from ``meta.pre{,Token}Balances`` vs
        ``meta.post{,Token}Balances`` and populate ``actual_profit_base``.
        For SOL-native base, we add the fee back to the delta so the
        number reflects pre-fee arbitrage profit.
        """
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
        return self._full_result(signature, status, wallet_pubkey, base_mint)

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

    def _full_result(
        self,
        signature: str,
        status: dict[str, Any],
        wallet_pubkey: str | None = None,
        base_mint: str | None = None,
    ) -> VerificationResult:
        slot = int(status.get("slot", 0))
        fee_lamports = 0
        actual_profit_base = D("0")
        tx: dict[str, Any] | None = None
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

        if tx and wallet_pubkey:
            actual_profit_base = _realized_profit_from_tx(tx, wallet_pubkey, base_mint, fee_lamports)

        fee_base = D(fee_lamports) / D(10**9)
        logger.info(
            "[verifier] %s included: slot=%d fee=%d lamports profit_base=%s",
            signature[:12], slot, fee_lamports, actual_profit_base,
        )
        return VerificationResult(
            included=True, reverted=False, dropped=False,
            signature=signature,
            confirmation_slot=slot,
            fee_paid_lamports=fee_lamports,
            fee_paid_base=fee_base,
            actual_profit_base=actual_profit_base,
        )


# ---------------------------------------------------------------------------
# Balance delta parsing (Phase 3b)
# ---------------------------------------------------------------------------


# Native SOL's "mint" in SPL contexts — the WSOL mint. A base_mint of None or
# "So11..." triggers the native-SOL accounting path.
_WSOL_MINT = "So11111111111111111111111111111111111111112"


def _realized_profit_from_tx(
    tx: dict[str, Any],
    wallet_pubkey: str,
    base_mint: str | None,
    fee_lamports: int,
) -> Decimal:
    """Extract net base-asset delta for ``wallet_pubkey`` from a getTransaction.

    For native SOL (base_mint is None or wSOL): computes
    ``post_balance - pre_balance + fee`` in SOL — the fee is added back
    so the returned number represents arbitrage profit before tx cost,
    matching how expected_net_profit is computed elsewhere.

    For SPL base assets: locates the wallet's token account by owner+mint
    in preTokenBalances/postTokenBalances and returns the delta in human
    units (using the balance entry's ``uiTokenAmount.decimals``).

    Returns ``Decimal("0")`` if the needed entries aren't present.
    """
    meta = (tx or {}).get("meta") or {}
    tx_inner = (tx or {}).get("transaction") or {}
    message = tx_inner.get("message") or {}
    account_keys_raw = message.get("accountKeys") or []
    # accountKeys can be list[str] (legacy) or list[dict] (with signer/writable flags).
    account_keys: list[str] = []
    for k in account_keys_raw:
        if isinstance(k, dict):
            pk = k.get("pubkey")
            if pk:
                account_keys.append(pk)
        elif isinstance(k, str):
            account_keys.append(k)

    if base_mint in (None, _WSOL_MINT, "SOL"):
        # Native SOL path.
        try:
            idx = account_keys.index(wallet_pubkey)
        except ValueError:
            return D("0")
        pre = (meta.get("preBalances") or [])
        post = (meta.get("postBalances") or [])
        if idx >= len(pre) or idx >= len(post):
            return D("0")
        delta_lamports = int(post[idx]) - int(pre[idx]) + int(fee_lamports)
        return D(delta_lamports) / D(10**9)

    # SPL path: match owner+mint in pre/post token balances. Each entry has
    # {accountIndex, owner, mint, uiTokenAmount: {amount, decimals, uiAmount}}.
    pre_tb = meta.get("preTokenBalances") or []
    post_tb = meta.get("postTokenBalances") or []
    decimals = 0
    pre_amount = 0
    post_amount = 0
    for row in pre_tb:
        if row.get("owner") == wallet_pubkey and row.get("mint") == base_mint:
            uta = row.get("uiTokenAmount") or {}
            pre_amount = int(uta.get("amount", 0) or 0)
            decimals = int(uta.get("decimals", 0) or 0)
            break
    for row in post_tb:
        if row.get("owner") == wallet_pubkey and row.get("mint") == base_mint:
            uta = row.get("uiTokenAmount") or {}
            post_amount = int(uta.get("amount", 0) or 0)
            if decimals == 0:
                decimals = int(uta.get("decimals", 0) or 0)
            break

    if decimals == 0 and post_amount == 0 and pre_amount == 0:
        return D("0")
    delta_native = post_amount - pre_amount
    # SPL assets don't pay the SOL fee out of this balance, so no fee add-back.
    return D(delta_native) / (D(10) ** decimals)
