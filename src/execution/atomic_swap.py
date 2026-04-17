"""Atomic two-leg swap builder (Phase 3b).

Builds a single ``VersionedTransaction`` that executes two back-to-back
Jupiter swaps (leg A: buy on venue/route X, leg B: sell on venue/route Y)
within the same tx — so either both legs land or neither does.

How
---

Jupiter's ``/swap-instructions`` endpoint returns each swap as a set of
pre-built instructions (setup / swap / cleanup) plus the address lookup
tables (ALTs) each swap needs. We:

  1. Call ``/quote`` twice (once per leg).
  2. Call ``/swap-instructions`` twice — gets ready-to-use Instructions.
  3. De-duplicate ALTs referenced by either leg.
  4. Compile a single ``MessageV0`` containing both legs' setup + swap +
     cleanup instructions, preserving order (leg A first, then leg B),
     with exactly one shared compute-budget prefix (max of the two legs'
     CU limits, max priority fee).
  5. Return an unsigned ``VersionedTransaction``. Caller signs via
     ``execution.wallet.Wallet``.

Safety
------

This module builds a transaction; it never submits anything.
``solana_executor.py`` keeps its 7 gates; this builder is imported into
the dry-run path only and will not bypass any check.

Phase 3c will add Orca/Raydium direct legs by assembling their
instructions in-process rather than delegating to Jupiter.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import requests
from solders.address_lookup_table_account import AddressLookupTableAccount
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from core.env import get_jupiter_api_url
from core.tokens import get_token
from execution.jupiter_swap import JupiterSwapBuilder, SwapQuote

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LegParams:
    """Inputs for one Jupiter leg."""
    input_symbol: str
    output_symbol: str
    input_amount_human: Decimal
    slippage_bps: int
    only_direct_routes: bool = False


@dataclass
class _RawSwapInstructions:
    """Parsed Jupiter /swap-instructions response."""
    compute_budget_instructions: list[Instruction] = field(default_factory=list)
    setup_instructions: list[Instruction] = field(default_factory=list)
    swap_instruction: Instruction | None = None
    cleanup_instruction: Instruction | None = None
    address_lookup_table_addresses: list[str] = field(default_factory=list)
    prioritization_fee_lamports: int = 0


@dataclass(frozen=True)
class AtomicSwapPlan:
    """Resolved plan for a two-leg atomic swap."""
    leg_a_quote: SwapQuote
    leg_b_quote: SwapQuote
    leg_a: LegParams
    leg_b: LegParams

    def expected_net_out_human(self) -> Decimal:
        """Leg B's out_amount, in human units of its output asset.

        Leg A's output feeds leg B as its input; leg B's output is the
        round-trip result. Caller compares it to leg A's input to score
        profit.
        """
        out_tok = get_token(self.leg_b.output_symbol)
        return Decimal(self.leg_b_quote.out_amount) / (Decimal(10) ** out_tok.decimals)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class AtomicSwapBuilder:
    """Plan + compile a two-leg atomic Jupiter swap.

    Not threadsafe — one instance per pipeline tick is fine.
    """

    def __init__(
        self,
        jupiter: JupiterSwapBuilder | None = None,
        base_url: str | None = None,
        timeout: float = 4.0,
    ) -> None:
        self.jupiter = jupiter or JupiterSwapBuilder(base_url=base_url, timeout=timeout)
        self.base_url = (base_url or get_jupiter_api_url()).rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    # ---------- plan ----------

    def plan_two_leg(self, leg_a: LegParams, leg_b: LegParams) -> AtomicSwapPlan:
        """Quote both legs. Does not submit anything.

        ``leg_b.input_amount_human`` is ignored — it's replaced by leg A's
        expected output so the two legs chain correctly (leg A's output
        feeds leg B's input in native units). We keep the field in the
        dataclass for symmetry, and tests may pass a dummy value.
        """
        qa = self.jupiter.quote(
            input_symbol=leg_a.input_symbol,
            output_symbol=leg_a.output_symbol,
            input_amount_human=leg_a.input_amount_human,
            slippage_bps=leg_a.slippage_bps,
            only_direct_routes=leg_a.only_direct_routes,
        )
        # Convert leg A's native out_amount to the human amount leg B
        # should request. Keep full precision via Decimal.
        mid_tok = get_token(leg_a.output_symbol)
        leg_a_out_human = Decimal(qa.out_amount) / (Decimal(10) ** mid_tok.decimals)

        # Replace leg_b input amount with leg A's output so legs chain.
        leg_b_chained = LegParams(
            input_symbol=leg_b.input_symbol,
            output_symbol=leg_b.output_symbol,
            input_amount_human=leg_a_out_human,
            slippage_bps=leg_b.slippage_bps,
            only_direct_routes=leg_b.only_direct_routes,
        )
        if leg_a.output_symbol.upper() != leg_b.input_symbol.upper():
            raise ValueError(
                f"Leg B's input must match leg A's output "
                f"(got {leg_a.output_symbol!r} → {leg_b.input_symbol!r})"
            )

        qb = self.jupiter.quote(
            input_symbol=leg_b_chained.input_symbol,
            output_symbol=leg_b_chained.output_symbol,
            input_amount_human=leg_b_chained.input_amount_human,
            slippage_bps=leg_b_chained.slippage_bps,
            only_direct_routes=leg_b_chained.only_direct_routes,
        )

        return AtomicSwapPlan(
            leg_a_quote=qa,
            leg_b_quote=qb,
            leg_a=leg_a,
            leg_b=leg_b_chained,
        )

    # ---------- compile ----------

    def build_atomic_tx(
        self,
        plan: AtomicSwapPlan,
        user_pubkey: str,
        priority_fee_lamports: int,
        recent_blockhash: Hash,
        alt_resolver=None,
    ) -> VersionedTransaction:
        """Compile both legs into one unsigned VersionedTransaction.

        ``alt_resolver`` is a callable
        ``(list[str]) -> list[AddressLookupTableAccount]`` that fetches ALT
        accounts for the given pubkey strings. Pass an explicit mock in
        tests. In production, leaving this as ``None`` uses
        ``SolanaRPC.get_address_lookup_tables`` as the default (Phase 3c).

        Compute-budget priority-fee instructions from both legs are
        coalesced into one set (leg A's) so we don't double-pay.
        """
        if alt_resolver is None:
            # Late import to keep this module importable in tests that
            # don't hit the RPC package.
            from market.solana_rpc import SolanaRPC as _SolanaRPC
            _rpc = _SolanaRPC()
            alt_resolver = _rpc.get_address_lookup_tables
        raw_a = self._fetch_swap_instructions(plan.leg_a_quote, user_pubkey, priority_fee_lamports)
        raw_b = self._fetch_swap_instructions(plan.leg_b_quote, user_pubkey, priority_fee_lamports)

        # ALT de-duplication by key string (hashable).
        alt_keys: list[str] = []
        for key in (*raw_a.address_lookup_table_addresses, *raw_b.address_lookup_table_addresses):
            if key not in alt_keys:
                alt_keys.append(key)
        alts = alt_resolver(alt_keys) if alt_keys else []

        instructions: list[Instruction] = []
        # One shared compute-budget header — take all unique CB instructions
        # from leg A (they already encode the tip Jupiter suggested). Leg B's
        # CB instructions would stack unnecessarily.
        instructions.extend(raw_a.compute_budget_instructions)
        instructions.extend(raw_a.setup_instructions)
        if raw_a.swap_instruction is not None:
            instructions.append(raw_a.swap_instruction)
        if raw_a.cleanup_instruction is not None:
            instructions.append(raw_a.cleanup_instruction)

        instructions.extend(raw_b.setup_instructions)
        if raw_b.swap_instruction is not None:
            instructions.append(raw_b.swap_instruction)
        if raw_b.cleanup_instruction is not None:
            instructions.append(raw_b.cleanup_instruction)

        msg = MessageV0.try_compile(
            payer=Pubkey.from_string(user_pubkey),
            instructions=instructions,
            address_lookup_table_accounts=alts,
            recent_blockhash=recent_blockhash,
        )
        return VersionedTransaction.populate(msg, [])

    # ---------- helpers ----------

    def _fetch_swap_instructions(
        self,
        quote: SwapQuote,
        user_pubkey: str,
        priority_fee_lamports: int,
    ) -> _RawSwapInstructions:
        cu_price_micro = max(1, priority_fee_lamports * 5)
        payload = {
            "quoteResponse": quote.quote_response,
            "userPublicKey": user_pubkey,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": priority_fee_lamports,
            "computeUnitPriceMicroLamports": cu_price_micro,
        }
        resp = self._session.post(
            f"{self.base_url}/swap-instructions",
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        return _parse_swap_instructions(body)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_swap_instructions(body: dict[str, Any]) -> _RawSwapInstructions:
    """Parse a Jupiter /swap-instructions response body."""
    return _RawSwapInstructions(
        compute_budget_instructions=[
            _parse_instruction(i) for i in body.get("computeBudgetInstructions") or []
        ],
        setup_instructions=[
            _parse_instruction(i) for i in body.get("setupInstructions") or []
        ],
        swap_instruction=_parse_instruction(body["swapInstruction"])
            if body.get("swapInstruction") else None,
        cleanup_instruction=_parse_instruction(body["cleanupInstruction"])
            if body.get("cleanupInstruction") else None,
        address_lookup_table_addresses=list(body.get("addressLookupTableAddresses") or []),
        prioritization_fee_lamports=int(body.get("prioritizationFeeLamports") or 0),
    )


def _parse_instruction(raw: dict[str, Any]) -> Instruction:
    accounts = [
        AccountMeta(
            pubkey=Pubkey.from_string(a["pubkey"]),
            is_signer=bool(a.get("isSigner", False)),
            is_writable=bool(a.get("isWritable", False)),
        )
        for a in raw.get("accounts") or []
    ]
    return Instruction(
        program_id=Pubkey.from_string(raw["programId"]),
        data=base64.b64decode(raw.get("data") or ""),
        accounts=accounts,
    )
