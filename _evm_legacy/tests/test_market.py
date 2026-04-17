import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.config import BotConfig, DexConfig
from market.sim_market import SimulatedMarket


def _make_config(**overrides: object) -> BotConfig:
    defaults: dict = dict(
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
            DexConfig(name="A", base_price=3000.0, fee_bps=30.0, volatility_bps=10.0),
            DexConfig(name="B", base_price=3050.0, fee_bps=25.0, volatility_bps=15.0),
        ],
    )
    defaults.update(overrides)
    config = BotConfig(**defaults)
    config.validate()
    return config


class SimulatedMarketTests(unittest.TestCase):
    def test_returns_one_quote_per_dex(self) -> None:
        config = _make_config()
        market = SimulatedMarket(config)
        quotes = market.get_quotes()

        self.assertEqual(len(quotes), 2)
        dex_names = {q.dex for q in quotes}
        self.assertEqual(dex_names, {"A", "B"})

    def test_quotes_have_correct_pair(self) -> None:
        config = _make_config()
        market = SimulatedMarket(config)
        quotes = market.get_quotes()

        for q in quotes:
            self.assertEqual(q.pair, "WETH/USDC")

    def test_buy_price_above_sell_price(self) -> None:
        config = _make_config()
        market = SimulatedMarket(config)

        for _ in range(20):
            quotes = market.get_quotes()
            for q in quotes:
                self.assertGreater(q.buy_price, q.sell_price)

    def test_deterministic_with_same_seed(self) -> None:
        config = _make_config()

        market_a = SimulatedMarket(config, seed=42)
        market_b = SimulatedMarket(config, seed=42)

        for _ in range(10):
            quotes_a = market_a.get_quotes()
            quotes_b = market_b.get_quotes()
            for qa, qb in zip(quotes_a, quotes_b):
                self.assertEqual(qa.buy_price, qb.buy_price)
                self.assertEqual(qa.sell_price, qb.sell_price)

    def test_different_seeds_produce_different_prices(self) -> None:
        config = _make_config()

        market_a = SimulatedMarket(config, seed=1)
        market_b = SimulatedMarket(config, seed=99)

        # Advance several ticks so volatility kicks in
        for _ in range(5):
            quotes_a = market_a.get_quotes()
            quotes_b = market_b.get_quotes()

        self.assertNotEqual(quotes_a[0].buy_price, quotes_b[0].buy_price)

    def test_zero_volatility_prices_stay_constant(self) -> None:
        config = _make_config(dexes=[
            DexConfig(name="A", base_price=3000.0, fee_bps=30.0, volatility_bps=0.0),
            DexConfig(name="B", base_price=3050.0, fee_bps=25.0, volatility_bps=0.0),
        ])
        market = SimulatedMarket(config)

        first_quotes = market.get_quotes()
        for _ in range(10):
            later_quotes = market.get_quotes()

        for fq, lq in zip(first_quotes, later_quotes):
            self.assertAlmostEqual(fq.buy_price, lq.buy_price, places=6)
            self.assertAlmostEqual(fq.sell_price, lq.sell_price, places=6)

    def test_fee_bps_matches_dex_config(self) -> None:
        config = _make_config()
        market = SimulatedMarket(config)
        quotes = market.get_quotes()

        fee_map = {d.name: d.fee_bps for d in config.dexes}
        for q in quotes:
            self.assertEqual(q.fee_bps, fee_map[q.dex])

    def test_three_dex_market(self) -> None:
        config = _make_config(dexes=[
            DexConfig(name="Uni", base_price=3000.0, fee_bps=30.0, volatility_bps=10.0),
            DexConfig(name="Sushi", base_price=3010.0, fee_bps=30.0, volatility_bps=12.0),
            DexConfig(name="Bal", base_price=3005.0, fee_bps=25.0, volatility_bps=8.0),
        ])
        market = SimulatedMarket(config)
        quotes = market.get_quotes()

        self.assertEqual(len(quotes), 3)
        dex_names = {q.dex for q in quotes}
        self.assertEqual(dex_names, {"Uni", "Sushi", "Bal"})


if __name__ == "__main__":
    unittest.main()
