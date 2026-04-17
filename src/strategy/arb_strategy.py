"""Arbitrage strategy — compute net profit across Solana venues.

All financial math uses ``Decimal``.  The model is intentionally simpler
than the EVM version: Solana is single-chain, there is no flash-loan
provider fee, and execution cost is a flat priority fee (in lamports →
SOL) plus optional Jito tip.

For non-SOL base pairs (e.g. USDC/USDT) profit is normalised to SOL using
a reference SOL/USDC mid-price from the current scan cycle, so the
dashboard always displays profit in a single unit.
"""

from __future__ import annotations

import math
import time
from decimal import Decimal

from core.config import BotConfig, PairConfig
from observability.log import get_logger
from core.models import BPS_DIVISOR, ONE, ZERO, MarketQuote, Opportunity

logger = get_logger(__name__)

D = Decimal
HUNDRED = D("100")


def _apply_fee(amount: Decimal, fee_bps: Decimal) -> Decimal:
    return amount * (ONE - fee_bps / BPS_DIVISOR)


def _add_slippage(amount: Decimal, slippage_bps: Decimal) -> Decimal:
    return amount * (slippage_bps / BPS_DIVISOR)


def _dynamic_slippage_bps(
    trade_size_usd: Decimal,
    liquidity_usd: Decimal,
    base_slippage_bps: Decimal,
) -> Decimal:
    if liquidity_usd <= ZERO:
        return base_slippage_bps
    impact_ratio = trade_size_usd / liquidity_usd
    return base_slippage_bps * (ONE + impact_ratio)


