"""Tests for smart pair discovery."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from registry.discovery import (
    discover_best_pairs, DiscoveredPair,
    SUPPORTED_CHAINS, BLUE_CHIP_TOKENS, _normalize,
)


def _fake_dexscreener_response(pairs):
    """Build fake DexScreener API pairs."""
    return pairs


def _make_pair(base="WETH", quote="USDC", chain="ethereum", dex="uniswap",
               volume=500_000, liquidity=1_000_000):
    return {
        "chainId": chain,
        "dexId": dex,
        "baseToken": {"symbol": base, "address": "0xbase"},
        "quoteToken": {"symbol": quote, "address": "0xquote"},
        "volume": {"h24": volume},
        "liquidity": {"usd": liquidity},
    }


class NormalizeTests(unittest.TestCase):
    def test_weth(self):
        self.assertEqual(_normalize("WETH"), "WETH")
        self.assertEqual(_normalize("eth"), "WETH")
        self.assertEqual(_normalize("ETH"), "WETH")

    def test_other(self):
        self.assertEqual(_normalize("usdc"), "USDC")


class SupportedChainsTests(unittest.TestCase):
    def test_has_top_chains(self):
        for chain in ["ethereum", "arbitrum", "base", "bsc", "polygon", "optimism"]:
            self.assertIn(chain, SUPPORTED_CHAINS)

    def test_no_non_evm(self):
        for chain in ["solana", "tron", "bitcoin", "aptos"]:
            self.assertNotIn(chain, SUPPORTED_CHAINS)


class BluechipTests(unittest.TestCase):
    def test_majors_are_blue_chip(self):
        for t in ["WETH", "WBTC", "USDC", "USDT", "DAI"]:
            self.assertIn(t, BLUE_CHIP_TOKENS)


class DiscoveryTests(unittest.TestCase):
    @patch("registry.discovery._search_dexscreener")
    def test_finds_cross_dex_pairs(self, mock_search):
        mock_search.return_value = [
            _make_pair(dex="uniswap", volume=1_000_000),
            _make_pair(dex="sushiswap", volume=500_000),
        ]
        results = discover_best_pairs(
            chains=["ethereum"], search_tokens=["WETH"],
            min_volume=100_000, min_dex_count=2,
        )
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0].pair_name, "WETH/USDC")
        self.assertEqual(results[0].dex_count, 2)

    @patch("registry.discovery._search_dexscreener")
    def test_filters_low_volume(self, mock_search):
        mock_search.return_value = [
            _make_pair(dex="uniswap", volume=50),
            _make_pair(dex="sushi", volume=50),
        ]
        results = discover_best_pairs(
            chains=["ethereum"], search_tokens=["WETH"],
            min_volume=100_000,
        )
        self.assertEqual(len(results), 0)

    @patch("registry.discovery._search_dexscreener")
    def test_filters_single_dex(self, mock_search):
        mock_search.return_value = [
            _make_pair(dex="uniswap", volume=1_000_000),
        ]
        results = discover_best_pairs(
            chains=["ethereum"], search_tokens=["WETH"],
            min_dex_count=2,
        )
        self.assertEqual(len(results), 0)

    @patch("registry.discovery._search_dexscreener")
    def test_filters_non_evm_chains(self, mock_search):
        mock_search.return_value = [
            _make_pair(chain="solana", dex="raydium", volume=1_000_000),
            _make_pair(chain="solana", dex="orca", volume=500_000),
        ]
        results = discover_best_pairs(
            chains=["ethereum"], search_tokens=["WETH"],
        )
        self.assertEqual(len(results), 0)

    @patch("registry.discovery._search_dexscreener")
    def test_blue_chip_gets_higher_score(self, mock_search):
        mock_search.side_effect = [
            # WETH search
            [
                _make_pair(base="WETH", quote="USDC", dex="uni", volume=500_000),
                _make_pair(base="WETH", quote="USDC", dex="sushi", volume=500_000),
            ],
            # SHIB search
            [
                _make_pair(base="SHIB", quote="USDC", dex="uni", volume=500_000),
                _make_pair(base="SHIB", quote="USDC", dex="sushi", volume=500_000),
            ],
        ]
        results = discover_best_pairs(
            chains=["ethereum"],
            search_tokens=["WETH", "SHIB"],
            min_volume=100_000, min_dex_count=2,
        )
        self.assertGreater(len(results), 0)
        # WETH/USDC should rank higher than SHIB/USDC (blue chip 2x bonus).
        weth_pair = next((p for p in results if p.pair_name == "WETH/USDC"), None)
        shib_pair = next((p for p in results if p.pair_name == "SHIB/USDC"), None)
        if weth_pair and shib_pair:
            self.assertGreater(weth_pair.arbitrage_score, shib_pair.arbitrage_score)

    @patch("registry.discovery._search_dexscreener")
    def test_sorted_by_score_descending(self, mock_search):
        mock_search.return_value = [
            _make_pair(base="WETH", quote="USDC", dex="uni", chain="ethereum", volume=2_000_000),
            _make_pair(base="WETH", quote="USDC", dex="sushi", chain="ethereum", volume=1_000_000),
            _make_pair(base="WETH", quote="USDC", dex="uni", chain="arbitrum", volume=500_000),
            _make_pair(base="WETH", quote="USDC", dex="sushi", chain="arbitrum", volume=300_000),
        ]
        results = discover_best_pairs(
            chains=["ethereum", "arbitrum"], search_tokens=["WETH"],
            min_dex_count=2,
        )
        if len(results) >= 2:
            self.assertGreaterEqual(results[0].arbitrage_score, results[1].arbitrage_score)

    @patch("registry.discovery._search_dexscreener", side_effect=Exception("network"))
    def test_handles_api_failure_gracefully(self, mock_search):
        results = discover_best_pairs(search_tokens=["WETH"])
        self.assertEqual(len(results), 0)


if __name__ == "__main__":
    unittest.main()
