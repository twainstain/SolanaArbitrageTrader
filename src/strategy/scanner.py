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

from core.config import BotConfig, PairConfig
from observability.log import get_logger
from core.models import ZERO, MarketQuote, Opportunity

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
        # Scan history records collected during _find_all_opportunities.
        # Flushed to DB async by the caller after the scan cycle.
        self._pending_scan_records: list[dict] = []

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

        # Update reference WETH price so non-WETH pairs (OP/USDC, AERO/WETH)
        # can normalise net_profit_base to ETH.
        self.strategy.update_weth_price(quotes)

        # Pre-compute per-chain medians for same-chain price consistency check.
        _chain_medians = self._compute_chain_medians(quotes)
        # Pre-compute Decimal constants used in the inner loop to avoid
        # repeated object creation (~0.5μs each × 200 iterations = ~0.1ms).
        _MAX_DEV = D("0.02")
        # Pre-compute per-chain liquidity thresholds: $1M for Ethereum,
        # $100K for L2s (legitimate L2 pools are smaller).
        from core.config import BotConfig as _BC
        _chain_min_liq: dict[str, Decimal] = {}
        for dex_cfg in self.config.dexes:
            ch = dex_cfg.chain or ""
            if ch and ch not in _chain_min_liq:
                _chain_min_liq[ch] = _BC.min_liquidity_for_chain(ch)

        results: list[Opportunity] = []
        scan_records: list[dict] = []
        skipped_same_dex = 0
        skipped_diff_pair = 0
        skipped_unprofitable = 0
        skipped_cross_chain = 0
        skipped_low_liq = 0
        skipped_price_deviation = 0
        evaluated = 0

        # Helper to resolve chain from DEX name.
        _dex_chain: dict[str, str] = {}
        for dex_cfg in self.config.dexes:
            _dex_chain[dex_cfg.name] = dex_cfg.chain or ""

        def _record(buy_q: MarketQuote, sell_q: MarketQuote,
                     opp: Opportunity | None, reason: str) -> None:
            chain = _dex_chain.get(buy_q.dex, "")
            if opp is not None:
                scan_records.append({
                    "pair": opp.pair, "chain": opp.chain or chain,
                    "buy_dex": opp.buy_dex, "sell_dex": opp.sell_dex,
                    "buy_price": str(buy_q.buy_price), "sell_price": str(sell_q.sell_price),
                    "spread_bps": str(opp.gross_spread_pct),
                    "gross_profit": str(opp.gross_profit_quote),
                    "net_profit": str(opp.net_profit_base),
                    "gas_cost": str(opp.gas_cost_base),
                    "fee_cost": str(opp.dex_fee_cost_quote),
                    "slippage_cost": str(opp.slippage_cost_quote),
                    "filter_reason": reason, "passed": reason == "passed",
                })
            else:
                # Unprofitable — compute basic spread from quotes.
                mid = (buy_q.buy_price + sell_q.sell_price) / D("2")
                spread = ((sell_q.sell_price - buy_q.buy_price) / buy_q.buy_price * D("100")) if buy_q.buy_price > 0 else ZERO
                gas = self.config.gas_cost_for_chain(chain)
                scan_records.append({
                    "pair": buy_q.pair, "chain": chain,
                    "buy_dex": buy_q.dex, "sell_dex": sell_q.dex,
                    "buy_price": str(buy_q.buy_price), "sell_price": str(sell_q.sell_price),
                    "spread_bps": str(spread),
                    "gross_profit": "0", "net_profit": "0",
                    "gas_cost": str(gas), "fee_cost": "0", "slippage_cost": "0",
                    "filter_reason": reason, "passed": False,
                })

        # Pre-group quotes by pair to eliminate O(n^2) cross-pair comparisons.
        by_pair: dict[str, list[MarketQuote]] = {}
        for q in quotes:
            by_pair.setdefault(q.pair, []).append(q)

        for pair_quotes in by_pair.values():
            if len(pair_quotes) < 2:
                continue
            for buy_quote in pair_quotes:
                for sell_quote in pair_quotes:
                    if buy_quote.dex == sell_quote.dex:
                        skipped_same_dex += 1
                        continue
                    evaluated += 1
                    opp = self.strategy.evaluate_pair(buy_quote, sell_quote)
                    if opp is None:
                        skipped_unprofitable += 1
                        _record(buy_quote, sell_quote, None, "unprofitable")
                        continue
                    if opp.is_cross_chain:
                        skipped_cross_chain += 1
                        _record(buy_quote, sell_quote, opp, "cross_chain")
                        continue
                    buy_liq = buy_quote.liquidity_usd
                    sell_liq = sell_quote.liquidity_usd
                    min_liq = min(buy_liq, sell_liq)
                    max_liq = max(buy_liq, sell_liq)
                    opp_chain = _dex_chain.get(buy_quote.dex, "")
                    _min_liq_threshold = _chain_min_liq.get(opp_chain, D("1000000"))
                    if min_liq > ZERO and min_liq < _min_liq_threshold:
                        skipped_low_liq += 1
                        _record(buy_quote, sell_quote, opp, "low_liquidity")
                        continue
                    if min_liq == ZERO and max_liq > ZERO:
                        skipped_low_liq += 1
                        _record(buy_quote, sell_quote, opp, "low_liquidity")
                        continue
                    if self._price_deviates_from_chain(buy_quote, _chain_medians, _MAX_DEV):
                        skipped_price_deviation += 1
                        _record(buy_quote, sell_quote, opp, "price_deviation")
                        logger.info(
                            "Price deviation: %s on %s deviates from chain median",
                            buy_quote.pair, buy_quote.dex,
                        )
                        continue
                    if self._price_deviates_from_chain(sell_quote, _chain_medians, _MAX_DEV):
                        skipped_price_deviation += 1
                        _record(buy_quote, sell_quote, opp, "price_deviation")
                        logger.info(
                            "Price deviation: %s on %s deviates from chain median",
                            sell_quote.pair, sell_quote.dex,
                        )
                        continue
                    results.append(opp)
                    _record(buy_quote, sell_quote, opp, "passed")

        # Store scan records for async flush (does NOT block the pipeline).
        self._pending_scan_records = scan_records

        logger.info(
            "[scanner] %d quotes → %d pairs evaluated | "
            "unprofitable=%d cross_chain=%d low_liq=%d price_dev=%d | %d passed",
            len(quotes), evaluated,
            skipped_unprofitable, skipped_cross_chain, skipped_low_liq,
            skipped_price_deviation, len(results),
        )
        return results

    def drain_scan_records(self) -> list[dict]:
        """Return and clear pending scan records for async DB flush."""
        records = self._pending_scan_records
        self._pending_scan_records = []
        return records

    @staticmethod
    def _compute_chain_medians(quotes: list[MarketQuote]) -> dict[str, Decimal]:
        """Compute median mid-price per (pair, chain) for consistency checks.

        Returns a dict keyed by "pair:chain" → median mid-price.
        Chain is extracted from the DEX name suffix (e.g. "Uniswap-Arbitrum" → "arbitrum").
        """
        import statistics
        TWO = D("2")
        by_pair_chain: dict[str, list[Decimal]] = {}
        for q in quotes:
            parts = q.dex.rsplit("-", 1)
            chain = parts[1].lower() if len(parts) == 2 else ""
            if not chain:
                continue
            key = f"{q.pair}:{chain}"
            mid = (q.buy_price + q.sell_price) / TWO
            by_pair_chain.setdefault(key, []).append(mid)

        medians: dict[str, Decimal] = {}
        for key, mids in by_pair_chain.items():
            if len(mids) >= 2:
                medians[key] = statistics.median(mids)
        return medians

    @staticmethod
    def _price_deviates_from_chain(
        quote: MarketQuote,
        chain_medians: dict[str, Decimal],
        max_deviation: Decimal,
    ) -> bool:
        """Return True if a quote's price deviates from its chain median.

        Only triggers when there are 2+ quotes for the same pair on the
        same chain — ensures we have a reliable baseline to compare against.
        """
        TWO = D("2")
        parts = quote.dex.rsplit("-", 1)
        chain = parts[1].lower() if len(parts) == 2 else ""
        if not chain:
            return False
        key = f"{quote.pair}:{chain}"
        median = chain_medians.get(key)
        if median is None or median == ZERO:
            return False
        mid = (quote.buy_price + quote.sell_price) / TWO
        deviation = abs(mid - median) / median
        return deviation > max_deviation

    def _composite_score(self, opp: Opportunity) -> float:
        """Compute a multi-factor ranking score for opportunity prioritization.

        Weights were set based on initial production observations (not ML-tuned):
          - 0.50 net profit: primary signal — profit is the objective function
          - 0.25 liquidity:  guards against thin-pool false positives; pools with
            $10M+ TVL get full score, $100K gets ~0.71 (see strategy.py log10 scaling)
          - 0.15 flag safety: each warning flag (stale_quote, low_liquidity, etc.)
            reduces this component by 0.25.  4+ flags = zero.  This is aggressive
            because multiple flags compound risk in ways a weighted average can't capture.
          - 0.10 spread: tie-breaker only — wider spread = more room for execution
            slippage before the trade becomes unprofitable

        Normalization caps prevent outliers from dominating the ranking:
          - Profit capped at 1.0 WETH (~$2300): a $10K profit opportunity is
            ranked the same as $2300 — both are "very profitable", the ranking
            should prioritize execution reliability over extreme profit
          - Spread capped at 5%: above this is almost certainly a data error
            or an illiquid pool that would get filtered anyway

        These weights should be re-tuned after collecting 1000+ real trades.

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
