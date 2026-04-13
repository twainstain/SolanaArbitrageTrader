"""Core data models: MarketQuote, Opportunity, ExecutionResult."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class MarketQuote:
    dex: str
    pair: str
    buy_price: float
    sell_price: float
    fee_bps: float
    # Enriched fields for risk assessment and ranking (per scanner doc).
    volume_usd: float = 0.0          # 24h trading volume in USD
    liquidity_usd: float = 0.0       # Total liquidity / TVL in USD
    quote_timestamp: float = 0.0     # Unix timestamp when quote was fetched
    venue_type: str = "dex"           # "dex" or "cex"


@dataclass(frozen=True)
class Opportunity:
    pair: str
    buy_dex: str
    sell_dex: str
    trade_size: float
    cost_to_buy_quote: float
    proceeds_from_sell_quote: float
    gross_profit_quote: float
    net_profit_quote: float
    net_profit_base: float
    # Individual cost breakdown (per the video's recommended scanner output).
    gross_spread_pct: float = 0.0
    dex_fee_cost_quote: float = 0.0
    flash_loan_fee_quote: float = 0.0
    slippage_cost_quote: float = 0.0
    gas_cost_base: float = 0.0
    is_actionable: bool = True
    # Risk assessment fields (per arbitrage scanner doc).
    warning_flags: tuple = ()         # e.g. ("low_liquidity", "stale_quote")
    liquidity_score: float = 1.0      # 0.0 (illiquid) to 1.0 (highly liquid)
    strategy_type: str = "cross_exchange"


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    reason: str
    realized_profit_base: float
    opportunity: Opportunity
