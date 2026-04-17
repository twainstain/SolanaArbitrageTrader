"""Scanner — ranking, filtering, and alerting for Solana arbitrage opportunities.

Solana-native simplifications vs the legacy EVM scanner:
  - no per-chain liquidity gates (single-chain product)
  - no cross-chain filter (opportunities are always same-chain)
  - simpler venue-based outlier detection (median across all venues per pair)

The scanner wraps ArbitrageStrategy and adds:
  - multi-factor ranking (net profit, liquidity, freshness, warning flags)
  - hard liquidity / price-deviation vetoes
  - alert thresholds
  - scan-record collection for async DB flush
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from decimal import Decimal

from core.config import BotConfig, PairConfig
from observability.log import get_logger
from core.models import ZERO, MarketQuote, Opportunity
from strategy import ArbitrageStrategy

logger = get_logger(__name__)

D = Decimal
TWO = D("2")


@dataclass
class ScanResult:
    """Result of a full scan cycle with ranked opportunities."""
    timestamp: float
    total_quotes: int
    opportunities: list[Opportunity]    # sorted by composite score, best first
    rejected_count: int
    best: Opportunity | None


class OpportunityScanner:
    """Scan, rank, filter, and alert on arbitrage opportunities."""

    def __init__(
        self,
        config: BotConfig,
        strategy: ArbitrageStrategy | None = None,
        pairs: list[PairConfig] | None = None,
        alert_min_net_profit: Decimal = ZERO,
        alert_max_warning_flags: int = 1,
        min_liquidity_usd: Decimal = D("100000"),
        max_price_deviation: Decimal = D("0.02"),
    ) -> None:
        self.config = config
        self.strategy = strategy or ArbitrageStrategy(config, pairs=pairs)
        self.alert_min_net_profit = alert_min_net_profit
        self.alert_max_warning_flags = alert_max_warning_flags
        self.min_liquidity_usd = min_liquidity_usd
        self.max_price_deviation = max_price_deviation
        self._history: list[ScanResult] = []
        self._pending_scan_records: list[dict] = []

    def scan_and_rank(self, quotes: list[MarketQuote]) -> ScanResult:
        opportunities = self._find_all_opportunities(quotes)

        scored = [(self._composite_score(opp), opp) for opp in opportunities]
        scored.sort(key=lambda x: x[0], reverse=True)

        actionable: list[Opportunity] = []
        rejected_count = 0
        for _, opp in scored:
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
        if result.best:
            self._emit_alert(result.best)
        self._history.append(result)
        return result

    @property
    def recent_history(self) -> list[ScanResult]:
        return self._history[-100:]

    def _find_all_opportunities(self, quotes: list[MarketQuote]) -> list[Opportunity]:
        if len(quotes) < 2:
            return []
        self.strategy.update_sol_price(quotes)
        medians = self._compute_pair_medians(quotes)

        results: list[Opportunity] = []
        scan_records: list[dict] = []
        skipped_same_venue = skipped_unprofitable = skipped_low_liq = skipped_price_dev = 0
        evaluated = 0

        by_pair: dict[str, list[MarketQuote]] = {}
        for q in quotes:
            by_pair.setdefault(q.pair, []).append(q)

        def _record(buy_q: MarketQuote, sell_q: MarketQuote,
                    opp: Opportunity | None, reason: str) -> None:
            passed = reason == "passed"
            if opp is not None:
                scan_records.append({
                    "pair": opp.pair,
                    "buy_venue": opp.buy_venue, "sell_venue": opp.sell_venue,
                    "buy_price": str(buy_q.buy_price), "sell_price": str(sell_q.sell_price),
                    "spread_bps": str(opp.gross_spread_pct),
                    "gross_profit": str(opp.gross_profit_quote),
                    "net_profit": str(opp.net_profit_base),
                    "fee_cost": str(opp.fee_cost_base),
                    "venue_fee_cost": str(opp.venue_fee_cost_quote),
                    "slippage_cost": str(opp.slippage_cost_quote),
                    "filter_reason": reason, "passed": passed,
                })
            else:
                mid = (buy_q.buy_price + sell_q.sell_price) / TWO
                spread = ((sell_q.sell_price - buy_q.buy_price) / buy_q.buy_price * D("100")) if buy_q.buy_price > 0 else ZERO
                scan_records.append({
                    "pair": buy_q.pair,
                    "buy_venue": buy_q.venue, "sell_venue": sell_q.venue,
                    "buy_price": str(buy_q.buy_price), "sell_price": str(sell_q.sell_price),
                    "spread_bps": str(spread),
                    "gross_profit": "0", "net_profit": "0",
                    "fee_cost": "0", "venue_fee_cost": "0", "slippage_cost": "0",
                    "filter_reason": reason, "passed": False,
                })

        for pair_quotes in by_pair.values():
            if len(pair_quotes) < 2:
                continue
            for buy_q in pair_quotes:
                for sell_q in pair_quotes:
                    if buy_q.venue == sell_q.venue:
                        skipped_same_venue += 1
                        continue
                    evaluated += 1
                    opp = self.strategy.evaluate_pair(buy_q, sell_q)
                    if opp is None:
                        skipped_unprofitable += 1
                        _record(buy_q, sell_q, None, "unprofitable")
                        continue
                    min_liq = min(buy_q.liquidity_usd, sell_q.liquidity_usd)
                    max_liq = max(buy_q.liquidity_usd, sell_q.liquidity_usd)
                    if min_liq > ZERO and min_liq < self.min_liquidity_usd:
                        skipped_low_liq += 1
                        _record(buy_q, sell_q, opp, "low_liquidity")
                        continue
                    if min_liq == ZERO and max_liq > ZERO:
                        skipped_low_liq += 1
                        _record(buy_q, sell_q, opp, "low_liquidity")
                        continue
                    if self._price_deviates(buy_q, medians):
                        skipped_price_dev += 1
                        _record(buy_q, sell_q, opp, "price_deviation")
                        continue
                    if self._price_deviates(sell_q, medians):
                        skipped_price_dev += 1
                        _record(buy_q, sell_q, opp, "price_deviation")
                        continue
                    results.append(opp)
                    _record(buy_q, sell_q, opp, "passed")

        self._pending_scan_records = scan_records
        logger.info(
            "[scanner] %d quotes → %d evaluated | unprofitable=%d low_liq=%d price_dev=%d | %d passed",
            len(quotes), evaluated,
            skipped_unprofitable, skipped_low_liq, skipped_price_dev, len(results),
        )
        return results

    def drain_scan_records(self) -> list[dict]:
        records = self._pending_scan_records
        self._pending_scan_records = []
        return records

    @staticmethod
    def _compute_pair_medians(quotes: list[MarketQuote]) -> dict[str, Decimal]:
        """Return per-pair median mid-price for cross-venue outlier detection."""
        by_pair: dict[str, list[Decimal]] = {}
        for q in quotes:
            mid = (q.buy_price + q.sell_price) / TWO
            by_pair.setdefault(q.pair, []).append(mid)
        return {p: statistics.median(mids) for p, mids in by_pair.items() if len(mids) >= 2}

    def _price_deviates(
        self, quote: MarketQuote, medians: dict[str, Decimal],
    ) -> bool:
        median = medians.get(quote.pair)
        if median is None or median == ZERO:
            return False
        mid = (quote.buy_price + quote.sell_price) / TWO
        deviation = abs(mid - median) / median
        return deviation > self.max_price_deviation

    def _composite_score(self, opp: Opportunity) -> float:
        """Multi-factor ranking.

        Weights (same as EVM heuristic — re-tune after collecting Solana data):
          0.50 profit, 0.25 liquidity, 0.15 flag-safety, 0.10 spread.
        """
        import math as _math
        net_profit = float(opp.net_profit_base)
        spread_pct = float(opp.gross_spread_pct)

        # Solana profit is in SOL — at ~$165/SOL, 0.01 SOL ≈ $1.65.  Cap at 1 SOL.
        profit_score = min(net_profit / 1.0, 1.0) if net_profit > 0 else 0.0
        liq_score = opp.liquidity_score
        flag_score = max(0.0, 1.0 - len(opp.warning_flags) * 0.25)
        spread_score = min(spread_pct / 5.0, 1.0)
        return (
            0.50 * profit_score + 0.25 * liq_score
            + 0.15 * flag_score + 0.10 * spread_score
        )

    def _passes_alert_filter(self, opp: Opportunity) -> bool:
        if opp.net_profit_base < self.alert_min_net_profit:
            return False
        if len(opp.warning_flags) > self.alert_max_warning_flags:
            return False
        return True

    def _emit_alert(self, opp: Opportunity) -> None:
        base_asset = opp.pair.split("/", 1)[0] if "/" in opp.pair else self.config.base_asset
        flags_str = ", ".join(opp.warning_flags) if opp.warning_flags else "none"
        logger.info(
            "ALERT: %s buy=%s sell=%s spread=%.2f%% net=%.8f %s liq_score=%.2f flags=[%s]",
            opp.pair, opp.buy_venue, opp.sell_venue,
            float(opp.gross_spread_pct), float(opp.net_profit_base),
            base_asset, opp.liquidity_score, flags_str,
        )
