"""Arbitrage strategy: evaluate all cross-DEX pairs and compute net profit after fees.

All financial math uses Decimal (per CLAUDE.md: "NEVER use float").
"""

from __future__ import annotations

import math
import time
from decimal import Decimal

from config import BotConfig, PairConfig
from log import get_logger
from models import BPS_DIVISOR, ONE, ZERO, MarketQuote, Opportunity

logger = get_logger(__name__)

D = Decimal


def _apply_fee(amount: Decimal, fee_bps: Decimal) -> Decimal:
    """Reduce an amount by fee_bps (used for sell-side proceeds)."""
    return amount * (ONE - fee_bps / BPS_DIVISOR)


def _add_slippage(amount: Decimal, slippage_bps: Decimal) -> Decimal:
    """Compute the slippage cost on a given amount (added to total cost)."""
    return amount * (slippage_bps / BPS_DIVISOR)


def _dynamic_slippage_bps(
    trade_size_usd: Decimal,
    liquidity_usd: Decimal,
    base_slippage_bps: Decimal,
) -> Decimal:
    """Compute slippage as a function of trade size relative to pool liquidity.

    For deep pools, slippage is close to the configured base.
    For thin pools, slippage scales up proportionally.

    Formula: slippage = base_slippage * (1 + trade_size / liquidity)

    Example:
      - $3000 trade in $50M pool → ~base_slippage (negligible extra)
      - $3000 trade in $50K pool → ~2x base_slippage (significant impact)
    """
    if liquidity_usd <= ZERO:
        # No liquidity data — fall back to base slippage.
        return base_slippage_bps
    impact_ratio = trade_size_usd / liquidity_usd
    return base_slippage_bps * (ONE + impact_ratio)


