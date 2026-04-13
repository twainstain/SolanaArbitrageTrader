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

from arbitrage_bot.config import BotConfig
from arbitrage_bot.log import get_logger
from arbitrage_bot.models import MarketQuote, Opportunity
from arbitrage_bot.strategy import ArbitrageStrategy

logger = get_logger(__name__)


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
        alert_min_net_profit: float = 0.0,
        alert_max_warning_flags: int = 1,
    ) -> None:
        self.config = config
        self.strategy = strategy or ArbitrageStrategy(config)
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
        """Evaluate every cross-DEX pair and return all profitable opportunities."""
        if len(quotes) < 2:
            return []

        results: list[Opportunity] = []
        for buy_quote in quotes:
            for sell_quote in quotes:
                if buy_quote.dex == sell_quote.dex:
                    continue
                if buy_quote.pair != sell_quote.pair:
                    continue
                opp = self.strategy._evaluate_pair(buy_quote, sell_quote)
                if opp is not None:
                    results.append(opp)
        return results

    def _composite_score(self, opp: Opportunity) -> float:
        """Compute a multi-factor ranking score.

        Factors (per scanner doc):
          - net profit (primary, weighted 0.5)
          - liquidity score (weighted 0.25)
          - absence of warning flags (weighted 0.15)
          - spread quality (weighted 0.10)
        """
        # Normalize net profit to a 0-1 scale (cap at 1.0 WETH).
        profit_score = min(opp.net_profit_base / 1.0, 1.0) if opp.net_profit_base > 0 else 0.0

        # Liquidity score is already 0-1.
        liq_score = opp.liquidity_score

        # Flag penalty: 1.0 if no flags, decays with more flags.
        flag_score = max(0.0, 1.0 - len(opp.warning_flags) * 0.25)

        # Spread quality: gross_spread_pct capped at 5%.
        spread_score = min(opp.gross_spread_pct / 5.0, 1.0)

        return (
            0.50 * profit_score
            + 0.25 * liq_score
            + 0.15 * flag_score
            + 0.10 * spread_score
        )

    def _passes_alert_filter(self, opp: Opportunity) -> bool:
        """Return True if the opportunity should be surfaced (not rejected)."""
        if opp.net_profit_base < self.alert_min_net_profit:
            return False
        if len(opp.warning_flags) > self.alert_max_warning_flags:
            return False
        return True

    def _emit_alert(self, opp: Opportunity) -> None:
        """Log an alert for an actionable opportunity."""
        flags_str = ", ".join(opp.warning_flags) if opp.warning_flags else "none"
        logger.info(
            "ALERT: %s buy=%s sell=%s spread=%.2f%% net=%.6f %s "
            "liq_score=%.2f flags=[%s]",
            opp.pair, opp.buy_dex, opp.sell_dex,
            opp.gross_spread_pct, opp.net_profit_base,
            self.config.base_asset, opp.liquidity_score, flags_str,
        )
