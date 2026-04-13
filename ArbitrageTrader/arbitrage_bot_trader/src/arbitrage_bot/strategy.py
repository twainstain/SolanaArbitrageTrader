"""Arbitrage strategy: evaluate all cross-DEX pairs and compute net profit after fees."""

from __future__ import annotations

from arbitrage_bot.config import BotConfig
from arbitrage_bot.models import MarketQuote, Opportunity


def _apply_fee(amount: float, fee_bps: float) -> float:
    """Reduce an amount by fee_bps (used for sell-side proceeds)."""
    return amount * (1 - fee_bps / 10_000.0)


def _add_slippage(amount: float, slippage_bps: float) -> float:
    """Compute the slippage cost on a given amount (added to total cost)."""
    return amount * (slippage_bps / 10_000.0)


class ArbitrageStrategy:
    def __init__(self, config: BotConfig) -> None:
        self.config = config

    def find_best_opportunity(self, quotes: list[MarketQuote]) -> Opportunity | None:
        """Return the most profitable cross-DEX opportunity, or None if nothing is actionable."""
        if len(quotes) < 2:
            return None

        best_opportunity: Opportunity | None = None
        for buy_quote in quotes:
            for sell_quote in quotes:
                if buy_quote.dex == sell_quote.dex:
                    continue
                # Only compare quotes for the same trading pair.
                if buy_quote.pair != sell_quote.pair:
                    continue
                candidate = self._evaluate_pair(buy_quote, sell_quote)
                if candidate is None:
                    continue
                if (
                    best_opportunity is None
                    or candidate.net_profit_base > best_opportunity.net_profit_base
                ):
                    best_opportunity = candidate
        return best_opportunity

    def _evaluate_pair(
        self, buy_quote: MarketQuote, sell_quote: MarketQuote
    ) -> Opportunity | None:
        """Evaluate a buy-on-A / sell-on-B pair and return an Opportunity if profitable.

        Cost model (all values in quote asset, e.g. USDC):
          1. buy_cost_with_fee   — cost to acquire trade_size base asset, grossed up for DEX fee
          2. sell_proceeds_after_fee — proceeds from selling, reduced by DEX fee
          3. slippage_cost       — estimated market impact
          4. flash_fee           — flash loan provider fee (e.g. Aave 9 bps)
          5. gas_cost            — estimated on-chain gas, subtracted in base asset units
        """
        trade_size = self.config.trade_size
        buy_cost_quote = trade_size * buy_quote.buy_price
        sell_proceeds_quote = trade_size * sell_quote.sell_price

        # Buy side: divide by (1 - fee) to get the total amount owed including the
        # DEX fee.  This is the inverse of _apply_fee — we need *more* quote to end
        # up with the same base amount after the DEX takes its cut.
        buy_cost_with_fee = buy_cost_quote / (1 - buy_quote.fee_bps / 10_000.0)
        # Sell side: straightforward reduction — DEX keeps fee_bps of proceeds.
        sell_proceeds_after_fee = _apply_fee(sell_proceeds_quote, sell_quote.fee_bps)

        slippage_cost_quote = _add_slippage(buy_cost_quote, self.config.slippage_bps)
        flash_fee_quote = buy_cost_quote * (self.config.flash_loan_fee_bps / 10_000.0)

        gross_profit_quote = sell_proceeds_quote - buy_cost_quote
        net_profit_quote = (
            sell_proceeds_after_fee
            - buy_cost_with_fee
            - slippage_cost_quote
            - flash_fee_quote
        )

        # Convert quote-denominated profit to base asset using the average of the
        # two prices as a rough conversion rate, then subtract gas cost in base.
        mid_price = (buy_quote.buy_price + sell_quote.sell_price) / 2
        net_profit_base = (net_profit_quote / mid_price) - self.config.estimated_gas_cost_base

        is_actionable = net_profit_base > self.config.min_profit_base
        if not is_actionable:
            return None

        # Gross spread as a percentage of the buy price.
        gross_spread_pct = (
            (sell_quote.sell_price - buy_quote.buy_price) / buy_quote.buy_price * 100
            if buy_quote.buy_price > 0 else 0.0
        )
        # Total DEX fee cost = buy fee markup + sell fee reduction.
        dex_fee_cost_quote = (buy_cost_with_fee - buy_cost_quote) + (sell_proceeds_quote - sell_proceeds_after_fee)

        # --- Risk flag assessment (per scanner doc warning flags) ---
        import time
        flags: list[str] = []

        min_liq = min(buy_quote.liquidity_usd, sell_quote.liquidity_usd)
        min_vol = min(buy_quote.volume_usd, sell_quote.volume_usd)

        if min_liq > 0 and min_liq < 100_000:
            flags.append("low_liquidity")
        if min_vol > 0 and min_vol < 50_000:
            flags.append("thin_market")

        now = time.time()
        for q in (buy_quote, sell_quote):
            if q.quote_timestamp > 0 and (now - q.quote_timestamp) > 60:
                flags.append("stale_quote")
                break

        total_fee_pct = (dex_fee_cost_quote + flash_fee_quote + slippage_cost_quote) / buy_cost_quote * 100 if buy_cost_quote > 0 else 0
        if total_fee_pct > 0 and gross_spread_pct > 0 and total_fee_pct / gross_spread_pct > 0.8:
            flags.append("high_fee_ratio")

        # Liquidity score: 0.0 (illiquid) to 1.0 (highly liquid).
        # Based on the lower of buy/sell venue liquidity, scaled logarithmically.
        if min_liq > 0:
            import math
            # $10M+ → 1.0, $100K → ~0.5, $10K → ~0.25
            liquidity_score = min(1.0, math.log10(max(min_liq, 1)) / 7.0)
        else:
            # No liquidity data available — default to 1.0 (no penalty).
            liquidity_score = 1.0

        return Opportunity(
            pair=self.config.pair,
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
        )
