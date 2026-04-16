"""Core data models: MarketQuote, Opportunity, ExecutionResult.

All financial values use Decimal to avoid floating-point precision errors
(per CLAUDE.md: "NEVER use float -- use Decimal or integer math").

Float/int values passed to Decimal fields are auto-coerced via __post_init__
to ease migration -- new code should always pass Decimal or string literals.

The three dataclasses form a pipeline: market sources produce
``MarketQuote`` objects, the scanner/strategy layer compares quotes and
builds ``Opportunity`` objects, and the executor produces an
``ExecutionResult`` for each attempted trade.  All three are frozen
(immutable) to prevent accidental mutation after creation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from decimal import Decimal
from enum import Enum

# Convenience alias for constructing Decimal literals.
D = Decimal

ZERO = D("0")
ONE = D("1")
BPS_DIVISOR = D("10000")


class OpportunityStatus(str, Enum):
    """Pipeline status values for an opportunity.

    Using str enum so values are directly usable as DB strings and JSON
    without .value — e.g., OpportunityStatus.APPROVED == "approved".

    Status progression:
      detected → priced → approved/rejected/simulation_approved
        → simulated/simulation_failed → submitted → included/reverted/not_included
      Or: detected → priced → approved → dry_run (no submitter wired)
    """
    DETECTED = "detected"
    PRICED = "priced"
    APPROVED = "approved"
    REJECTED = "rejected"
    SIMULATION_APPROVED = "simulation_approved"
    SIMULATED = "simulated"
    SIMULATION_FAILED = "simulation_failed"
    SUBMITTED = "submitted"
    INCLUDED = "included"
    REVERTED = "reverted"
    NOT_INCLUDED = "not_included"
    DRY_RUN = "dry_run"


# Chains the bot supports for scanning and execution.
# Single source of truth — used by wallet, API, discovery, and config.
SUPPORTED_CHAINS: tuple[str, ...] = (
    "ethereum", "arbitrum", "base", "optimism",
    "polygon", "bsc", "avax",
)

# Fields that should remain as-is (not coerced to Decimal).
#
# This set lists every dataclass field across MarketQuote, Opportunity, and
# ExecutionResult that is intentionally *not* a financial value and therefore
# must not be converted to Decimal by ``_coerce_decimals()``.  The categories
# are:
#   - String identifiers: dex, pair, buy_dex, sell_dex, venue_type,
#     strategy_type, reason, chain
#   - Booleans: is_actionable, success
#   - Non-financial floats: quote_timestamp (Unix epoch), liquidity_score
#     (0.0-1.0 ranking metric)
#   - Nested objects: opportunity (an Opportunity instance inside
#     ExecutionResult), warning_flags (tuple of strings)
_NON_DECIMAL_FIELDS = frozenset({
    "dex", "pair", "buy_dex", "sell_dex", "venue_type", "strategy_type",
    "reason", "is_actionable", "warning_flags", "success",
    "quote_timestamp", "liquidity_score", "opportunity", "chain",
    "fee_included", "fees_pre_included",
})


def _coerce_decimals(instance: object) -> None:
    """Convert any float/int financial fields to Decimal on a frozen dataclass.

    This auto-coercion exists because many callers (tests, market sources,
    JSON deserialization) naturally produce ``float`` or ``int`` values for
    prices and quantities.  Forcing every call-site to wrap values in
    ``Decimal(str(...))`` would be error-prone and verbose.  Instead, each
    dataclass calls this function in ``__post_init__`` to silently convert
    numeric types to Decimal via ``Decimal(str(value))``.

    The ``str()`` intermediate is critical: ``Decimal(0.1)`` produces
    ``Decimal('0.10000000000000000555...')`` due to IEEE-754, whereas
    ``Decimal(str(0.1))`` produces the expected ``Decimal('0.1')``.

    Fields listed in ``_NON_DECIMAL_FIELDS`` are skipped because they are
    either non-numeric (strings, bools, tuples) or intentionally kept as
    float (timestamps, scores).

    Uses ``object.__setattr__`` to bypass the frozen-dataclass write guard.
    """
    for f in fields(instance):  # type: ignore[arg-type]
        if f.name in _NON_DECIMAL_FIELDS:
            continue
        val = getattr(instance, f.name)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            object.__setattr__(instance, f.name, D(str(val)))


@dataclass(frozen=True)
class MarketQuote:
    dex: str
    pair: str
    buy_price: Decimal
    sell_price: Decimal
    fee_bps: Decimal
    # True when the quoted prices already include DEX fees (on-chain quoters
    # return post-fee amounts).  When True, strategy.evaluate_pair skips its
    # own fee adjustment to avoid double-counting.  fee_bps is still set to
    # the actual pool fee tier for display/logging purposes.
    fee_included: bool = False
    # Enriched fields for risk assessment and ranking (per scanner doc).
    volume_usd: Decimal = ZERO       # 24h trading volume in USD
    liquidity_usd: Decimal = ZERO    # Total liquidity / TVL in USD
    quote_timestamp: float = 0.0     # Unix timestamp (not financial — stays float)
    venue_type: str = "dex"           # "dex" or "cex"

    def __post_init__(self) -> None:
        _coerce_decimals(self)


@dataclass(frozen=True)
class Opportunity:
    pair: str
    buy_dex: str
    sell_dex: str
    trade_size: Decimal
    cost_to_buy_quote: Decimal
    proceeds_from_sell_quote: Decimal
    gross_profit_quote: Decimal
    net_profit_quote: Decimal
    net_profit_base: Decimal
    # Individual cost breakdown (per the video's recommended scanner output).
    gross_spread_pct: Decimal = ZERO
    dex_fee_cost_quote: Decimal = ZERO
    flash_loan_fee_quote: Decimal = ZERO
    slippage_cost_quote: Decimal = ZERO
    gas_cost_base: Decimal = ZERO
    is_actionable: bool = True
    # Risk assessment fields (per arbitrage scanner doc).
    warning_flags: tuple = ()         # e.g. ("low_liquidity", "stale_quote")
    liquidity_score: float = 1.0      # 0.0–1.0 ranking metric (not financial — stays float)
    strategy_type: str = "cross_exchange"
    chain: str = ""                   # chain where this opportunity exists
    fees_pre_included: bool = False   # True when DEX fees were already in the quoted prices
    buy_liquidity_usd: Decimal = ZERO   # estimated TVL of the buy-side pool
    sell_liquidity_usd: Decimal = ZERO  # estimated TVL of the sell-side pool
    # Per-pair exposure limit override (from PairConfig.max_exposure).
    # When set, the risk policy uses this instead of the global max_exposure_per_pair.
    max_exposure_override: Decimal = ZERO

    def __post_init__(self) -> None:
        _coerce_decimals(self)


    @property
    def is_cross_chain(self) -> bool:
        """Detect if buy and sell DEXs are on different chains.

        Uses the DEX naming convention ``"DEXName-ChainName"`` (e.g.
        ``"UniswapV3-Ethereum"``, ``"PancakeV3-BSC"``) to extract the chain
        suffix by splitting on the last hyphen.  If both suffixes exist and
        differ (case-insensitive), the opportunity spans two chains.

        Cross-chain arbitrage cannot be executed atomically in a single
        transaction (no flash-loan across chains), so these opportunities are
        flagged and filtered out by the scanner.  If the naming convention is
        absent (no hyphen), the method conservatively returns ``False``
        (assumes same-chain).
        """
        buy_parts = self.buy_dex.rsplit("-", 1)
        sell_parts = self.sell_dex.rsplit("-", 1)
        if len(buy_parts) == 2 and len(sell_parts) == 2:
            return buy_parts[1].lower() != sell_parts[1].lower()
        return False  # can't determine — assume same chain


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    reason: str
    realized_profit_base: Decimal
    opportunity: Opportunity

    def __post_init__(self) -> None:
        _coerce_decimals(self)