class ArbitrageStrategy:
    def __init__(self, config: BotConfig, pairs: list[PairConfig] | None = None) -> None:
        self.config = config
        # Reference SOL price (in USD) for normalising USDC/USDT pairs to SOL.
        self._sol_price_usd: Decimal = ZERO
        self._pair_configs: dict[str, PairConfig] = {
            config.pair: PairConfig(
                pair=config.pair,
                base_asset=config.base_asset,
                quote_asset=config.quote_asset,
                trade_size=config.trade_size,
            )
        }
        for pair_cfg in pairs or config.extra_pairs or []:
            self._pair_configs[pair_cfg.pair] = pair_cfg

    @staticmethod
    def _is_sol_base(symbol: str) -> bool:
        return symbol.upper() in ("SOL", "WSOL")

    def update_sol_price(self, quotes: list[MarketQuote]) -> None:
        """Update the reference SOL price from SOL/USDC or SOL/USDT quotes."""
        prices: list[Decimal] = []
        for q in quotes:
            if q.pair in ("SOL/USDC", "SOL/USDT", "WSOL/USDC") and q.buy_price > ZERO:
                prices.append(q.buy_price)
        if prices:
            prices.sort()
            self._sol_price_usd = prices[len(prices) // 2]

    def find_best_opportunity(self, quotes: list[MarketQuote]) -> Opportunity | None:
        """Return the most profitable cross-venue opportunity, or None."""
        if len(quotes) < 2:
            return None
        self.update_sol_price(quotes)

        by_pair: dict[str, list[MarketQuote]] = {}
        for q in quotes:
            by_pair.setdefault(q.pair, []).append(q)
        for pair_name, pq in by_pair.items():
            prices = sorted(pq, key=lambda q: q.buy_price)
            low, high = prices[0], prices[-1]
            spread_bps = (
                (high.sell_price - low.buy_price) / low.buy_price * D("10000")
                if low.buy_price > ZERO else ZERO
            )
            logger.info(
                "[strategy] %s: %d quotes, cheapest=%s(%.4f) dearest=%s(%.4f) raw_spread=%.1f bps",
                pair_name, len(pq), low.venue, low.buy_price,
                high.venue, high.buy_price, spread_bps,
            )

        best: Opportunity | None = None
        for buy_quote in quotes:
            for sell_quote in quotes:
                if buy_quote.venue == sell_quote.venue:
                    continue
                if buy_quote.pair != sell_quote.pair:
                    continue
                candidate = self.evaluate_pair(buy_quote, sell_quote)
                if candidate is None:
                    continue
                if best is None or candidate.net_profit_base > best.net_profit_base:
                    best = candidate
        return best

    def evaluate_pair(
        self, buy_quote: MarketQuote, sell_quote: MarketQuote,
    ) -> Opportunity | None:
        """Evaluate a buy-on-A / sell-on-B pair and return an Opportunity if profitable."""
        pair_cfg = self._pair_configs.get(
            buy_quote.pair,
            PairConfig(
                pair=buy_quote.pair,
                base_asset=self.config.base_asset,
                quote_asset=self.config.quote_asset,
                trade_size=self.config.trade_size,
            ),
        )
        trade_size = pair_cfg.trade_size
        buy_cost_quote = trade_size * buy_quote.buy_price
        sell_proceeds_quote = trade_size * sell_quote.sell_price

        # --- Fee handling (critical: prevents double-counting) ---
        # Jupiter's outAmount is already post-fee → fee_included=True.
        # Direct pool adapters (Phase 2) may not include fees → fee_included=False.
        if buy_quote.fee_included:
            buy_cost_with_fee = buy_cost_quote
        else:
            buy_cost_with_fee = buy_cost_quote / (ONE - buy_quote.fee_bps / BPS_DIVISOR)

        if sell_quote.fee_included:
            sell_proceeds_after_fee = sell_proceeds_quote
        else:
            sell_proceeds_after_fee = _apply_fee(sell_proceeds_quote, sell_quote.fee_bps)

        # Liquidity-aware slippage.
        min_liq = min(buy_quote.liquidity_usd, sell_quote.liquidity_usd)
        if min_liq > ZERO:
            effective_slippage_bps = _dynamic_slippage_bps(
                buy_cost_quote, min_liq, self.config.slippage_bps,
            )
        else:
            effective_slippage_bps = self.config.slippage_bps
        slippage_cost_quote = _add_slippage(buy_cost_quote, effective_slippage_bps)

        gross_profit_quote = sell_proceeds_quote - buy_cost_quote
        net_profit_quote = (
            sell_proceeds_after_fee
            - buy_cost_with_fee
            - slippage_cost_quote
        )

        # Execution cost: priority fee in SOL.
        fee_cost_base = self.config.priority_fee_sol()

        # Convert quote-denominated profit to SOL.
        mid_price = (buy_quote.buy_price + sell_quote.sell_price) / D("2")

        if pair_cfg.quote_asset.upper() in ("SOL", "WSOL"):
            # Quote already SOL — profit is directly in SOL.
            net_profit_base = net_profit_quote - fee_cost_base
        elif self._is_sol_base(pair_cfg.base_asset):
            # SOL/USDC, SOL/USDT — divide by mid_price (≈ SOL price).
            net_profit_base = (net_profit_quote / mid_price) - fee_cost_base
        elif self._sol_price_usd > ZERO:
            # Non-SOL base (USDC/USDT) — normalise to SOL using reference price.
            net_profit_base = (net_profit_quote / self._sol_price_usd) - fee_cost_base
        else:
            logger.warning(
                "[strategy] No SOL reference price for %s — profit may be inaccurate",
                buy_quote.pair,
            )
            net_profit_base = (net_profit_quote / mid_price) - fee_cost_base

        is_actionable = net_profit_base > self.config.min_profit_base
        if not is_actionable:
            logger.debug(
                "[strategy] %s buy@%s(%.4f) sell@%s(%.4f) → net=%.8f < min=%.8f SKIP",
                buy_quote.pair, buy_quote.venue, buy_quote.buy_price,
                sell_quote.venue, sell_quote.sell_price,
                net_profit_base, self.config.min_profit_base,
            )
            return None

        gross_spread_pct = (
            (sell_quote.sell_price - buy_quote.buy_price) / buy_quote.buy_price * HUNDRED
            if buy_quote.buy_price > ZERO else ZERO
        )

        # Venue fee cost (display).  With Jupiter these are effectively zero
        # since fees are inside the quoted output; we still compute a
        # transparent estimate for logging.
        if buy_quote.fee_included or sell_quote.fee_included:
            buy_fee_est = (
                buy_cost_quote * (buy_quote.fee_bps / BPS_DIVISOR)
                if buy_quote.fee_included else (buy_cost_with_fee - buy_cost_quote)
            )
            sell_fee_est = (
                sell_proceeds_quote * (sell_quote.fee_bps / BPS_DIVISOR)
                if sell_quote.fee_included else (sell_proceeds_quote - sell_proceeds_after_fee)
            )
            venue_fee_cost_quote = buy_fee_est + sell_fee_est
        else:
            venue_fee_cost_quote = (buy_cost_with_fee - buy_cost_quote) + (sell_proceeds_quote - sell_proceeds_after_fee)

        # --- Risk flag assessment ---
        # Thresholds sized for Solana (tighter staleness: slots ~400ms).
        flags: list[str] = []

        min_vol = min(buy_quote.volume_usd, sell_quote.volume_usd)
        if min_liq > ZERO and min_liq < D("100000"):
            flags.append("low_liquidity")
        if min_vol > ZERO and min_vol < D("50000"):
            flags.append("thin_market")

        now = time.time()
        for q in (buy_quote, sell_quote):
            if q.quote_timestamp > 0 and (now - q.quote_timestamp) > 10:
                flags.append("stale_quote")
                break

        total_fee_pct = (
            (venue_fee_cost_quote + slippage_cost_quote) / buy_cost_quote * HUNDRED
            if buy_cost_quote > ZERO else ZERO
        )
        if total_fee_pct > ZERO and gross_spread_pct > ZERO and total_fee_pct / gross_spread_pct > D("0.8"):
            flags.append("high_fee_ratio")

        # Liquidity score (0.0–1.0, log10-scaled).  Ranking metric — float ok.
        if min_liq > ZERO:
            liquidity_score = min(1.0, math.log10(max(float(min_liq), 1)) / 7.0)
        else:
            liquidity_score = 1.0

        return Opportunity(
            pair=buy_quote.pair,
            buy_venue=buy_quote.venue,
            sell_venue=sell_quote.venue,
            trade_size=trade_size,
            cost_to_buy_quote=buy_cost_with_fee,
            proceeds_from_sell_quote=sell_proceeds_after_fee,
            gross_profit_quote=gross_profit_quote,
            net_profit_quote=net_profit_quote,
            net_profit_base=net_profit_base,
            gross_spread_pct=gross_spread_pct,
            venue_fee_cost_quote=venue_fee_cost_quote,
            slippage_cost_quote=slippage_cost_quote,
            fee_cost_base=fee_cost_base,
            is_actionable=is_actionable,
            warning_flags=tuple(flags),
            liquidity_score=liquidity_score,
            fees_pre_included=buy_quote.fee_included or sell_quote.fee_included,
            buy_liquidity_usd=buy_quote.liquidity_usd,
            sell_liquidity_usd=sell_quote.liquidity_usd,
            max_exposure_override=pair_cfg.max_exposure or ZERO,
        )
