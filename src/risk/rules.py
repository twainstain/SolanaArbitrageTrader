"""Pluggable risk rules for the ArbitrageTrader.

Each rule implements the trading_platform RiskRule protocol:
  - name: str — identifier for rejection reason tracking
  - evaluate(candidate, context) → RiskVerdict

Rules are evaluated sequentially by RuleBasedPolicy. First failure = hard veto.

Context dict carries per-evaluation state:
  - "chain": str — lowercase chain name
  - "analysis": dict — shared analysis dict for dashboard visibility
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
    """Rule 1: Check if chain is disabled. Set simulation_mode in context."""

    name = "execution_mode"

    def __init__(self, chain_execution_mode: dict, execution_enabled: bool = False):
        self.chain_execution_mode = chain_execution_mode
        self.execution_enabled = execution_enabled

    def evaluate(self, opp: Opportunity, context: dict[str, Any]) -> RiskVerdict:
        chain = context.get("chain", "")
        analysis = context.get("analysis", {})
        chain_mode = self.chain_execution_mode.get(chain)

        if chain_mode == "disabled":
            return RiskVerdict(False, "chain_disabled", {
                **analysis, "reason_detail": f"Execution disabled for chain '{chain}'.",
            })

        # Determine simulation mode and store in context for downstream.
        if chain_mode == "live":
            context["simulation_mode"] = False
        elif chain_mode == "simulated":
            context["simulation_mode"] = True
        else:
            context["simulation_mode"] = not self.execution_enabled

        return RiskVerdict(True, "ok")


class MinSpreadRule:
    """Rule 2a: Minimum spread percentage (per-chain thresholds)."""

    name = "min_spread"

    def __init__(self, chain_thresholds: dict, default: Decimal = D("0.40")):
        self.chain_thresholds = chain_thresholds
        self.default = default

    def evaluate(self, opp: Opportunity, context: dict[str, Any]) -> RiskVerdict:
        chain = context.get("chain", "")
        analysis = context.get("analysis", {})
        threshold = self.chain_thresholds.get(chain, self.default)

        if opp.gross_spread_pct < threshold:
            analysis["reason_detail"] = (
                f"Spread {opp.gross_spread_pct}% is below minimum "
                f"{threshold}% for {chain or 'default'}."
            )
            analysis["chain_min_spread"] = str(threshold)
            return RiskVerdict(False, "below_min_spread", analysis)

        return RiskVerdict(True, "ok")


class MinProfitRule:
    """Rule 2b: Minimum net profit (per-chain thresholds)."""

    name = "min_profit"

    def __init__(self, chain_thresholds: dict, default: Decimal = D("0.005")):
        self.chain_thresholds = chain_thresholds
        self.default = default

    def evaluate(self, opp: Opportunity, context: dict[str, Any]) -> RiskVerdict:
        chain = context.get("chain", "")
        analysis = context.get("analysis", {})
        threshold = self.chain_thresholds.get(chain, self.default)

        if opp.net_profit_base < threshold:
            analysis["reason_detail"] = (
                f"Net profit {opp.net_profit_base} is below minimum "
                f"{threshold} for {chain or 'default'}. "
                f"Costs: DEX fees={opp.dex_fee_cost_quote}, "
                f"flash={opp.flash_loan_fee_quote}, "
                f"slippage={opp.slippage_cost_quote}, "
                f"gas={opp.gas_cost_base}."
            )
            analysis["required"] = str(threshold)
            analysis["chain_min_profit"] = str(threshold)
            return RiskVerdict(False, "below_min_profit", analysis)

        return RiskVerdict(True, "ok")


class PoolLiquidityRule:
    """Rule 2c: Hard minimum pool TVL (per-chain). Safety net for stale pools."""

    name = "pool_liquidity"

    def evaluate(self, opp: Opportunity, context: dict[str, Any]) -> RiskVerdict:
        chain = context.get("chain", "")
        analysis = context.get("analysis", {})

        from core.config import BotConfig
        min_tvl = BotConfig.min_liquidity_for_chain(chain)
        min_pool_liq = min(opp.buy_liquidity_usd, opp.sell_liquidity_usd)

        if min_pool_liq > ZERO and min_pool_liq < min_tvl:
            analysis["reason_detail"] = (
                f"Pool liquidity ${float(min_pool_liq):,.0f} is below "
                f"${float(min_tvl):,.0f} minimum for {chain or 'default'}. "
                f"Buy pool: ${float(opp.buy_liquidity_usd):,.0f}, "
                f"Sell pool: ${float(opp.sell_liquidity_usd):,.0f}."
            )
            analysis["min_pool_liquidity"] = str(min_pool_liq)
            analysis["chain_min_tvl"] = str(min_tvl)
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
                f"Too many warning flags ({len(opp.warning_flags)} > max {self.max_flags}): "
                f"{', '.join(opp.warning_flags)}."
            )
            return RiskVerdict(False, "too_many_flags", analysis)

        return RiskVerdict(True, "ok")


class LiquidityScoreRule:
    """Rule 4: Pool quality score threshold."""

    name = "liquidity_score"

    def __init__(self, min_score: float = 0.3):
        self.min_score = min_score

    def evaluate(self, opp: Opportunity, context: dict[str, Any]) -> RiskVerdict:
        analysis = context.get("analysis", {})

        if opp.liquidity_score < self.min_score:
            analysis["reason_detail"] = (
                f"Liquidity score {opp.liquidity_score:.2f} is below minimum {self.min_score}. "
                f"Pool may be too thin for the trade size."
            )
            return RiskVerdict(False, "low_liquidity_score", analysis)

        return RiskVerdict(True, "ok")


class GasProfitRatioRule:
    """Rule 5: Gas cost must be below a fraction of expected profit."""

    name = "gas_profit_ratio"

    def __init__(self, max_ratio: Decimal = D("0.5")):
        self.max_ratio = max_ratio

    def evaluate(self, opp: Opportunity, context: dict[str, Any]) -> RiskVerdict:
        analysis = context.get("analysis", {})

        if opp.net_profit_base > ZERO and opp.gas_cost_base > ZERO:
            gas_ratio = opp.gas_cost_base / opp.net_profit_base
            if gas_ratio > self.max_ratio:
                analysis["reason_detail"] = (
                    f"Gas cost is {float(gas_ratio)*100:.1f}% of profit "
                    f"(max allowed {float(self.max_ratio)*100:.0f}%). "
                    f"Gas={opp.gas_cost_base}, profit={opp.net_profit_base}."
                )
                analysis["gas_ratio"] = str(gas_ratio)
                return RiskVerdict(False, "gas_too_expensive", analysis)

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
                f"Rate limit: {current} trades in the last hour "
                f"(max {self.max_per_hour})."
            )
            return RiskVerdict(False, "rate_limit_exceeded", analysis)

        return RiskVerdict(True, "ok")


class ExposureLimitRule:
    """Rule 7: Maximum exposure per pair."""

    name = "exposure_limit"

    def __init__(self, max_per_pair: Decimal = D("10")):
        self.max_per_pair = max_per_pair

    def evaluate(self, opp: Opportunity, context: dict[str, Any]) -> RiskVerdict:
        analysis = context.get("analysis", {})
        current_exposure = context.get("current_pair_exposure", ZERO)

        # Per-pair override (for non-WETH pairs like OP/USDC).
        if opp.max_exposure_override > ZERO:
            effective_max = opp.max_exposure_override
        elif opp.trade_size > self.max_per_pair * D("10"):
            logger.warning(
                "Exposure check skipped for %s: trade_size=%s >> global max=%s "
                "(missing max_exposure_override?)",
                opp.pair, opp.trade_size, self.max_per_pair,
            )
            effective_max = opp.trade_size * D("2")
        else:
            effective_max = self.max_per_pair

        new_exposure = current_exposure + opp.trade_size
        if new_exposure > effective_max:
            analysis["reason_detail"] = (
                f"Exposure would be {new_exposure} (max {effective_max}). "
                f"Current exposure: {current_exposure}."
            )
            analysis["effective_max_exposure"] = str(effective_max)
            return RiskVerdict(False, "exposure_limit", analysis)

        return RiskVerdict(True, "ok")
