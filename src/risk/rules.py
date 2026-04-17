"""Pluggable risk rules for SolanaTrader.

Each rule implements the trading_platform RiskRule protocol:
  - name: str — identifier for rejection reason tracking
  - evaluate(candidate, context) → RiskVerdict

Rules are evaluated sequentially by RiskPolicy. First failure = hard veto.

Context dict carries per-evaluation state:
  - "analysis": dict — shared analysis for dashboard/logging
  - "current_hour_trades": int — for rate limiting
  - "current_pair_exposure": Decimal — for exposure limiting
  - "simulation_mode": bool — set by ExecutionModeRule for downstream use
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from trading_platform.contracts import RiskVerdict

from core.models import ZERO, Opportunity

D = Decimal
logger = logging.getLogger(__name__)


class ExecutionModeRule:
    """Rule 1: resolve simulation vs. live mode and hard-veto when disabled."""

    name = "execution_mode"

    def __init__(self, execution_enabled: bool = False, disabled: bool = False):
        self.execution_enabled = execution_enabled
        self.disabled = disabled

    def evaluate(self, opp: Opportunity, context: dict[str, Any]) -> RiskVerdict:
        analysis = context.get("analysis", {})
        if self.disabled:
            return RiskVerdict(False, "execution_disabled", {
                **analysis, "reason_detail": "Solana execution is disabled.",
            })
        context["simulation_mode"] = not self.execution_enabled
        return RiskVerdict(True, "ok")


class MinSpreadRule:
    """Rule 2a: Minimum gross-spread %."""

    name = "min_spread"

    def __init__(self, min_spread_pct: Decimal = D("0.05")):
        self.min_spread_pct = min_spread_pct

    def evaluate(self, opp: Opportunity, context: dict[str, Any]) -> RiskVerdict:
        analysis = context.get("analysis", {})
        if opp.gross_spread_pct < self.min_spread_pct:
            analysis["reason_detail"] = (
                f"Spread {opp.gross_spread_pct}% is below minimum {self.min_spread_pct}%."
            )
            analysis["min_spread"] = str(self.min_spread_pct)
            return RiskVerdict(False, "below_min_spread", analysis)
        return RiskVerdict(True, "ok")


class MinProfitRule:
    """Rule 2b: Minimum net profit in base asset (SOL)."""

    name = "min_profit"

    def __init__(self, min_profit_base: Decimal = D("0.001")):
        self.min_profit_base = min_profit_base

    def evaluate(self, opp: Opportunity, context: dict[str, Any]) -> RiskVerdict:
        analysis = context.get("analysis", {})
        if opp.net_profit_base < self.min_profit_base:
            analysis["reason_detail"] = (
                f"Net profit {opp.net_profit_base} SOL is below minimum {self.min_profit_base}. "
                f"Costs: venue_fees={opp.venue_fee_cost_quote}, "
                f"slippage={opp.slippage_cost_quote}, fee_cost_base={opp.fee_cost_base}."
            )
            analysis["required"] = str(self.min_profit_base)
            return RiskVerdict(False, "below_min_profit", analysis)
        return RiskVerdict(True, "ok")


class PoolLiquidityRule:
    """Rule 2c: Hard minimum pool/route liquidity."""

    name = "pool_liquidity"

    def __init__(self, min_liquidity_usd: Decimal = D("100000")):
        self.min_liquidity_usd = min_liquidity_usd

    def evaluate(self, opp: Opportunity, context: dict[str, Any]) -> RiskVerdict:
        analysis = context.get("analysis", {})
        min_liq = min(opp.buy_liquidity_usd, opp.sell_liquidity_usd)
        if min_liq > ZERO and min_liq < self.min_liquidity_usd:
            analysis["reason_detail"] = (
                f"Pool liquidity ${float(min_liq):,.0f} is below "
                f"${float(self.min_liquidity_usd):,.0f} minimum."
            )
            return RiskVerdict(False, "pool_too_thin", analysis)
        return RiskVerdict(True, "ok")


class WarningFlagRule:
    """Rule 3: Too many warning flags = compounding risk."""

    name = "warning_flags"

    def __init__(self, max_flags: int = 1):
        self.max_flags = max_flags

    def evaluate(self, opp: Opportunity, context: dict[str, Any]) -> RiskVerdict:
        analysis = context.get("analysis", {})
        if len(opp.warning_flags) > self.max_flags:
            analysis["reason_detail"] = (
                f"Too many warning flags ({len(opp.warning_flags)} > {self.max_flags}): "
                f"{', '.join(opp.warning_flags)}."
            )
            return RiskVerdict(False, "too_many_flags", analysis)
        return RiskVerdict(True, "ok")


class LiquidityScoreRule:
    """Rule 4: Composite liquidity quality score threshold."""

    name = "liquidity_score"

    def __init__(self, min_score: float = 0.3):
        self.min_score = min_score

    def evaluate(self, opp: Opportunity, context: dict[str, Any]) -> RiskVerdict:
        analysis = context.get("analysis", {})
        if opp.liquidity_score < self.min_score:
            analysis["reason_detail"] = (
                f"Liquidity score {opp.liquidity_score:.2f} is below minimum {self.min_score}."
            )
            return RiskVerdict(False, "low_liquidity_score", analysis)
        return RiskVerdict(True, "ok")


class FeeProfitRatioRule:
    """Rule 5: Execution cost must stay below a fraction of expected profit.

    Replaces the EVM GasProfitRatioRule — on Solana the equivalent "cost" is
    the priority fee + Jito tip (``fee_cost_base``).
    """

    name = "fee_profit_ratio"

    def __init__(self, max_ratio: Decimal = D("0.5")):
        self.max_ratio = max_ratio

    def evaluate(self, opp: Opportunity, context: dict[str, Any]) -> RiskVerdict:
        analysis = context.get("analysis", {})
        if opp.net_profit_base > ZERO and opp.fee_cost_base > ZERO:
            ratio = opp.fee_cost_base / opp.net_profit_base
            if ratio > self.max_ratio:
                analysis["reason_detail"] = (
                    f"Execution cost is {float(ratio)*100:.1f}% of profit "
                    f"(max {float(self.max_ratio)*100:.0f}%). "
                    f"fee={opp.fee_cost_base}, profit={opp.net_profit_base}."
                )
                return RiskVerdict(False, "fee_too_expensive", analysis)
        return RiskVerdict(True, "ok")


class RateLimitRule:
    """Rule 6: Maximum trades per hour."""

    name = "rate_limit"

    def __init__(self, max_per_hour: int = 100):
        self.max_per_hour = max_per_hour

    def evaluate(self, opp: Opportunity, context: dict[str, Any]) -> RiskVerdict:
        analysis = context.get("analysis", {})
        current = context.get("current_hour_trades", 0)
        if current >= self.max_per_hour:
            analysis["reason_detail"] = (
                f"Rate limit: {current} trades in the last hour (max {self.max_per_hour})."
            )
            return RiskVerdict(False, "rate_limit_exceeded", analysis)
        return RiskVerdict(True, "ok")


class ExposureLimitRule:
    """Rule 7: Maximum exposure per pair (in base-asset units)."""

    name = "exposure_limit"

    def __init__(self, max_per_pair: Decimal = D("100")):
        self.max_per_pair = max_per_pair

    def evaluate(self, opp: Opportunity, context: dict[str, Any]) -> RiskVerdict:
        analysis = context.get("analysis", {})
        current_exposure = context.get("current_pair_exposure", ZERO)
        if opp.max_exposure_override > ZERO:
            effective_max = opp.max_exposure_override
        elif opp.trade_size > self.max_per_pair * D("10"):
            logger.warning(
                "Exposure check skipped for %s: trade_size=%s >> global max=%s",
                opp.pair, opp.trade_size, self.max_per_pair,
            )
            effective_max = opp.trade_size * D("2")
        else:
            effective_max = self.max_per_pair

        new_exposure = current_exposure + opp.trade_size
        if new_exposure > effective_max:
            analysis["reason_detail"] = (
                f"Exposure would be {new_exposure} (max {effective_max}). "
                f"Current: {current_exposure}."
            )
            return RiskVerdict(False, "exposure_limit", analysis)
        return RiskVerdict(True, "ok")
