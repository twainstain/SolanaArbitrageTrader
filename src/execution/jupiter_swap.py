"""Build a signed Solana swap transaction via Jupiter's /swap endpoint.

Flow
----

1. Ask Jupiter for a quote (same ``/quote`` path the scanner already uses)
   but this time record the full quote response object — Jupiter requires
   the original ``quoteResponse`` when building the swap tx.
2. POST ``/swap`` with ``{quoteResponse, userPublicKey, wrapAndUnwrapSol,
   computeUnitPriceMicroLamports, ...}`` → returns a base64-encoded
   ``VersionedTransaction``.
3. Deserialize, let the caller sign with the wallet, return the signed
   bytes ready for ``sendTransaction``.

Keep the tx build pure: no actual signing inside this module.  The
``Wallet`` sits on the caller's side of the boundary so the secret bytes
never cross into the HTTP client.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

import requests
from solders.transaction import VersionedTransaction

from core.env import get_jupiter_api_url
from core.tokens import get_token

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SwapQuote:
    """Jupiter quote + swap-ready metadata."""
    quote_response: dict             # opaque Jupiter object; pass back verbatim to /swap
    in_amount: int                   # native units of input (lamports, etc.)
    out_amount: int                  # native units of output
    price_impact_pct: float
    route_plan: list                 # list of hop descriptors (for logging)


@dataclass(frozen=True)
class UnsignedSwapTx:
    """A Jupiter-built VersionedTransaction waiting to be signed."""
    transaction: VersionedTransaction
    last_valid_block_height: int
    prioritization_fee_lamports: int


class JupiterSwapBuilder:
    """Build swap transactions via Jupiter HTTP API."""

    def __init__(self, base_url: str | None = None, timeout: float = 4.0) -> None:
        self.base_url = (base_url or get_jupiter_api_url()).rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    # ------------------------------------------------------------------
    # Quote
    # ------------------------------------------------------------------

    def quote(
        self,
        input_symbol: str,
        output_symbol: str,
        input_amount_human: float | str,
        slippage_bps: int,
        only_direct_routes: bool = False,
    ) -> SwapQuote:
        in_tok = get_token(input_symbol)
        out_tok = get_token(output_symbol)
        from decimal import Decimal
        in_native = int(Decimal(str(input_amount_human)) * (Decimal(10) ** in_tok.decimals))
        params = {
            "inputMint": in_tok.mint,
            "outputMint": out_tok.mint,
            "amount": str(in_native),
            "slippageBps": str(slippage_bps),
            "onlyDirectRoutes": "true" if only_direct_routes else "false",
            "restrictIntermediateTokens": "true",
        }
        resp = self._session.get(f"{self.base_url}/quote", params=params, timeout=self.timeout)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("outAmount"):
            raise RuntimeError(f"Jupiter /quote returned no route: {body}")
        return SwapQuote(
            quote_response=body,
            in_amount=int(body["inAmount"]),
            out_amount=int(body["outAmount"]),
            price_impact_pct=float(body.get("priceImpactPct", 0) or 0),
            route_plan=body.get("routePlan", []),
        )

    # ------------------------------------------------------------------
    # Build swap tx (caller signs)
    # ------------------------------------------------------------------

    def build_swap_tx(
        self,
        quote: SwapQuote,
        user_pubkey: str,
        priority_fee_lamports: int,
    ) -> UnsignedSwapTx:
        """POST /swap and return the deserialized VersionedTransaction."""
        # Jupiter takes priority fee as micro-lamports per compute unit.
        # Rough conversion: assume 200k CU budget (typical swap), so
        # micro_lamports_per_cu = priority_fee_lamports × 1_000_000 / 200_000 = × 5.
        # This is conservative — pays a slightly higher tip than asked, which is fine.
        cu_price_micro = max(1, priority_fee_lamports * 5)
        payload = {
            "quoteResponse": quote.quote_response,
            "userPublicKey": user_pubkey,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": {
                "priorityLevelWithMaxLamports": {
                    "maxLamports": priority_fee_lamports,
                    "priorityLevel": "medium",
                },
            },
            "computeUnitPriceMicroLamports": cu_price_micro,
        }
        resp = self._session.post(f"{self.base_url}/swap", json=payload, timeout=self.timeout)
        resp.raise_for_status()
        body = resp.json()
        if "swapTransaction" not in body:
            raise RuntimeError(f"Jupiter /swap missing swapTransaction: {body}")

        tx_bytes = base64.b64decode(body["swapTransaction"])
        tx = VersionedTransaction.from_bytes(tx_bytes)
        return UnsignedSwapTx(
            transaction=tx,
            last_valid_block_height=int(body.get("lastValidBlockHeight", 0)),
            prioritization_fee_lamports=int(body.get("prioritizationFeeLamports", priority_fee_lamports)),
        )


def sign_swap_tx(unsigned: UnsignedSwapTx, wallet) -> bytes:
    """Sign an UnsignedSwapTx using ``wallet`` and return the wire bytes.

    ``wallet`` is an instance of ``execution.wallet.Wallet``.  We re-build
    the VersionedTransaction with the wallet's signature inserted for the
    payer account, then serialize.
    """
    from solders.transaction import VersionedTransaction
    # solders' VersionedTransaction is immutable; we reconstruct it with the
    # payer's signature by creating a new one signed by [wallet.solders_keypair].
    signed = VersionedTransaction(unsigned.transaction.message, [wallet.solders_keypair])
    return bytes(signed)
