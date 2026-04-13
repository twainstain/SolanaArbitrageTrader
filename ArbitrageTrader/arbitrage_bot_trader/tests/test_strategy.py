import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arbitrage_bot.config import BotConfig, DexConfig
from arbitrage_bot.models import MarketQuote
from arbitrage_bot.strategy import ArbitrageStrategy


def make_config() -> BotConfig:
    config = BotConfig(
        pair="WETH/USDC",
        base_asset="WETH",
        quote_asset="USDC",
        trade_size=1.0,
        min_profit_base=0.01,
        estimated_gas_cost_base=0.003,
        flash_loan_fee_bps=9.0,
        flash_loan_provider="aave_v3",
        slippage_bps=10.0,
        poll_interval_seconds=0.0,
        dexes=[
            DexConfig(name="A", base_price=3000.0, fee_bps=30.0, volatility_bps=0.0),
            DexConfig(name="B", base_price=3050.0, fee_bps=30.0, volatility_bps=0.0),
        ],
    )
    config.validate()
    return config


class StrategyTests(unittest.TestCase):
    def test_strategy_finds_profitable_opportunity(self) -> None:
        config = make_config()
        strategy = ArbitrageStrategy(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0, fee_bps=30.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0, fee_bps=30.0),
        ]

        opportunity = strategy.find_best_opportunity(quotes)

        self.assertIsNotNone(opportunity)
        assert opportunity is not None
        self.assertEqual(opportunity.buy_dex, "A")
        self.assertEqual(opportunity.sell_dex, "B")
        self.assertGreater(opportunity.net_profit_base, config.min_profit_base)

    def test_strategy_rejects_unprofitable_spread(self) -> None:
        config = make_config()
        strategy = ArbitrageStrategy(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0, fee_bps=30.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3002.0, sell_price=3000.0, fee_bps=30.0),
        ]

        opportunity = strategy.find_best_opportunity(quotes)

        self.assertIsNone(opportunity)

    def test_strategy_checks_all_cross_dex_pairs(self) -> None:
        config = BotConfig(
            pair="WETH/USDC",
            base_asset="WETH",
            quote_asset="USDC",
            trade_size=1.0,
            min_profit_base=0.001,
            estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=0.0,
            flash_loan_provider="aave_v3",
            slippage_bps=0.0,
            poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="A", base_price=100.0, fee_bps=0.0, volatility_bps=0.0),
                DexConfig(name="B", base_price=100.0, fee_bps=0.0, volatility_bps=0.0),
                DexConfig(name="C", base_price=100.0, fee_bps=0.0, volatility_bps=0.0),
            ],
        )
        config.validate()
        strategy = ArbitrageStrategy(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=100.0, sell_price=110.0, fee_bps=0.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=101.0, sell_price=109.0, fee_bps=0.0),
            MarketQuote(dex="C", pair="WETH/USDC", buy_price=90.0, sell_price=95.0, fee_bps=0.0),
        ]

        opportunity = strategy.find_best_opportunity(quotes)

        self.assertIsNotNone(opportunity)
        assert opportunity is not None
        self.assertEqual(opportunity.buy_dex, "C")
        self.assertEqual(opportunity.sell_dex, "A")


    def test_empty_quotes_returns_none(self) -> None:
        config = make_config()
        strategy = ArbitrageStrategy(config)

        result = strategy.find_best_opportunity([])
        self.assertIsNone(result)

    def test_single_quote_returns_none(self) -> None:
        config = make_config()
        strategy = ArbitrageStrategy(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0, fee_bps=30.0),
        ]

        result = strategy.find_best_opportunity(quotes)
        self.assertIsNone(result)

    def test_opportunity_fields_are_populated(self) -> None:
        config = make_config()
        strategy = ArbitrageStrategy(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0, fee_bps=30.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0, fee_bps=30.0),
        ]

        opp = strategy.find_best_opportunity(quotes)
        assert opp is not None

        self.assertEqual(opp.pair, "WETH/USDC")
        self.assertEqual(opp.trade_size, 1.0)
        self.assertGreater(opp.cost_to_buy_quote, 0)
        self.assertGreater(opp.proceeds_from_sell_quote, 0)
        self.assertGreater(opp.gross_profit_quote, 0)

    def test_fees_reduce_profit(self) -> None:
        """High fees should eliminate what looks like a small spread opportunity."""
        config = BotConfig(
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
            dexes=[
                DexConfig(name="A", base_price=100.0, fee_bps=0.0, volatility_bps=0.0),
                DexConfig(name="B", base_price=100.0, fee_bps=0.0, volatility_bps=0.0),
            ],
        )
        config.validate()

        # With zero fees, a $5 spread is profitable
        strategy_zero = ArbitrageStrategy(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=100.0, sell_price=99.0, fee_bps=0.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=106.0, sell_price=105.0, fee_bps=0.0),
        ]
        opp_zero = strategy_zero.find_best_opportunity(quotes)
        self.assertIsNotNone(opp_zero)

        # With high fees, same spread may not be profitable
        high_fee_quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=100.0, sell_price=99.0, fee_bps=500.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=106.0, sell_price=105.0, fee_bps=500.0),
        ]
        opp_fee = strategy_zero.find_best_opportunity(high_fee_quotes)
        self.assertIsNone(opp_fee)


    def test_identical_prices_across_dexes_returns_none(self) -> None:
        """When all DEXs have the same price, no arbitrage is possible."""
        config = make_config()
        strategy = ArbitrageStrategy(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3000.0, sell_price=2998.0, fee_bps=30.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3000.0, sell_price=2998.0, fee_bps=30.0),
        ]

        result = strategy.find_best_opportunity(quotes)
        self.assertIsNone(result)

    def test_zero_sell_price_returns_none(self) -> None:
        """A sell price of 0 means no proceeds — should not be profitable."""
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=0.0, flash_loan_provider="aave_v3",
            slippage_bps=0.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="A", base_price=100.0, fee_bps=0.0, volatility_bps=0.0),
                DexConfig(name="B", base_price=100.0, fee_bps=0.0, volatility_bps=0.0),
            ],
        )
        config.validate()
        strategy = ArbitrageStrategy(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=100.0, sell_price=99.0, fee_bps=0.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=100.0, sell_price=0.0, fee_bps=0.0),
        ]

        result = strategy.find_best_opportunity(quotes)
        self.assertIsNone(result)

    def test_gas_cost_eliminates_small_profit(self) -> None:
        """High gas cost should eliminate a small spread opportunity."""
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0,
            estimated_gas_cost_base=1.0,  # very high gas
            flash_loan_fee_bps=0.0, flash_loan_provider="aave_v3",
            slippage_bps=0.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="A", base_price=3000.0, fee_bps=0.0, volatility_bps=0.0),
                DexConfig(name="B", base_price=3010.0, fee_bps=0.0, volatility_bps=0.0),
            ],
        )
        config.validate()
        strategy = ArbitrageStrategy(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0, fee_bps=0.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3011.0, sell_price=3009.0, fee_bps=0.0),
        ]

        result = strategy.find_best_opportunity(quotes)
        self.assertIsNone(result)

    def test_flash_loan_fee_reduces_profit(self) -> None:
        """Flash loan fee should be subtracted from net profit."""
        config_no_flash = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=0.0, flash_loan_provider="aave_v3",
            slippage_bps=0.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="A", base_price=3000.0, fee_bps=0.0, volatility_bps=0.0),
                DexConfig(name="B", base_price=3050.0, fee_bps=0.0, volatility_bps=0.0),
            ],
        )
        config_no_flash.validate()
        config_with_flash = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=100.0, flash_loan_provider="aave_v3",
            slippage_bps=0.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="A", base_price=3000.0, fee_bps=0.0, volatility_bps=0.0),
                DexConfig(name="B", base_price=3050.0, fee_bps=0.0, volatility_bps=0.0),
            ],
        )
        config_with_flash.validate()

        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0, fee_bps=0.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0, fee_bps=0.0),
        ]

        opp_no = ArbitrageStrategy(config_no_flash).find_best_opportunity(quotes)
        opp_with = ArbitrageStrategy(config_with_flash).find_best_opportunity(quotes)

        self.assertIsNotNone(opp_no)
        self.assertIsNotNone(opp_with)
        assert opp_no is not None and opp_with is not None
        self.assertGreater(opp_no.net_profit_base, opp_with.net_profit_base)


if __name__ == "__main__":
    unittest.main()
