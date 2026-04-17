"""Tests verifying that financial math uses Decimal and avoids float precision errors.

Per CLAUDE.md: "NEVER use float (use Decimal or integer math)".
These tests prove that the system handles precision-sensitive scenarios correctly.
"""

import sys
from decimal import Decimal
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.config import BotConfig, DexConfig, PairConfig
from core.models import MarketQuote, Opportunity, ExecutionResult, ZERO
from strategy.arb_strategy import ArbitrageStrategy

D = Decimal


class DecimalCoercionTests(unittest.TestCase):
    """Verify that float/int inputs are auto-coerced to Decimal."""

    def test_market_quote_coerces_float_to_decimal(self) -> None:
        q = MarketQuote(dex="A", pair="WETH/USDC", buy_price=3000.5, sell_price=2999.5, fee_bps=30.0)
        self.assertIsInstance(q.buy_price, Decimal)
        self.assertIsInstance(q.sell_price, Decimal)
        self.assertIsInstance(q.fee_bps, Decimal)

    def test_market_quote_accepts_decimal_directly(self) -> None:
        q = MarketQuote(dex="A", pair="WETH/USDC", buy_price=D("3000.5"), sell_price=D("2999.5"), fee_bps=D("30"))
        self.assertEqual(q.buy_price, D("3000.5"))
        self.assertEqual(q.sell_price, D("2999.5"))

    def test_opportunity_coerces_float_to_decimal(self) -> None:
        opp = Opportunity(
            pair="WETH/USDC", buy_dex="A", sell_dex="B",
            trade_size=1.5, cost_to_buy_quote=3001.0,
            proceeds_from_sell_quote=3079.0,
            gross_profit_quote=78.0, net_profit_quote=50.0,
            net_profit_base=0.05,
        )
        self.assertIsInstance(opp.trade_size, Decimal)
        self.assertIsInstance(opp.net_profit_base, Decimal)
        self.assertEqual(opp.net_profit_base, D("0.05"))

    def test_execution_result_coerces_float(self) -> None:
        opp = Opportunity(
            pair="WETH/USDC", buy_dex="A", sell_dex="B",
            trade_size=1.0, cost_to_buy_quote=3001.0,
            proceeds_from_sell_quote=3079.0,
            gross_profit_quote=78.0, net_profit_quote=50.0,
            net_profit_base=0.05,
        )
        result = ExecutionResult(success=True, reason="test", realized_profit_base=0.05, opportunity=opp)
        self.assertIsInstance(result.realized_profit_base, Decimal)

    def test_dex_config_coerces_float(self) -> None:
        dex = DexConfig(name="A", base_price=3000.0, fee_bps=30.0, volatility_bps=10.0)
        self.assertIsInstance(dex.base_price, Decimal)
        self.assertIsInstance(dex.fee_bps, Decimal)
        self.assertEqual(dex.fee_bps, D("30.0"))

    def test_bot_config_coerces_float(self) -> None:
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.5, min_profit_base=0.008,
            estimated_gas_cost_base=0.002, flash_loan_fee_bps=9.0,
            flash_loan_provider="aave_v3", slippage_bps=15.0,
            poll_interval_seconds=0.5,
            dexes=[
                DexConfig(name="A", base_price=3000.0, fee_bps=30.0, volatility_bps=10.0),
                DexConfig(name="B", base_price=3050.0, fee_bps=30.0, volatility_bps=10.0),
            ],
        )
        self.assertIsInstance(config.trade_size, Decimal)
        self.assertIsInstance(config.min_profit_base, Decimal)
        self.assertIsInstance(config.slippage_bps, Decimal)
        # poll_interval_seconds stays float (timing, not financial)
        self.assertIsInstance(config.poll_interval_seconds, float)

    def test_pair_config_coerces_float(self) -> None:
        pc = PairConfig(pair="WETH/USDC", base_asset="WETH", quote_asset="USDC", trade_size=1.0)
        self.assertIsInstance(pc.trade_size, Decimal)


