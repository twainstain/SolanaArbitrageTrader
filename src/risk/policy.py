"""Risk policy engine — configurable rules for trade approval on Solana.

Principle (per CLAUDE.md): capital preservation > profit.  No trade is
better than a bad trade.  Every rule is a pluggable, independently
testable unit with a single responsibility.

No per-chain complexity — Solana is single-chain.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import NamedTuple

from core.models import ZERO, Opportunity, OpportunityStatus as Status
from risk.rules import (
    ExecutionModeRule,
    ExposureLimitRule,
    FeeProfitRatioRule,
    LiquidityScoreRule,
    MinProfitRule,
    MinSpreadRule,
    PoolLiquidityRule,
    RateLimitRule,
    WarningFlagRule,
)

D = Decimal
logger = logging.getLogger(__name__)


class RiskVerdict(NamedTuple):
    """Result of a risk evaluation (NamedTuple for legacy callers)."""
    approved: bool
    reason: str
    details: dict


@dataclass
class RiskPolicy:
    """Flat-threshold Solana risk policy backed by a rule chain."""

    min_net_profit: Decimal = D("0.001")       # SOL
    min_spread_pct: Decimal = D("0.05")        # %
    max_slippage_bps: Decimal = D("50")
    min_liquidity_usd: Decimal = D("100000")
    max_quote_age_seconds: float = 10.0        # Solana slots ~400ms → stricter than EVM
    max_fee_profit_ratio: Decimal = D("0.5")
    max_warning_flags: int = 1
    max_trades_per_hour: int = 100
    max_exposure_per_pair: Decimal = D("100")  # SOL
    min_liquidity_score: float = 0.3
    execution_enabled: bool = False
    disabled: bool = False

    def _build_rules(self) -> list:
        """Build the rule chain from current config."""
        return [
            ExecutionModeRule(self.execution_enabled, self.disabled),
            MinSpreadRule(self.min_spread_pct),
            MinProfitRule(self.min_net_profit),
            PoolLiquidityRule(self.min_liquidity_usd),
            WarningFlagRule(self.max_warning_flags),
            LiquidityScoreRule(self.min_liquidity_score),
            FeeProfitRatioRule(self.max_fee_profit_ratio),
            RateLimitRule(self.max_trades_per_hour),
            ExposureLimitRule(self.max_exposure_per_pair),
        ]

    def evaluate(
        self,
        opportunity: Opportunity,
        current_hour_trades: int = 0,
        current_pair_exposure: Decimal = ZERO,
    ) -> RiskVerdict:
        analysis = {
            "net_profit": str(opportunity.net_profit_base),
            "trade_size": str(opportunity.trade_size),
            "gross_spread_pct": str(opportunity.gross_spread_pct),
            "venue_fees": str(opportunity.venue_fee_cost_quote),
            "slippage_cost": str(opportunity.slippage_cost_quote),
            "fee_cost_base": str(opportunity.fee_cost_base),
            "liquidity_score": opportunity.liquidity_score,
            "warning_flags": list(opportunity.warning_flags),
            "buy_venue": opportunity.buy_venue,
            "sell_venue": opportunity.sell_venue,
            "fee_included": opportunity.fees_pre_included,
        }
        context = {
            "analysis": analysis,
            "current_hour_trades": current_hour_trades,
            "current_pair_exposure": current_pair_exposure,
            "simulation_mode": False,
        }

        for rule in self._build_rules():
            verdict = rule.evaluate(opportunity, context)
            if not verdict.approved:
                return RiskVerdict(False, verdict.reason, verdict.details or analysis)

        if context.get("simulation_mode"):
            analysis["reason_detail"] = (
                "SIMULATION: All risk checks passed. Execution is disabled."
            )
            analysis["simulation"] = True
            return RiskVerdict(False, Status.SIMULATION_APPROVED, analysis)

        analysis["reason_detail"] = "All risk checks passed."
        return RiskVerdict(True, "approved", analysis)

    def to_dict(self) -> dict:
        return {
            "min_net_profit": str(self.min_net_profit),
            "min_spread_pct": str(self.min_spread_pct),
            "max_slippage_bps": str(self.max_slippage_bps),
            "min_liquidity_usd": str(self.min_liquidity_usd),
            "max_quote_age_seconds": self.max_quote_age_seconds,
            "max_fee_profit_ratio": str(self.max_fee_profit_ratio),
            "max_warning_flags": self.max_warning_flags,
            "max_trades_per_hour": self.max_trades_per_hour,
            "max_exposure_per_pair": str(self.max_exposure_per_pair),
            "min_liquidity_score": self.min_liquidity_score,
            "execution_enabled": self.execution_enabled,
            "disabled": self.disabled,
        }
