import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.config import BotConfig, DexConfig
from core.models import MarketQuote
from strategy.arb_strategy import ArbitrageStrategy


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


class DynamicSlippageTests(unittest.TestCase):
    def _make_zero_cost_config(self, slippage_bps: float = 15.0) -> BotConfig:
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=0.0, flash_loan_provider="aave_v3",
            slippage_bps=slippage_bps, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="A", base_price=3000.0, fee_bps=0.0, volatility_bps=0.0),
                DexConfig(name="B", base_price=3050.0, fee_bps=0.0, volatility_bps=0.0),
            ],
        )
        config.validate()
        return config

    def test_deep_pool_slippage_near_base(self) -> None:
        """With $50M liquidity, slippage should be very close to the configured base."""
        config = self._make_zero_cost_config(slippage_bps=15.0)
        strategy = ArbitrageStrategy(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0,
                        fee_bps=0.0, liquidity_usd=50_000_000),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0,
                        fee_bps=0.0, liquidity_usd=50_000_000),
        ]
        opp_deep = strategy.find_best_opportunity(quotes)
        assert opp_deep is not None

        # $3001 trade in $50M pool → impact ratio ≈ 0.00006 → negligible extra slippage.
        # slippage_cost should be very close to base: 3001 * 15/10000 ≈ 4.5
        self.assertAlmostEqual(float(opp_deep.slippage_cost_quote), 4.5015, delta=0.1)

    def test_thin_pool_has_higher_slippage(self) -> None:
        """With $50K liquidity, slippage should be significantly higher."""
        config = self._make_zero_cost_config(slippage_bps=15.0)
        strategy = ArbitrageStrategy(config)

        # Deep pool quotes
        deep_quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0,
                        fee_bps=0.0, liquidity_usd=50_000_000),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0,
                        fee_bps=0.0, liquidity_usd=50_000_000),
        ]

        # Thin pool quotes (same prices, less liquidity)
        thin_quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0,
                        fee_bps=0.0, liquidity_usd=50_000),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0,
                        fee_bps=0.0, liquidity_usd=50_000),
        ]

        opp_deep = strategy.find_best_opportunity(deep_quotes)
        opp_thin = strategy.find_best_opportunity(thin_quotes)

        assert opp_deep is not None
        assert opp_thin is not None
        # Thin pool should have higher slippage cost.
        self.assertGreater(opp_thin.slippage_cost_quote, opp_deep.slippage_cost_quote)

    def test_no_liquidity_data_uses_base_slippage(self) -> None:
        """When liquidity_usd is 0 (default), should use flat base slippage."""
        config = self._make_zero_cost_config(slippage_bps=15.0)
        strategy = ArbitrageStrategy(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0, fee_bps=0.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0, fee_bps=0.0),
        ]

        opp = strategy.find_best_opportunity(quotes)
        assert opp is not None
        # With no liquidity data: slippage = 3001 * 15/10000 = 4.5015
        self.assertAlmostEqual(float(opp.slippage_cost_quote), 4.5015, delta=0.001)


