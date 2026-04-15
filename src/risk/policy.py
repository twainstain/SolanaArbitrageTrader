"""Risk policy engine — configurable rules for trade approval.

Per the architecture doc, the risk engine must:
  - enforce trade thresholds
  - reject opportunities below minimum expected edge
  - reject stale quotes
  - reject low-liquidity routes
  - reject trades with excessive price impact
  - reject routes too sensitive to gas spikes
  - reject trades with poor execution confidence

Principle: No trade is better than a bad trade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import NamedTuple

from core.models import ZERO, Opportunity, OpportunityStatus as Status

D = Decimal


class RiskVerdict(NamedTuple):
    """Result of a risk evaluation."""
    approved: bool
    reason: str
    details: dict


# Per-chain minimum spread thresholds.
# Ethereum mainnet has high gas ($2-5), so needs a wider spread to be
# profitable.  L2s have cheap gas ($0.01-0.10), so smaller spreads work.
#
# Derivation (1 WETH trade at ~$2300):
#   Ethereum: gas ~$3, flash 9bps=$2.07, slip 10bps=$2.30 → need ~$8 = 0.35%
#   Arbitrum: gas ~$0.10, same fees → need ~$4.5 = 0.20%
#   Base/Optimism: gas ~$0.05, same fees → need ~$4.4 = 0.15%
CHAIN_MIN_SPREAD_PCT: dict[str, Decimal] = {
    "ethereum": D("0.40"),
    "arbitrum": D("0.20"),
    "base": D("0.15"),
    "optimism": D("0.15"),
    "polygon": D("0.20"),
    "bsc": D("0.20"),
    "avax": D("0.25"),
}


@dataclass
class RiskPolicy:
    """Configurable risk policy with named rules.

    Each rule is a threshold. An opportunity must pass ALL rules to be approved.
    """
    # Minimum net profit in base asset.
    # Production: 0.005 WETH (~$10). Testing: 0.0005 (~$1).
    min_net_profit: Decimal = D("0.005")

    # Default minimum spread percentage (used when chain not in override map).
    min_spread_pct: Decimal = D("0.40")

    # Per-chain spread overrides.  L2s with cheap gas can use tighter spreads.
    chain_min_spread_pct: dict = field(default_factory=lambda: dict(CHAIN_MIN_SPREAD_PCT))

    # Maximum allowed slippage in bps
    max_slippage_bps: Decimal = D("50")

    # Minimum pool liquidity in USD for either venue
    min_liquidity_usd: Decimal = D("50000")

    # Maximum quote age in seconds (0 = disabled)
    max_quote_age_seconds: float = 60.0

    # Gas cost must be below this fraction of expected profit (e.g. 0.5 = 50%)
    max_gas_profit_ratio: Decimal = D("0.5")

    # Maximum warning flags allowed
    max_warning_flags: int = 1

    # Maximum trades per interval (rate limiting)
    max_trades_per_hour: int = 100

    # Maximum open exposure per pair in base asset
    max_exposure_per_pair: Decimal = D("10")

    # Minimum liquidity score (0.0-1.0)
    min_liquidity_score: float = 0.3

    # Whether live execution is enabled (global default)
    execution_enabled: bool = False

    # Per-chain execution mode: "live", "simulated", or "disabled".
    # If a chain is not in this dict, falls back to global execution_enabled.
    chain_execution_mode: dict = field(default_factory=dict)

    def evaluate(
        self,
        opportunity: Opportunity,
        current_hour_trades: int = 0,
        current_pair_exposure: Decimal = ZERO,
    ) -> RiskVerdict:
        """Evaluate an opportunity against all risk rules.

        Rule evaluation order matters:
          1. Kill switch — highest authority, checked first
          2. Min profit  — non-negotiable floor (if not profitable, skip everything)
          3. Warning flags — hard veto on compounding risk
          4. Liquidity score — pool quality check
          5. Gas-to-profit ratio — economics check
          6. Rate limiting — velocity control
          7. Exposure limit — position sizing

        Returns RiskVerdict with approved=True only if ALL rules pass.
        Analysis dict is populated even on rejection for dashboard visibility.
        """
        # Build analysis details for every verdict (approved or rejected).
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

        # Rule 1: Execution mode — per-chain or global fallback.
        # When a chain is in simulated mode, we still evaluate ALL other rules
        # to show "simulation_approved" vs "simulation_rejected" on the dashboard.
        chain = opportunity.chain.lower() if opportunity.chain else ""
        chain_mode = self.chain_execution_mode.get(chain)
        if chain_mode == "disabled":
            return RiskVerdict(False, "chain_disabled", {
                **analysis, "reason_detail": f"Execution disabled for chain '{chain}'."
            })
        if chain_mode == "live":
            simulation_mode = False
        elif chain_mode == "simulated":
            simulation_mode = True
        else:
            simulation_mode = not self.execution_enabled

        # Rule 2a: Minimum spread percentage (per-chain)
        effective_min_spread = self.chain_min_spread_pct.get(chain, self.min_spread_pct)
        if opportunity.gross_spread_pct < effective_min_spread:
            analysis["reason_detail"] = (
                f"Spread {opportunity.gross_spread_pct}% is below minimum "
                f"{effective_min_spread}% for {chain or 'default'}."
            )
            analysis["chain_min_spread"] = str(effective_min_spread)
            return RiskVerdict(False, "below_min_spread", analysis)

        # Rule 2b: Minimum net profit
        if opportunity.net_profit_base < self.min_net_profit:
            analysis["reason_detail"] = (
                f"Net profit {opportunity.net_profit_base} is below minimum {self.min_net_profit}. "
                f"Costs: DEX fees={opportunity.dex_fee_cost_quote}, "
                f"flash={opportunity.flash_loan_fee_quote}, "
                f"slippage={opportunity.slippage_cost_quote}, "
                f"gas={opportunity.gas_cost_base}."
            )
            analysis["required"] = str(self.min_net_profit)
            return RiskVerdict(False, "below_min_profit", analysis)

        # Rule 3: Warning flags
        if len(opportunity.warning_flags) > self.max_warning_flags:
            analysis["reason_detail"] = (
                f"Too many warning flags ({len(opportunity.warning_flags)} > max {self.max_warning_flags}): "
                f"{', '.join(opportunity.warning_flags)}."
            )
            return RiskVerdict(False, "too_many_flags", analysis)

        # Rule 4: Liquidity score
        if opportunity.liquidity_score < self.min_liquidity_score:
            analysis["reason_detail"] = (
                f"Liquidity score {opportunity.liquidity_score:.2f} is below minimum {self.min_liquidity_score}. "
                f"Pool may be too thin for the trade size."
            )
            return RiskVerdict(False, "low_liquidity_score", analysis)

        # Rule 5: Gas-to-profit ratio
        if opportunity.net_profit_base > ZERO and opportunity.gas_cost_base > ZERO:
            gas_ratio = opportunity.gas_cost_base / opportunity.net_profit_base
            if gas_ratio > self.max_gas_profit_ratio:
                analysis["reason_detail"] = (
                    f"Gas cost is {float(gas_ratio)*100:.1f}% of profit "
                    f"(max allowed {float(self.max_gas_profit_ratio)*100:.0f}%). "
                    f"Gas={opportunity.gas_cost_base}, profit={opportunity.net_profit_base}."
                )
                analysis["gas_ratio"] = str(gas_ratio)
                return RiskVerdict(False, "gas_too_expensive", analysis)

        # Rule 6: Rate limiting
        if current_hour_trades >= self.max_trades_per_hour:
            analysis["reason_detail"] = (
                f"Rate limit: {current_hour_trades} trades in the last hour "
                f"(max {self.max_trades_per_hour})."
            )
            return RiskVerdict(False, "rate_limit_exceeded", analysis)

        # Rule 7: Exposure limit
        new_exposure = current_pair_exposure + opportunity.trade_size
        if new_exposure > self.max_exposure_per_pair:
            analysis["reason_detail"] = (
                f"Exposure would be {new_exposure} (max {self.max_exposure_per_pair}). "
                f"Current exposure: {current_pair_exposure}."
            )
            return RiskVerdict(False, "exposure_limit", analysis)

        # All rules passed.
        if simulation_mode:
            # In simulation mode: trade WOULD have been approved, but not executing.
            # Dashboard shows "simulation_approved" so operators can see which
            # trades would have been profitable if execution were enabled.
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
            "min_net_profit": str(self.min_net_profit),
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
