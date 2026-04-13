from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import List, Dict
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arbitrage_bot.config import BotConfig, DexConfig
from arbitrage_bot.historical_market import HistoricalMarket, HistoricalMarketError


def _make_config() -> BotConfig:
    config = BotConfig(
        pair="WETH/USDC",
        base_asset="WETH",
        quote_asset="USDC",
        trade_size=1.0,
        min_profit_base=0.0,
        estimated_gas_cost_base=0.0,
        flash_loan_fee_bps=9.0,
        flash_loan_provider="aave_v3",
        slippage_bps=10.0,
        poll_interval_seconds=0.0,
        dexes=[
            DexConfig(name="Uniswap", base_price=0, fee_bps=5.0, volatility_bps=0,
                      chain="ethereum", dex_type="uniswap_v3"),
            DexConfig(name="Sushi", base_price=0, fee_bps=30.0, volatility_bps=0,
                      chain="ethereum", dex_type="sushi_v3"),
        ],
    )
    config.validate()
    return config


def _make_data_file(dex: str, chain: str, snapshots: list[dict]) -> str:
    """Write a temporary JSON data file and return its path."""
    data = {
        "dex": dex,
        "chain": chain,
        "pool": "0xfake",
        "pair": "WETH/USDC",
        "snapshots": snapshots,
    }
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.flush()
    f.close()
    return f.name


def _sample_snapshots(base_price: float, count: int = 5, start_ts: int = 1700000000) -> list[dict]:
    """Generate sample hourly snapshots with small price variations."""
    snaps = []
    for i in range(count):
        price = base_price + i * 0.5
        snaps.append({
            "timestamp": start_ts + i * 3600,
            "open": price - 0.1,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price,
            "token0Price": str(1 / price),
            "token1Price": str(price),
            "liquidity": "12345678",
            "volumeUSD": "1000000",
        })
    return snaps


class HistoricalMarketInitTests(unittest.TestCase):
    def test_raises_with_no_files(self) -> None:
        config = _make_config()
        with self.assertRaises(HistoricalMarketError, msg="At least one"):
            HistoricalMarket(config, data_files=[])

    def test_raises_with_empty_snapshots(self) -> None:
        config = _make_config()
        f = _make_data_file("uniswap_v3", "ethereum", [])
        with self.assertRaises(HistoricalMarketError, msg="No snapshots"):
            HistoricalMarket(config, data_files=[f])

    def test_raises_with_no_overlapping_timestamps(self) -> None:
        config = _make_config()
        f1 = _make_data_file("uniswap_v3", "ethereum", _sample_snapshots(2200.0, 5, 1700000000))
        f2 = _make_data_file("sushi_v3", "ethereum", _sample_snapshots(2195.0, 5, 1800000000))
        with self.assertRaises(HistoricalMarketError, msg="No overlapping"):
            HistoricalMarket(config, data_files=[f1, f2])

    def test_creates_with_valid_files(self) -> None:
        config = _make_config()
        f1 = _make_data_file("uniswap_v3", "ethereum", _sample_snapshots(2200.0))
        f2 = _make_data_file("sushi_v3", "ethereum", _sample_snapshots(2195.0))
        market = HistoricalMarket(config, data_files=[f1, f2])
        self.assertEqual(market.total_ticks, 5)


