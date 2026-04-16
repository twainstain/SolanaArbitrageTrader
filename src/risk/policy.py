"""Risk policy engine — configurable rules for trade approval.

Uses trading_platform's RuleBasedPolicy framework with ArbitrageTrader-specific
rules defined in risk/rules.py. Each rule is independently testable and reorderable.

Principle: No trade is better than a bad trade.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import NamedTuple

from core.models import ZERO, Opportunity, OpportunityStatus as Status
from risk.rules import (
    ExecutionModeRule,
    ExposureLimitRule,
    GasProfitRatioRule,
    LiquidityScoreRule,
    MinProfitRule,
    MinSpreadRule,
    PoolLiquidityRule,
    RateLimitRule,
    WarningFlagRule,
)
from trading_platform.contracts import RiskVerdict as _PlatformVerdict

D = Decimal
logger = logging.getLogger(__name__)


class RiskVerdict(NamedTuple):
    """Result of a risk evaluation."""
    approved: bool
    reason: str
    details: dict


# Per-chain minimum spread thresholds.
CHAIN_MIN_SPREAD_PCT: dict[str, Decimal] = {
    "ethereum": D("0.40"),
    "arbitrum": D("0.20"),
    "base": D("0.15"),
    "optimism": D("0.15"),
    "polygon": D("0.20"),
    "bsc": D("0.20"),
    "avax": D("0.25"),
}

# Per-chain minimum net profit thresholds (in base asset, e.g. WETH).
CHAIN_MIN_NET_PROFIT: dict[str, Decimal] = {
    "ethereum": D("0.005"),
    "arbitrum": D("0.0002"),
    "base": D("0.0002"),
    "optimism": D("0.0002"),
    "polygon": D("0.0003"),
    "bsc": D("0.0003"),
    "avax": D("0.0005"),
}


@dataclass
class RiskPolicy:
    """Configurable risk policy backed by trading_platform's rule-based engine.

    External API is unchanged — callers use evaluate(opportunity, ...) as before.
    Internally, each threshold is a pluggable RiskRule evaluated sequentially.
    """
    min_net_profit: Decimal = D("0.005")
    chain_min_net_profit: dict = field(default_factory=lambda: dict(CHAIN_MIN_NET_PROFIT))
    min_spread_pct: Decimal = D("0.40")
    chain_min_spread_pct: dict = field(default_factory=lambda: dict(CHAIN_MIN_SPREAD_PCT))
    max_slippage_bps: Decimal = D("50")
    min_liquidity_usd: Decimal = D("50000")
    max_quote_age_seconds: float = 60.0
    max_gas_profit_ratio: Decimal = D("0.5")
    max_warning_flags: int = 1
    max_trades_per_hour: int = 100
    max_exposure_per_pair: Decimal = D("10")
    min_liquidity_score: float = 0.3
    execution_enabled: bool = False
    chain_execution_mode: dict = field(default_factory=dict)

    def _build_rules(self) -> list:
        """Build the rule chain from current config. Called on each evaluate()
        to pick up any runtime config changes (e.g. set_chain_mode)."""
        return [
            ExecutionModeRule(self.chain_execution_mode, self.execution_enabled),
            MinSpreadRule(self.chain_min_spread_pct, self.min_spread_pct),
            MinProfitRule(self.chain_min_net_profit, self.min_net_profit),
            PoolLiquidityRule(),
            WarningFlagRule(self.max_warning_flags),
            LiquidityScoreRule(self.min_liquidity_score),
            GasProfitRatioRule(self.max_gas_profit_ratio),
            RateLimitRule(self.max_trades_per_hour),
            ExposureLimitRule(self.max_exposure_per_pair),
        ]

    def evaluate(
        self,
        opportunity: Opportunity,
        current_hour_trades: int = 0,
        current_pair_exposure: Decimal = ZERO,
    ) -> RiskVerdict:
        """Evaluate an opportunity against all risk rules.

        Returns RiskVerdict with approved=True only if ALL rules pass.
        """
        chain = opportunity.chain.lower() if opportunity.chain else ""

        # Build shared analysis dict (populated by rules for dashboard visibility).
        analysis = {
            "net_profit": str(opportunity.net_profit_base),
            "trade_size": str(opportunity.trade_size),
            "gross_spread_pct": str(opportunity.gross_spread_pct),
            "dex_fees": str(opportunity.dex_fee_cost_quote),
            "flash_loan_fee": str(opportunity.flash_loan_fee_quote),
            "slippage_cost": str(opportunity.slippage_cost_quote),
            "gas_cost": str(opportunity.gas_cost_base),
            "liquidity_score": opportunity.liquidity_score,
            "warning_flags": list(opportunity.warning_flags),
            "buy_dex": opportunity.buy_dex,
            "sell_dex": opportunity.sell_dex,
            "fee_included": opportunity.fees_pre_included,
        }

        # Context carries per-evaluation state shared across rules.
        context = {
            "chain": chain,
            "analysis": analysis,
            "current_hour_trades": current_hour_trades,
            "current_pair_exposure": current_pair_exposure,
            "simulation_mode": False,  # set by ExecutionModeRule
        }

        # Run the rule chain.
        rules = self._build_rules()
        for rule in rules:
            verdict = rule.evaluate(opportunity, context)
            if not verdict.approved:
                # Convert platform RiskVerdict to AT's NamedTuple format.
                return RiskVerdict(False, verdict.reason, verdict.details or analysis)

        # All rules passed.
        simulation_mode = context.get("simulation_mode", False)
        if simulation_mode:
            analysis["reason_detail"] = (
                "SIMULATION: All risk checks passed. This trade would be executed "
                "if live mode were enabled (POST /execution {enabled: true})."
            )
            analysis["simulation"] = True
            return RiskVerdict(False, Status.SIMULATION_APPROVED, analysis)

        analysis["reason_detail"] = "All risk checks passed."
        return RiskVerdict(True, "approved", analysis)

    def get_chain_mode(self, chain: str) -> str:
        """Return the execution mode for a chain: 'live', 'simulated', or 'disabled'."""
        mode = self.chain_execution_mode.get(chain.lower())
        if mode:
            return mode
        return "live" if self.execution_enabled else "simulated"

    def set_chain_mode(self, chain: str, mode: str) -> None:
        """Set execution mode for a specific chain."""
        if mode not in ("live", "simulated", "disabled"):
            raise ValueError(f"Invalid mode: {mode}")
        self.chain_execution_mode[chain.lower()] = mode

    def to_dict(self) -> dict:
        """Serialize the current policy for logging/API."""
        return {
            "min_net_profit_default": str(self.min_net_profit),
            "chain_min_net_profit": {k: str(v) for k, v in self.chain_min_net_profit.items()},
            "min_spread_pct_default": str(self.min_spread_pct),
            "chain_min_spread_pct": {k: str(v) for k, v in self.chain_min_spread_pct.items()},
            "max_slippage_bps": str(self.max_slippage_bps),
            "min_liquidity_usd": str(self.min_liquidity_usd),
            "max_quote_age_seconds": self.max_quote_age_seconds,
            "max_gas_profit_ratio": str(self.max_gas_profit_ratio),
            "max_warning_flags": self.max_warning_flags,
            "max_trades_per_hour": self.max_trades_per_hour,
            "max_exposure_per_pair": str(self.max_exposure_per_pair),
            "min_liquidity_score": self.min_liquidity_score,
            "execution_enabled": self.execution_enabled,
            "chain_execution_mode": dict(self.chain_execution_mode),
        }