class ArbitrageStrategy:
    def __init__(self, config: BotConfig, pairs: list[PairConfig] | None = None) -> None:
        self.config = config
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

    def find_best_opportunity(self, quotes: list[MarketQuote]) -> Opportunity | None:
        """Return the most profitable cross-DEX opportunity, or None if nothing is actionable."""
        if len(quotes) < 2:
            return None

        # Log price range to surface spread visibility.
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
                "[strategy] %s: %d quotes, cheapest=%s(%.2f) dearest=%s(%.2f) raw_spread=%.1f bps",
                pair_name, len(pq), low.dex, low.buy_price,
                high.dex, high.buy_price, spread_bps,
            )

        best_opportunity: Opportunity | None = None
        for buy_quote in quotes:
            for sell_quote in quotes:
                if buy_quote.dex == sell_quote.dex:
                    continue
                # Only compare quotes for the same trading pair.
                if buy_quote.pair != sell_quote.pair:
                    continue
                candidate = self.evaluate_pair(buy_quote, sell_quote)
                if candidate is None:
                    continue
                if (
                    best_opportunity is None
                    or candidate.net_profit_base > best_opportunity.net_profit_base
                ):
                    best_opportunity = candidate
        return best_opportunity

    def evaluate_pair(
        self, buy_quote: MarketQuote, sell_quote: MarketQuote
    ) -> Opportunity | None:
        """Evaluate a buy-on-A / sell-on-B pair and return an Opportunity if profitable.

        Cost model (all values in quote asset, e.g. USDC):
          1. buy_cost_with_fee   — cost to acquire trade_size base asset, grossed up for DEX fee
          2. sell_proceeds_after_fee — proceeds from selling, reduced by DEX fee
          3. slippage_cost       — estimated market impact
          4. flash_fee           — flash loan provider fee (e.g. Aave 9 bps)
          5. gas_cost            — estimated on-chain gas, subtracted in base asset units

        When ``fee_included`` is True on a quote, the price already reflects
        the pool fee (on-chain quoters return post-fee output).  In that case
        we skip the fee adjustment to avoid double-counting — fee_bps is
        carried for display only.
        """
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

        # Buy side: if fees are already included in the quoted price, the
        # cost IS buy_cost_quote.  Otherwise gross up for the DEX fee.
        if buy_quote.fee_included:
            buy_cost_with_fee = buy_cost_quote
        else:
            buy_cost_with_fee = buy_cost_quote / (ONE - buy_quote.fee_bps / BPS_DIVISOR)

        # Sell side: if fees are already included, proceeds are final.
        if sell_quote.fee_included:
            sell_proceeds_after_fee = sell_proceeds_quote
        else:
            sell_proceeds_after_fee = _apply_fee(sell_proceeds_quote, sell_quote.fee_bps)

        # Use liquidity-aware slippage when pool data is available.
        # We use min(buy, sell) liquidity as the bottleneck — the thinnest pool
        # determines the worst-case slippage for the entire trade.
        min_liq = min(buy_quote.liquidity_usd, sell_quote.liquidity_usd)
        if min_liq > ZERO:
            effective_slippage_bps = _dynamic_slippage_bps(
                buy_cost_quote, min_liq, self.config.slippage_bps,
            )
        else:
            effective_slippage_bps = self.config.slippage_bps
        slippage_cost_quote = _add_slippage(buy_cost_quote, effective_slippage_bps)
        flash_fee_quote = buy_cost_quote * (self.config.flash_loan_fee_bps / BPS_DIVISOR)

        gross_profit_quote = sell_proceeds_quote - buy_cost_quote
        net_profit_quote = (
            sell_proceeds_after_fee
            - buy_cost_with_fee
            - slippage_cost_quote
            - flash_fee_quote
        )

        # Convert quote-denominated profit to base asset using the average of the
        # two prices as a rough conversion rate, then subtract gas cost in base.
        # NOTE: Mid-price averaging is a simplification. In production with large
        # trades, use the actual execution price from the quoter instead.
        mid_price = (buy_quote.buy_price + sell_quote.sell_price) / D("2")
        net_profit_base = (net_profit_quote / mid_price) - self.config.estimated_gas_cost_base

        is_actionable = net_profit_base > self.config.min_profit_base
        if not is_actionable:
            logger.debug(
                "[strategy] %s buy@%s(%.2f) sell@%s(%.2f) → "
                "gross=%.6f net_base=%.6f < min_profit=%.6f SKIP",
                buy_quote.pair, buy_quote.dex, buy_quote.buy_price,
                sell_quote.dex, sell_quote.sell_price,
                gross_profit_quote, net_profit_base,
                self.config.min_profit_base,
            )
            return None

        # Gross spread as a percentage of the buy price.
        HUNDRED = D("100")
        gross_spread_pct = (
            (sell_quote.sell_price - buy_quote.buy_price) / buy_quote.buy_price * HUNDRED
            if buy_quote.buy_price > ZERO else ZERO
        )
        # Total DEX fee cost.  When fees are pre-included, estimate from
        # fee_bps for display (the actual deduction is already in the price).
        if buy_quote.fee_included or sell_quote.fee_included:
            buy_fee_est = buy_cost_quote * (buy_quote.fee_bps / BPS_DIVISOR) if buy_quote.fee_included else (buy_cost_with_fee - buy_cost_quote)
            sell_fee_est = sell_proceeds_quote * (sell_quote.fee_bps / BPS_DIVISOR) if sell_quote.fee_included else (sell_proceeds_quote - sell_proceeds_after_fee)
            dex_fee_cost_quote = buy_fee_est + sell_fee_est
        else:
            dex_fee_cost_quote = (buy_cost_with_fee - buy_cost_quote) + (sell_proceeds_quote - sell_proceeds_after_fee)

        # --- Risk flag assessment (per scanner doc warning flags) ---
        # Thresholds are empirically derived from DeFi market conditions:
        #   $100K = pools below this have frequent large price impact
        #   $50K  = 24h volume below this means the pair is barely traded
        #   60s   = quotes older than 1 minute may not reflect current state
        #   80%   = if fees eat >80% of the gross spread, the edge is too thin
        flags: list[str] = []

        min_liq = min(buy_quote.liquidity_usd, sell_quote.liquidity_usd)
        min_vol = min(buy_quote.volume_usd, sell_quote.volume_usd)

        if min_liq > ZERO and min_liq < D("100000"):
            flags.append("low_liquidity")
        if min_vol > ZERO and min_vol < D("50000"):
            flags.append("thin_market")

        now = time.time()
        for q in (buy_quote, sell_quote):
            if q.quote_timestamp > 0 and (now - q.quote_timestamp) > 60:
                flags.append("stale_quote")
                break

        total_fee_pct = (dex_fee_cost_quote + flash_fee_quote + slippage_cost_quote) / buy_cost_quote * HUNDRED if buy_cost_quote > ZERO else ZERO
        if total_fee_pct > ZERO and gross_spread_pct > ZERO and total_fee_pct / gross_spread_pct > D("0.8"):
            flags.append("high_fee_ratio")

        # Liquidity score: 0.0 (illiquid) to 1.0 (highly liquid).
        # Log10 scaling maps the typical DeFi liquidity range to [0, 1]:
        #   $10M+ → 1.0, $1M → ~0.86, $100K → ~0.71, $10K → ~0.57
        # Divisor 7.0 = log10(10,000,000) — pools with $10M+ TVL saturate at 1.0.
        # This is a ranking metric (not financial), so float is acceptable.
        if min_liq > ZERO:
            liquidity_score = min(1.0, math.log10(max(float(min_liq), 1)) / 7.0)
        else:
            # No liquidity data available — default to 1.0 (no penalty).
            liquidity_score = 1.0

        # Resolve chain from DEX config.
        chain = ""
        for dex_cfg in self.config.dexes:
            if dex_cfg.name == buy_quote.dex and dex_cfg.chain:
                chain = dex_cfg.chain
                break

        return Opportunity(
            pair=buy_quote.pair,
            buy_dex=buy_quote.dex,
            sell_dex=sell_quote.dex,
            trade_size=trade_size,
            cost_to_buy_quote=buy_cost_with_fee,
            proceeds_from_sell_quote=sell_proceeds_after_fee,
            gross_profit_quote=gross_profit_quote,
            net_profit_quote=net_profit_quote,
            net_profit_base=net_profit_base,
            gross_spread_pct=gross_spread_pct,
            dex_fee_cost_quote=dex_fee_cost_quote,
            flash_loan_fee_quote=flash_fee_quote,
            slippage_cost_quote=slippage_cost_quote,
            gas_cost_base=self.config.estimated_gas_cost_base,
            is_actionable=is_actionable,
            warning_flags=tuple(flags),
            liquidity_score=liquidity_score,
            chain=chain,
            fees_pre_included=buy_quote.fee_included or sell_quote.fee_included,
            buy_liquidity_usd=buy_quote.liquidity_usd,
            sell_liquidity_usd=sell_quote.liquidity_usd,
        )
