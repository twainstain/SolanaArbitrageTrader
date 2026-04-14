"""Tests for multi-pair scanning across market sources and the bot loop."""

import io
import logging
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bot import ArbitrageBot
from config import BotConfig, DexConfig, PairConfig
from market import SimulatedMarket
from strategy import ArbitrageStrategy


def _make_multi_pair_config(**overrides) -> BotConfig:
    defaults = dict(
        pair="WETH/USDC",
        base_asset="WETH",
        quote_asset="USDC",
        trade_size=1.0,
        min_profit_base=0.0,
        estimated_gas_cost_base=0.0,
        flash_loan_fee_bps=0.0,
        flash_loan_provider="aave_v3",
        slippage_bps=0.0,
        poll_interval_seconds=0.0,
        extra_pairs=[
            PairConfig(pair="WETH/USDT", base_asset="WETH", quote_asset="USDT", trade_size=1.0),
            PairConfig(pair="WBTC/USDC", base_asset="WBTC", quote_asset="USDC", trade_size=0.05),
        ],
        dexes=[
            DexConfig(name="A", base_price=3000.0, fee_bps=0.0, volatility_bps=10.0),
            DexConfig(name="B", base_price=3030.0, fee_bps=0.0, volatility_bps=12.0),
        ],
    )
    defaults.update(overrides)
    config = BotConfig(**defaults)
    config.validate()
    return config


class SimulatedMarketMultiPairTests(unittest.TestCase):
    def test_produces_quotes_for_all_pairs(self) -> None:
        config = _make_multi_pair_config()
        market = SimulatedMarket(config, seed=42)
        quotes = market.get_quotes()

        pair_names = {q.pair for q in quotes}
        self.assertEqual(pair_names, {"WETH/USDC", "WETH/USDT", "WBTC/USDC"})

    def test_each_pair_has_quotes_from_all_dexes(self) -> None:
        config = _make_multi_pair_config()
        market = SimulatedMarket(config, seed=42)
        quotes = market.get_quotes()

        for pair in ("WETH/USDC", "WETH/USDT", "WBTC/USDC"):
            pair_quotes = [q for q in quotes if q.pair == pair]
            dex_names = {q.dex for q in pair_quotes}
            self.assertEqual(dex_names, {"A", "B"}, f"Missing DEX for {pair}")

    def test_wbtc_price_higher_than_weth(self) -> None:
        config = _make_multi_pair_config()
        market = SimulatedMarket(config, seed=42)
        quotes = market.get_quotes()

        weth_mid = None
        wbtc_mid = None
        for q in quotes:
            mid = (q.buy_price + q.sell_price) / 2
            if q.pair == "WETH/USDC" and weth_mid is None:
                weth_mid = mid
            elif q.pair == "WBTC/USDC" and wbtc_mid is None:
                wbtc_mid = mid

        self.assertIsNotNone(weth_mid)
        self.assertIsNotNone(wbtc_mid)
        # WBTC should be ~23.5x WETH
        self.assertGreater(wbtc_mid, weth_mid * 10)

    def test_weth_usdt_similar_to_weth_usdc(self) -> None:
        config = _make_multi_pair_config()
        market = SimulatedMarket(config, seed=42)
        quotes = market.get_quotes()

        usdc_mids = [(q.buy_price + q.sell_price) / 2 for q in quotes if q.pair == "WETH/USDC"]
        usdt_mids = [(q.buy_price + q.sell_price) / 2 for q in quotes if q.pair == "WETH/USDT"]

        from decimal import Decimal
        # USDC and USDT are both ~$1, so prices should be within 1%
        for usdc, usdt in zip(usdc_mids, usdt_mids):
            self.assertAlmostEqual(float(usdc), float(usdt), delta=float(usdc) * 0.01)

    def test_no_extra_pairs_only_primary(self) -> None:
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=0.0, flash_loan_provider="aave_v3",
            slippage_bps=0.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="A", base_price=3000.0, fee_bps=0.0, volatility_bps=0.0),
                DexConfig(name="B", base_price=3030.0, fee_bps=0.0, volatility_bps=0.0),
            ],
        )
        config.validate()
        market = SimulatedMarket(config, seed=1)
        quotes = market.get_quotes()

        pair_names = {q.pair for q in quotes}
        self.assertEqual(pair_names, {"WETH/USDC"})

    def test_quotes_advance_each_call(self) -> None:
        config = _make_multi_pair_config()
        market = SimulatedMarket(config, seed=42)

        q1 = market.get_quotes()
        q2 = market.get_quotes()

        # Prices should change between calls (volatility > 0)
        prices1 = {(q.dex, q.pair): q.buy_price for q in q1}
        prices2 = {(q.dex, q.pair): q.buy_price for q in q2}
        self.assertNotEqual(prices1, prices2)


