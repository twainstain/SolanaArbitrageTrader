"""Scanner module — ranking, filtering, and alerting for arbitrage opportunities.

Per the arbitrage scanner doc, a scanner should:
  1. Discover opportunities (strategy.py does this)
  2. Rank by more than raw spread (this module)
  3. Apply risk filters and warning flags (this module)
  4. Alert only for actionable setups (this module)

The scanner wraps ArbitrageStrategy and adds:
  - Multi-factor ranking (net profit, liquidity, freshness, risk flags)
  - Warning flag enrichment
  - Alert thresholds with configurable callbacks
  - Opportunity history for "recently expired" tracking

Usage::

    scanner = OpportunityScanner(config, strategy)
    ranked = scanner.scan_and_rank(quotes)
    # ranked is a list of Opportunity sorted by composite score, best first
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

from config import BotConfig, PairConfig
from log import get_logger
from models import ZERO, MarketQuote, Opportunity

D = Decimal
from strategy import ArbitrageStrategy

logger = get_logger(__name__)

D = Decimal


@dataclass
class ScanResult:
    """Result of a full scan cycle with ranked opportunities."""
    timestamp: float
    total_quotes: int
    opportunities: list[Opportunity]    # Sorted by composite_score descending.
    rejected_count: int                 # Opportunities below threshold or flagged.
    best: Opportunity | None            # Top-ranked opportunity, or None.


class OpportunityScanner:
    """Scan, rank, filter, and alert on arbitrage opportunities.

    Wraps ArbitrageStrategy with multi-factor ranking and risk assessment.
    """

    def __init__(
        self,
        config: BotConfig,
        strategy: ArbitrageStrategy | None = None,
        pairs: list[PairConfig] | None = None,
        alert_min_net_profit: Decimal = ZERO,
        alert_max_warning_flags: int = 1,
    ) -> None:
        self.config = config
        self.strategy = strategy or ArbitrageStrategy(config, pairs=pairs)
        self.alert_min_net_profit = alert_min_net_profit
        self.alert_max_warning_flags = alert_max_warning_flags
        self._history: list[ScanResult] = []

    def scan_and_rank(self, quotes: list[MarketQuote]) -> ScanResult:
        """Evaluate all cross-DEX pairs, rank, filter, and return a ScanResult."""
        # Find ALL opportunities (not just the best one).
        opportunities = self._find_all_opportunities(quotes)

        # Rank by composite score.
        scored = [(self._composite_score(opp), opp) for opp in opportunities]
        scored.sort(key=lambda x: x[0], reverse=True)

        # Split into actionable vs rejected.
        actionable = []
        rejected_count = 0
        for score, opp in scored:
            if self._passes_alert_filter(opp):
                actionable.append(opp)
            else:
                rejected_count += 1

        result = ScanResult(
            timestamp=time.time(),
            total_quotes=len(quotes),
            opportunities=actionable,
            rejected_count=rejected_count,
            best=actionable[0] if actionable else None,
        )

        # Emit alert for top opportunity.
        if result.best:
            self._emit_alert(result.best)

        self._history.append(result)
        return result

    @property
    def recent_history(self) -> list[ScanResult]:
        """Return the last 100 scan results for analysis."""
        return self._history[-100:]

    def _find_all_opportunities(self, quotes: list[MarketQuote]) -> list[Opportunity]:
        """Evaluate every cross-DEX pair and return all profitable opportunities.

        Filters out cross-chain pairs (can't be executed atomically) and
        opportunities from pools with very low liquidity (inflated spreads).
        """
        if len(quotes) < 2:
            return []

        results: list[Opportunity] = []
        skipped_same_dex = 0
        skipped_diff_pair = 0
        skipped_unprofitable = 0
        skipped_cross_chain = 0
        skipped_low_liq = 0
        evaluated = 0

        for buy_quote in quotes:
            for sell_quote in quotes:
                if buy_quote.dex == sell_quote.dex:
                    skipped_same_dex += 1
                    continue
                if buy_quote.pair != sell_quote.pair:
                    skipped_diff_pair += 1
                    continue
                evaluated += 1
                opp = self.strategy.evaluate_pair(buy_quote, sell_quote)
                if opp is None:
                    skipped_unprofitable += 1
                    continue
                # Skip cross-chain opportunities — can't atomic execute.
                if opp.is_cross_chain:
                    skipped_cross_chain += 1
                    continue
                # Skip if either pool has low estimated liquidity.
                # $1M minimum ensures pools can absorb flash loan trade sizes
                # without massive slippage. Pools below this produce fake spreads.
                min_liq = min(buy_quote.liquidity_usd, sell_quote.liquidity_usd)
                if min_liq > ZERO and min_liq < D("1000000"):
                    skipped_low_liq += 1
                    continue
                results.append(opp)

        logger.info(
            "[scanner] %d quotes → %d pairs evaluated | "
            "unprofitable=%d cross_chain=%d low_liq=%d | %d passed",
            len(quotes), evaluated,
            skipped_unprofitable, skipped_cross_chain, skipped_low_liq,
            len(results),
        )
        return results

    def _composite_score(self, opp: Opportunity) -> float:
        """Compute a multi-factor ranking score.

        Weights rationale:
          - 0.50 net profit: primary signal — we're here to make money
          - 0.25 liquidity:  second most important — illiquid pools give false signals
          - 0.15 flag safety: penalize stale/risky opportunities
          - 0.10 spread:     tie-breaker — wider raw spread = more room for error

        Normalization caps:
          - Profit capped at 1.0 WETH (~$2300) — prevents one outlier from dominating
          - Spread capped at 5% — above this, likely a data error or illiquid pool

        Returns float — this is a ranking metric, not a financial value.
        """
        # Convert Decimal fields to float for ranking math.
        net_profit = float(opp.net_profit_base)
        spread_pct = float(opp.gross_spread_pct)

        profit_score = min(net_profit / 1.0, 1.0) if net_profit > 0 else 0.0
        liq_score = opp.liquidity_score

        # Each warning flag reduces score by 0.25 — 4 flags = zero score.
        flag_score = max(0.0, 1.0 - len(opp.warning_flags) * 0.25)

        spread_score = min(spread_pct / 5.0, 1.0)

        return (
            0.50 * profit_score
            + 0.25 * liq_score
            + 0.15 * flag_score
            + 0.10 * spread_score
        )

    def _passes_alert_filter(self, opp: Opportunity) -> bool:
        """Return True if the opportunity should be surfaced (not rejected).

        This is a hard veto — separate from composite scoring — because multiple
        warning flags (e.g., stale + low liquidity) create compounding risk that
        a weighted score can't adequately capture. Better to reject outright.
        """
        if opp.net_profit_base < self.alert_min_net_profit:
            return False
        if len(opp.warning_flags) > self.alert_max_warning_flags:
            return False
        return True

    def _emit_alert(self, opp: Opportunity) -> None:
        """Log an alert for an actionable opportunity."""
        base_asset = opp.pair.split("/", 1)[0] if "/" in opp.pair else self.config.base_asset
        flags_str = ", ".join(opp.warning_flags) if opp.warning_flags else "none"
        logger.info(
            "ALERT: %s buy=%s sell=%s spread=%.2f%% net=%.6f %s "
            "liq_score=%.2f flags=[%s]",
            opp.pair, opp.buy_dex, opp.sell_dex,
            float(opp.gross_spread_pct), float(opp.net_profit_base),
            base_asset, opp.liquidity_score, flags_str,
        )