class DecimalPrecisionTests(unittest.TestCase):
    """Verify that Decimal avoids known float precision traps."""

    def test_classic_float_trap_0_1_plus_0_2(self) -> None:
        """0.1 + 0.2 != 0.3 in float, but works in Decimal."""
        a = D("0.1")
        b = D("0.2")
        self.assertEqual(a + b, D("0.3"))

    def test_fee_calculation_precision(self) -> None:
        """Verify that fee math doesn't accumulate float drift."""
        # 9 bps on 3001 USDC should be exactly 2.70090
        amount = D("3001")
        fee_bps = D("9")
        fee = amount * fee_bps / D("10000")
        self.assertEqual(fee, D("2.70090"))

    def test_strategy_produces_decimal_opportunity(self) -> None:
        """Verify the strategy outputs Decimal fields."""
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=D("1"), min_profit_base=D("0.001"),
            estimated_gas_cost_base=D("0"), flash_loan_fee_bps=D("0"),
            flash_loan_provider="aave_v3", slippage_bps=D("0"),
            poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="A", base_price=D("3000"), fee_bps=D("0"), volatility_bps=D("0")),
                DexConfig(name="B", base_price=D("3050"), fee_bps=D("0"), volatility_bps=D("0")),
            ],
        )
        config.validate()
        strategy = ArbitrageStrategy(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=D("3001"), sell_price=D("2999"), fee_bps=D("0")),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=D("3081"), sell_price=D("3079"), fee_bps=D("0")),
        ]

        opp = strategy.find_best_opportunity(quotes)
        self.assertIsNotNone(opp)
        assert opp is not None
        self.assertIsInstance(opp.net_profit_base, Decimal)
        self.assertIsInstance(opp.cost_to_buy_quote, Decimal)
        self.assertIsInstance(opp.gross_spread_pct, Decimal)
        self.assertGreater(opp.net_profit_base, ZERO)

    def test_net_profit_equals_exact_formula(self) -> None:
        """Verify the profit calculation matches the hand-calculated Decimal result."""
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=D("1"), min_profit_base=D("0"),
            estimated_gas_cost_base=D("0"), flash_loan_fee_bps=D("0"),
            flash_loan_provider="aave_v3", slippage_bps=D("0"),
            poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="A", base_price=D("100"), fee_bps=D("0"), volatility_bps=D("0")),
                DexConfig(name="B", base_price=D("110"), fee_bps=D("0"), volatility_bps=D("0")),
            ],
        )
        config.validate()
        strategy = ArbitrageStrategy(config)

        buy_price = D("100")
        sell_price = D("110")
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=buy_price, sell_price=D("99"), fee_bps=D("0")),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=D("111"), sell_price=sell_price, fee_bps=D("0")),
        ]

        opp = strategy.find_best_opportunity(quotes)
        assert opp is not None

        # With zero fees/gas/slippage: profit = (sell - buy) / mid_price
        expected_profit = (sell_price - buy_price) / ((buy_price + sell_price) / D("2"))
        self.assertEqual(opp.net_profit_base, expected_profit)

    def test_small_spread_precision(self) -> None:
        """A spread of 1 cent on $3000 should be computed exactly."""
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=D("1"), min_profit_base=D("0"),
            estimated_gas_cost_base=D("0"), flash_loan_fee_bps=D("0"),
            flash_loan_provider="aave_v3", slippage_bps=D("0"),
            poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="A", base_price=D("3000"), fee_bps=D("0"), volatility_bps=D("0")),
                DexConfig(name="B", base_price=D("3000"), fee_bps=D("0"), volatility_bps=D("0")),
            ],
        )
        config.validate()
        strategy = ArbitrageStrategy(config)

        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=D("3000.00"), sell_price=D("2999.99"), fee_bps=D("0")),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=D("3000.02"), sell_price=D("3000.01"), fee_bps=D("0")),
        ]

        opp = strategy.find_best_opportunity(quotes)
        assert opp is not None
        # 1 cent spread on ~$3000 = exactly $0.01 profit quote
        self.assertEqual(opp.gross_profit_quote, D("0.01"))


if __name__ == "__main__":
    unittest.main()