class HistoricalMarketReplayTests(unittest.TestCase):
    def _make_market(self) -> HistoricalMarket:
        config = _make_config()
        f1 = _make_data_file("uniswap_v3", "ethereum", _sample_snapshots(2200.0, 5))
        f2 = _make_data_file("sushi_v3", "ethereum", _sample_snapshots(2195.0, 5))
        return HistoricalMarket(config, data_files=[f1, f2])

    def test_returns_one_quote_per_venue(self) -> None:
        market = self._make_market()
        quotes = market.get_quotes()
        self.assertEqual(len(quotes), 2)

    def test_advances_tick_each_call(self) -> None:
        market = self._make_market()
        self.assertEqual(market.ticks_remaining, 5)

        q1 = market.get_quotes()
        self.assertEqual(market.ticks_remaining, 4)

        q2 = market.get_quotes()
        self.assertEqual(market.ticks_remaining, 3)

        # Prices should differ between ticks (we added +0.5 per hour).
        mid1 = (q1[0].buy_price + q1[0].sell_price) / 2
        mid2 = (q2[0].buy_price + q2[0].sell_price) / 2
        self.assertNotEqual(mid1, mid2)

    def test_raises_when_exhausted(self) -> None:
        market = self._make_market()
        for _ in range(5):
            market.get_quotes()

        with self.assertRaises(HistoricalMarketError, msg="exhausted"):
            market.get_quotes()

    def test_buy_above_sell(self) -> None:
        market = self._make_market()
        for _ in range(5):
            quotes = market.get_quotes()
            for q in quotes:
                self.assertGreater(q.buy_price, q.sell_price)

    def test_pair_correct(self) -> None:
        market = self._make_market()
        quotes = market.get_quotes()
        for q in quotes:
            self.assertEqual(q.pair, "WETH/USDC")

    def test_price_difference_between_venues(self) -> None:
        market = self._make_market()
        quotes = market.get_quotes()
        mids = [(q.buy_price + q.sell_price) / 2 for q in quotes]
        # Venue 1 base=2200, venue 2 base=2195, so they should differ.
        self.assertNotEqual(mids[0], mids[1])
        self.assertGreater(mids[0], mids[1])

    def test_total_ticks_matches_common_timestamps(self) -> None:
        config = _make_config()
        # 3 overlapping out of 5 each
        snaps1 = _sample_snapshots(2200.0, 5, 1700000000)
        snaps2 = _sample_snapshots(2195.0, 5, 1700007200)  # starts 2h later
        f1 = _make_data_file("uniswap_v3", "ethereum", snaps1)
        f2 = _make_data_file("sushi_v3", "ethereum", snaps2)
        market = HistoricalMarket(config, data_files=[f1, f2])
        # snaps1 timestamps: 0, 3600, 7200, 10800, 14400
        # snaps2 timestamps: 7200, 10800, 14400, 18000, 21600
        # Common: 7200, 10800, 14400 = 3 ticks
        self.assertEqual(market.total_ticks, 3)


class HistoricalMarketSingleFileTests(unittest.TestCase):
    def test_single_file_works(self) -> None:
        config = _make_config()
        f1 = _make_data_file("uniswap_v3", "ethereum", _sample_snapshots(2200.0, 3))
        market = HistoricalMarket(config, data_files=[f1])
        self.assertEqual(market.total_ticks, 3)

        quotes = market.get_quotes()
        self.assertEqual(len(quotes), 1)


class HistoricalMarketZeroPriceTests(unittest.TestCase):
    def test_zero_close_falls_back_to_token_price(self) -> None:
        """When close=0, _extract_price should use token0Price/token1Price."""
        config = _make_config()
        snaps = [{
            "timestamp": 1700000000,
            "open": 0, "high": 0, "low": 0, "close": 0,
            "token0Price": "0.000454",
            "token1Price": "2200.0",
            "liquidity": "0", "volumeUSD": "0",
        }]
        f1 = _make_data_file("uniswap_v3", "ethereum", snaps)
        market = HistoricalMarket(config, data_files=[f1])
        quotes = market.get_quotes()
        mid = (quotes[0].buy_price + quotes[0].sell_price) / 2
        self.assertAlmostEqual(mid, 2200.0, places=0)

    def test_all_zero_prices_raises(self) -> None:
        """When close=0 and both token prices=0, should raise."""
        config = _make_config()
        snaps = [{
            "timestamp": 1700000000,
            "open": 0, "high": 0, "low": 0, "close": 0,
            "token0Price": "0",
            "token1Price": "0",
            "liquidity": "0", "volumeUSD": "0",
        }]
        f1 = _make_data_file("uniswap_v3", "ethereum", snaps)
        market = HistoricalMarket(config, data_files=[f1])
        with self.assertRaises(HistoricalMarketError, msg="Cannot extract price"):
            market.get_quotes()

    def test_exhausted_then_called_again_still_raises(self) -> None:
        config = _make_config()
        f1 = _make_data_file("uniswap_v3", "ethereum", _sample_snapshots(2200.0, 1))
        market = HistoricalMarket(config, data_files=[f1])
        market.get_quotes()  # consume the only tick

        with self.assertRaises(HistoricalMarketError):
            market.get_quotes()
        # Calling again should still raise, not crash differently.
        with self.assertRaises(HistoricalMarketError):
            market.get_quotes()


if __name__ == "__main__":
    unittest.main()
