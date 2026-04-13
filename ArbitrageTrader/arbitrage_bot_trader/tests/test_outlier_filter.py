"""Tests for the bot's outlier filter and multi-chain quote handling."""

from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arbitrage_bot.bot import ArbitrageBot
from arbitrage_bot.models import MarketQuote


class OutlierFilterTests(unittest.TestCase):
    def test_removes_price_outlier(self) -> None:
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=2201.0, sell_price=2199.0, fee_bps=30.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=2205.0, sell_price=2203.0, fee_bps=30.0),
            MarketQuote(dex="C", pair="WETH/USDC", buy_price=40.0, sell_price=39.0, fee_bps=30.0),  # outlier
        ]
        filtered = ArbitrageBot._filter_outliers(quotes)
        self.assertEqual(len(filtered), 2)
        dexes = {q.dex for q in filtered}
        self.assertEqual(dexes, {"A", "B"})

    def test_keeps_all_when_no_outliers(self) -> None:
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=2201.0, sell_price=2199.0, fee_bps=30.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=2205.0, sell_price=2203.0, fee_bps=30.0),
            MarketQuote(dex="C", pair="WETH/USDC", buy_price=2210.0, sell_price=2208.0, fee_bps=30.0),
        ]
        filtered = ArbitrageBot._filter_outliers(quotes)
        self.assertEqual(len(filtered), 3)

    def test_handles_single_quote_pair(self) -> None:
        quotes = [
            MarketQuote(dex="A", pair="ARB/WETH", buy_price=0.0001, sell_price=0.00009, fee_bps=30.0),
        ]
        filtered = ArbitrageBot._filter_outliers(quotes)
        self.assertEqual(len(filtered), 1)

    def test_handles_multiple_pairs_independently(self) -> None:
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=2201.0, sell_price=2199.0, fee_bps=30.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=2205.0, sell_price=2203.0, fee_bps=30.0),
            MarketQuote(dex="C", pair="WETH/USDC", buy_price=40.0, sell_price=39.0, fee_bps=30.0),  # outlier
            MarketQuote(dex="A", pair="WBTC/USDC", buy_price=70001.0, sell_price=69999.0, fee_bps=30.0),
            MarketQuote(dex="B", pair="WBTC/USDC", buy_price=70101.0, sell_price=70099.0, fee_bps=30.0),
        ]
        filtered = ArbitrageBot._filter_outliers(quotes)
        # WETH outlier removed, WBTC both kept
        self.assertEqual(len(filtered), 4)
        weth = [q for q in filtered if q.pair == "WETH/USDC"]
        wbtc = [q for q in filtered if q.pair == "WBTC/USDC"]
        self.assertEqual(len(weth), 2)
        self.assertEqual(len(wbtc), 2)

    def test_empty_quotes(self) -> None:
        filtered = ArbitrageBot._filter_outliers([])
        self.assertEqual(len(filtered), 0)

    def test_custom_deviation_threshold(self) -> None:
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=2200.0, sell_price=2198.0, fee_bps=30.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=2201.0, sell_price=2199.0, fee_bps=30.0),
            MarketQuote(dex="C", pair="WETH/USDC", buy_price=2800.0, sell_price=2798.0, fee_bps=30.0),  # ~27% off median
        ]
        # Default 50% threshold — all kept
        filtered_50 = ArbitrageBot._filter_outliers(quotes, max_deviation=0.5)
        self.assertEqual(len(filtered_50), 3)
        # Strict 10% threshold — C removed
        filtered_10 = ArbitrageBot._filter_outliers(quotes, max_deviation=0.1)
        self.assertEqual(len(filtered_10), 2)


class MultiChainTokensTests(unittest.TestCase):
    def test_token_registry_has_all_chains(self) -> None:
        from arbitrage_bot.tokens import CHAIN_TOKENS
        expected = {"ethereum", "arbitrum", "base", "bsc", "polygon", "optimism",
                    "avax", "fantom", "gnosis", "linea", "scroll", "zksync"}
        self.assertTrue(expected.issubset(set(CHAIN_TOKENS.keys())))

    def test_all_chains_have_weth_and_usdc(self) -> None:
        from arbitrage_bot.tokens import CHAIN_TOKENS
        for chain, tokens in CHAIN_TOKENS.items():
            self.assertTrue(tokens.weth, f"{chain} missing weth")
            self.assertTrue(tokens.usdc, f"{chain} missing usdc")


class PairConfigWithAddressTests(unittest.TestCase):
    def test_pair_config_carries_addresses(self) -> None:
        from arbitrage_bot.config import PairConfig
        pc = PairConfig(
            pair="ARB/WETH",
            base_asset="ARB",
            quote_asset="WETH",
            trade_size=100.0,
            base_address="0xB50721BCf8d664c30412Cfbc6cf7a15145234ad1",
            quote_address="0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
            chain="arbitrum",
        )
        self.assertEqual(pc.base_address, "0xB50721BCf8d664c30412Cfbc6cf7a15145234ad1")
        self.assertEqual(pc.chain, "arbitrum")

    def test_pair_config_defaults_none(self) -> None:
        from arbitrage_bot.config import PairConfig
        pc = PairConfig(pair="WETH/USDC", base_asset="WETH", quote_asset="USDC", trade_size=1.0)
        self.assertIsNone(pc.base_address)
        self.assertIsNone(pc.chain)


if __name__ == "__main__":
    unittest.main()