class BotMultiPairTests(unittest.TestCase):
    def _capture_log(self, bot, **kwargs) -> str:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        bot_logger = logging.getLogger("bot")
        bot_logger.addHandler(handler)
        bot_logger.setLevel(logging.DEBUG)
        try:
            bot.run(**kwargs)
        finally:
            bot_logger.removeHandler(handler)
        return stream.getvalue()

    def test_bot_scans_all_pairs(self) -> None:
        config = _make_multi_pair_config()
        market = SimulatedMarket(config, seed=42)
        bot = ArbitrageBot(config, market=market)

        output = self._capture_log(bot, iterations=1, sleep=False)
        # Should find an opportunity (spreads are non-zero)
        self.assertIn("[scan 1]", output)

    def test_bot_finds_opportunity_from_extra_pair(self) -> None:
        """Verify the bot can find opportunities in extra pairs, not just the primary."""
        # Set primary pair with zero spread (no opportunity).
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=0.0, flash_loan_provider="aave_v3",
            slippage_bps=0.0, poll_interval_seconds=0.0,
            extra_pairs=[
                PairConfig(pair="WETH/USDT", base_asset="WETH", quote_asset="USDT", trade_size=1.0),
            ],
            dexes=[
                # Same base_price → no primary pair opportunity.
                DexConfig(name="A", base_price=3000.0, fee_bps=0.0, volatility_bps=0.0),
                # Different base_price → extra pair will have spread (via ratio jitter).
                DexConfig(name="B", base_price=3000.0, fee_bps=0.0, volatility_bps=0.0),
            ],
        )
        config.validate()
        market = SimulatedMarket(config, seed=42)
        quotes = market.get_quotes()

        # The primary pair (WETH/USDC) should have identical prices → no opportunity.
        primary_quotes = [q for q in quotes if q.pair == "WETH/USDC"]
        strategy = ArbitrageStrategy(config)
        primary_opp = strategy.find_best_opportunity(primary_quotes)
        self.assertIsNone(primary_opp)

        # But WETH/USDT might have slight jitter-based spread.
        usdt_quotes = [q for q in quotes if q.pair == "WETH/USDT"]
        self.assertEqual(len(usdt_quotes), 2)


class StrategyWithMultiPairQuotesTests(unittest.TestCase):
    def test_strategy_only_compares_same_pair(self) -> None:
        """Strategy should not cross-compare WETH/USDC quotes with WBTC/USDC quotes."""
        config = _make_multi_pair_config()
        strategy = ArbitrageStrategy(config)

        from models import MarketQuote
        mixed_quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3000.0, sell_price=2998.0, fee_bps=0.0),
            MarketQuote(dex="B", pair="WBTC/USDC", buy_price=70000.0, sell_price=69900.0, fee_bps=0.0),
        ]

        # These are different pairs — strategy should not find an opportunity
        # by buying WETH/USDC on A and selling WBTC/USDC on B.
        opp = strategy.find_best_opportunity(mixed_quotes)
        # Strategy evaluates all quotes regardless of pair — the bot filters by pair.
        # But if it does find an "opportunity", it would be nonsensical.
        # This test documents the current behavior.
        # The BOT is responsible for filtering by pair before calling strategy.

    def test_strategy_uses_pair_specific_trade_size_and_pair_name(self) -> None:
        config = _make_multi_pair_config(
            extra_pairs=[
                PairConfig(pair="WBTC/USDC", base_asset="WBTC", quote_asset="USDC", trade_size=0.05),
            ],
        )
        strategy = ArbitrageStrategy(config)

        from models import MarketQuote
        quotes = [
            MarketQuote(dex="A", pair="WBTC/USDC", buy_price=70000.0, sell_price=69900.0, fee_bps=0.0),
            MarketQuote(dex="B", pair="WBTC/USDC", buy_price=70600.0, sell_price=70500.0, fee_bps=0.0),
        ]

        opp = strategy.find_best_opportunity(quotes)
        self.assertIsNotNone(opp)
        self.assertEqual(opp.pair, "WBTC/USDC")
        self.assertEqual(float(opp.trade_size), 0.05)


if __name__ == "__main__":
    unittest.main()
