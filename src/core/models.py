"""Core data models: MarketQuote, Opportunity, ExecutionResult.

All financial values use Decimal (per CLAUDE.md: "NEVER use float").
Float/int passed to Decimal fields are auto-coerced via ``__post_init__``
to ease migration — new code should always pass Decimal or string literals.

Pipeline: market sources produce ``MarketQuote`` objects, the strategy
layer compares quotes and builds ``Opportunity`` objects, and the executor
produces an ``ExecutionResult``.  All three are frozen (immutable).
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal
from enum import Enum

D = Decimal

ZERO = D("0")
ONE = D("1")
BPS_DIVISOR = D("10000")


class OpportunityStatus(str, Enum):
    """Pipeline status values for an opportunity.

    Status progression (scanner-only phase, Phase 1):
      detected → priced → approved/rejected/simulation_approved
    Execution phase (Phase 3+):
      approved → simulated/simulation_failed → submitted → confirmed/reverted/dropped
    """
    DETECTED = "detected"
    PRICED = "priced"
    APPROVED = "approved"
    REJECTED = "rejected"
    SIMULATION_APPROVED = "simulation_approved"
    SIMULATED = "simulated"
    SIMULATION_FAILED = "simulation_failed"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"     # Solana: landed + finalized
    REVERTED = "reverted"       # transaction landed but errored
    DROPPED = "dropped"         # never landed (blockhash expired, etc.)
    DRY_RUN = "dry_run"


# Fields that should not be coerced to Decimal (strings, bools, timestamps,
# nested objects, ranking metrics).
_NON_DECIMAL_FIELDS = frozenset({
    "venue", "pair", "buy_venue", "sell_venue", "venue_type", "strategy_type",
    "reason", "is_actionable", "warning_flags", "success",
    "quote_timestamp", "liquidity_score", "opportunity",
    "fee_included", "fees_pre_included",
})


def _coerce_decimals(instance: object) -> None:
    """Convert any float/int financial fields to Decimal on a frozen dataclass."""
    for f in fields(instance):  # type: ignore[arg-type]
        if f.name in _NON_DECIMAL_FIELDS:
            continue
        val = getattr(instance, f.name)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            object.__setattr__(instance, f.name, D(str(val)))


@dataclass(frozen=True)
class MarketQuote:
    """A single price quote from one Solana venue for one pair.

    ``venue`` replaces the legacy EVM ``dex`` field.  For backward-compatible
    tests a ``dex`` alias is exposed as a property.
    """
    venue: str
    pair: str
    buy_price: Decimal
    sell_price: Decimal
    fee_bps: Decimal
    # True when the quoted prices already include venue fees.  Jupiter returns
    # post-fee output, so Jupiter quotes set this to True and the strategy
    # skips fee adjustment.  Direct pool adapters (Raydium/Orca, Phase 2)
    # may set this to False and carry the pool fee separately.
    fee_included: bool = False
    volume_usd: Decimal = ZERO       # 24h trading volume in USD
    liquidity_usd: Decimal = ZERO    # pool TVL / route liquidity
    quote_timestamp: float = 0.0     # Unix seconds (not financial — stays float)
    venue_type: str = "aggregator"   # "aggregator" | "amm"

    def __post_init__(self) -> None:
        _coerce_decimals(self)

    @property
    def dex(self) -> str:
        """Legacy alias for venue (kept for any lingering call sites)."""
        return self.venue


@dataclass(frozen=True)
class Opportunity:
    """A detected arbitrage opportunity on Solana.

    ``buy_venue`` / ``sell_venue`` replace the EVM ``buy_dex`` / ``sell_dex``.
    All cost fields are denominated in the quote asset (e.g. USDC) except
    ``net_profit_base`` which is normalised to the base asset (e.g. SOL).
    """
    pair: str
    buy_venue: str
    sell_venue: str
    trade_size: Decimal
    cost_to_buy_quote: Decimal
    proceeds_from_sell_quote: Decimal
    gross_profit_quote: Decimal
    net_profit_quote: Decimal
    net_profit_base: Decimal
    gross_spread_pct: Decimal = ZERO
    venue_fee_cost_quote: Decimal = ZERO
    slippage_cost_quote: Decimal = ZERO
    # Estimated on-chain execution cost (priority fee + Jito tip) in SOL.
    # Replaces EVM gas_cost_base.  In scanner-only mode this is the config's
    # priority_fee_lamports converted to SOL.
    fee_cost_base: Decimal = ZERO
    is_actionable: bool = True
    warning_flags: tuple = ()          # e.g. ("low_liquidity", "stale_quote")
    liquidity_score: float = 1.0       # 0.0–1.0 ranking metric (float ok)
    strategy_type: str = "cross_venue"
    fees_pre_included: bool = False
    buy_liquidity_usd: Decimal = ZERO
    sell_liquidity_usd: Decimal = ZERO
    max_exposure_override: Decimal = ZERO

    def __post_init__(self) -> None:
        _coerce_decimals(self)

    # Solana opportunities are inherently single-chain.  Keeping the property
    # so any generic caller that still asks the question gets a sane answer.
    @property
    def is_cross_chain(self) -> bool:
        return False


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of a single execution attempt.

    On Solana the ``signature`` (base58 tx signature) and
    ``confirmation_slot`` replace EVM tx_hash and block_number.  In scanner-
    only (paper) mode these are empty strings / 0.
    """
    success: bool
    reason: str
    realized_profit_base: Decimal
    opportunity: Opportunity
    signature: str = ""
    confirmation_slot: int = 0

    def __post_init__(self) -> None:
        _coerce_decimals(self)