class PerChainGasCostTests(unittest.TestCase):
    """Tests for per-chain gas cost in strategy."""

    def _make_config(self, chain_gas_cost=None) -> BotConfig:
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.001,
            flash_loan_fee_bps=0.0, flash_loan_provider="aave_v3",
            slippage_bps=0.0, poll_interval_seconds=0.0,
            chain_gas_cost=chain_gas_cost,
            dexes=[
                DexConfig(name="Uni-Eth", base_price=3000.0, fee_bps=0.0,
                          volatility_bps=0.0, chain="ethereum", dex_type="uniswap_v3"),
                DexConfig(name="Sushi-Eth", base_price=3050.0, fee_bps=0.0,
                          volatility_bps=0.0, chain="ethereum", dex_type="sushi_v3"),
                DexConfig(name="Uni-Arb", base_price=3000.0, fee_bps=0.0,
                          volatility_bps=0.0, chain="arbitrum", dex_type="uniswap_v3"),
                DexConfig(name="Sushi-Arb", base_price=3050.0, fee_bps=0.0,
                          volatility_bps=0.0, chain="arbitrum", dex_type="sushi_v3"),
            ],
        )
        config.validate()
        return config

    def test_gas_cost_for_chain_with_override(self):
        config = self._make_config(chain_gas_cost={"ethereum": 0.005, "arbitrum": 0.0002})
        self.assertEqual(float(config.gas_cost_for_chain("ethereum")), 0.005)
        self.assertEqual(float(config.gas_cost_for_chain("arbitrum")), 0.0002)

    def test_gas_cost_for_chain_fallback(self):
        config = self._make_config(chain_gas_cost={"ethereum": 0.005})
        # arbitrum not in overrides → falls back to estimated_gas_cost_base
        self.assertEqual(float(config.gas_cost_for_chain("arbitrum")), 0.001)

    def test_gas_cost_for_chain_no_overrides(self):
        config = self._make_config(chain_gas_cost=None)
        self.assertEqual(float(config.gas_cost_for_chain("ethereum")), 0.001)

    def test_ethereum_higher_gas_reduces_profit(self):
        """Ethereum with 0.005 gas should have lower net profit than Arbitrum with 0.0002."""
        config = self._make_config(chain_gas_cost={"ethereum": 0.005, "arbitrum": 0.0002})
        strategy = ArbitrageStrategy(config)

        eth_quotes = [
            MarketQuote(dex="Uni-Eth", pair="WETH/USDC", buy_price=3000.0, sell_price=2999.0, fee_bps=0.0),
            MarketQuote(dex="Sushi-Eth", pair="WETH/USDC", buy_price=3050.0, sell_price=3049.0, fee_bps=0.0),
        ]
        arb_quotes = [
            MarketQuote(dex="Uni-Arb", pair="WETH/USDC", buy_price=3000.0, sell_price=2999.0, fee_bps=0.0),
            MarketQuote(dex="Sushi-Arb", pair="WETH/USDC", buy_price=3050.0, sell_price=3049.0, fee_bps=0.0),
        ]

        opp_eth = strategy.find_best_opportunity(eth_quotes)
        opp_arb = strategy.find_best_opportunity(arb_quotes)
        assert opp_eth is not None and opp_arb is not None

        # Same spread, but Ethereum has 25x higher gas
        self.assertGreater(opp_arb.net_profit_base, opp_eth.net_profit_base)
        self.assertAlmostEqual(float(opp_eth.gas_cost_base), 0.005, places=4)
        self.assertAlmostEqual(float(opp_arb.gas_cost_base), 0.0002, places=4)

    def test_high_gas_can_make_opportunity_unprofitable(self):
        """Ethereum gas of 0.02 ETH should make a small spread unprofitable."""
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.001, estimated_gas_cost_base=0.001,
            flash_loan_fee_bps=0.0, flash_loan_provider="aave_v3",
            slippage_bps=0.0, poll_interval_seconds=0.0,
            chain_gas_cost={"ethereum": 0.02},
            dexes=[
                DexConfig(name="Uni-Eth", base_price=3000.0, fee_bps=0.0,
                          volatility_bps=0.0, chain="ethereum", dex_type="uniswap_v3"),
                DexConfig(name="Sushi-Eth", base_price=3020.0, fee_bps=0.0,
                          volatility_bps=0.0, chain="ethereum", dex_type="sushi_v3"),
            ],
        )
        config.validate()
        strategy = ArbitrageStrategy(config)

        quotes = [
            MarketQuote(dex="Uni-Eth", pair="WETH/USDC", buy_price=3000.0, sell_price=2999.0, fee_bps=0.0),
            MarketQuote(dex="Sushi-Eth", pair="WETH/USDC", buy_price=3020.0, sell_price=3019.0, fee_bps=0.0),
        ]
        opp = strategy.find_best_opportunity(quotes)
        # $20 spread / $3010 mid ≈ 0.0066 ETH gross profit - 0.02 gas = negative
        # Strategy should filter this as not actionable
        if opp is not None:
            self.assertFalse(opp.is_actionable)


if __name__ == "__main__":
    unittest.main()
