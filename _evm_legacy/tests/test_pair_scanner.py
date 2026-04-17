import sys
from pathlib import Path
from unittest.mock import MagicMock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tools.pair_scanner import (
    PairInfo,
    _normalize_symbol,
    find_cross_dex_pairs,
    search_pairs_by_symbol,
    search_pairs_by_address,
)


def _mock_dexscreener_response() -> dict:
    return {
        "pairs": [
            {
                "chainId": "ethereum",
                "dexId": "uniswap",
                "pairAddress": "0xaaa",
                "baseToken": {"symbol": "WETH", "address": "0xC02"},
                "quoteToken": {"symbol": "USDC", "address": "0xA0b"},
                "priceUsd": "2200.0",
                "volume": {"h24": 5000000},
                "liquidity": {"usd": 100000000},
                "url": "https://dexscreener.com/ethereum/0xaaa",
            },
            {
                "chainId": "ethereum",
                "dexId": "pancakeswap",
                "pairAddress": "0xbbb",
                "baseToken": {"symbol": "WETH", "address": "0xC02"},
                "quoteToken": {"symbol": "USDC", "address": "0xA0b"},
                "priceUsd": "2199.5",
                "volume": {"h24": 2000000},
                "liquidity": {"usd": 50000000},
                "url": "https://dexscreener.com/ethereum/0xbbb",
            },
            {
                "chainId": "ethereum",
                "dexId": "sushiswap",
                "pairAddress": "0xeee",
                "baseToken": {"symbol": "WETH", "address": "0xC02"},
                "quoteToken": {"symbol": "USDT", "address": "0xdAC"},
                "priceUsd": "2201.0",
                "volume": {"h24": 1500000},
                "liquidity": {"usd": 30000000},
                "url": "https://dexscreener.com/ethereum/0xeee",
            },
            {
                "chainId": "ethereum",
                "dexId": "uniswap",
                "pairAddress": "0xfff",
                "baseToken": {"symbol": "WETH", "address": "0xC02"},
                "quoteToken": {"symbol": "USDT", "address": "0xdAC"},
                "priceUsd": "2200.5",
                "volume": {"h24": 3000000},
                "liquidity": {"usd": 80000000},
                "url": "https://dexscreener.com/ethereum/0xfff",
            },
            {
                "chainId": "ethereum",
                "dexId": "uniswap",
                "pairAddress": "0xccc",
                "baseToken": {"symbol": "PEPE", "address": "0xPEPE"},
                "quoteToken": {"symbol": "WETH", "address": "0xC02"},
                "priceUsd": "0.00001",
                "volume": {"h24": 50000},
                "liquidity": {"usd": 500000},
                "url": "https://dexscreener.com/ethereum/0xccc",
            },
            {
                "chainId": "bsc",
                "dexId": "pancakeswap",
                "pairAddress": "0xddd",
                "baseToken": {"symbol": "WETH", "address": "0x2170"},
                "quoteToken": {"symbol": "USDT", "address": "0x55d3"},
                "priceUsd": "2198.0",
                "volume": {"h24": 1000000},
                "liquidity": {"usd": 20000000},
                "url": "https://dexscreener.com/bsc/0xddd",
            },
        ]
    }


def _mock_get(url, **kwargs):
    resp = MagicMock()
    resp.json.return_value = _mock_dexscreener_response()
    resp.raise_for_status = MagicMock()
    return resp


class SearchBySymbolTests(unittest.TestCase):
    def _search(self, chain=None, min_volume=0):
        import requests
        original_get = requests.get
        requests.get = _mock_get
        try:
            return search_pairs_by_symbol("WETH", chain=chain, min_volume=min_volume)
        finally:
            requests.get = original_get

    def test_returns_all_pairs(self) -> None:
        results = self._search()
        self.assertEqual(len(results), 6)

    def test_filters_by_chain(self) -> None:
        results = self._search(chain="ethereum")
        self.assertEqual(len(results), 5)
        for r in results:
            self.assertEqual(r.chain, "ethereum")

    def test_filters_by_min_volume(self) -> None:
        results = self._search(min_volume=1_000_000)
        for r in results:
            self.assertGreaterEqual(r.volume_24h, 1_000_000)

    def test_sorted_by_volume_desc(self) -> None:
        results = self._search()
        volumes = [r.volume_24h for r in results]
        self.assertEqual(volumes, sorted(volumes, reverse=True))


class SearchByAddressTests(unittest.TestCase):
    def test_returns_results(self) -> None:
        import requests
        original_get = requests.get
        requests.get = _mock_get
        try:
            results = search_pairs_by_address("0xC02aaA39b223FE8D0A0e5c4F27eAD9083C756Cc2")
        finally:
            requests.get = original_get
        self.assertGreater(len(results), 0)


class CrossDexPairsTests(unittest.TestCase):
    def _find(self, chain=None, min_volume=0):
        import requests
        original_get = requests.get
        requests.get = _mock_get
        try:
            return find_cross_dex_pairs(query="WETH", chain=chain, min_volume=min_volume)
        finally:
            requests.get = original_get

    def test_finds_weth_usdc_multi_dex(self) -> None:
        results = self._find(chain="ethereum")
        self.assertIn("WETH/USDC", results)
        dex_names = {p.dex for p in results["WETH/USDC"]}
        self.assertGreaterEqual(len(dex_names), 2)

    def test_finds_weth_usdt_multi_dex(self) -> None:
        results = self._find(chain="ethereum")
        self.assertIn("WETH/USDT", results)
        dex_names = {p.dex for p in results["WETH/USDT"]}
        self.assertGreaterEqual(len(dex_names), 2)

    def test_excludes_single_dex_pairs(self) -> None:
        results = self._find(chain="ethereum")
        # PEPE/WETH is only on uniswap
        self.assertNotIn("PEPE/WETH", results)

    def test_no_query_returns_empty(self) -> None:
        results = find_cross_dex_pairs()
        self.assertEqual(results, {})


class NormalizeSymbolTests(unittest.TestCase):
    def test_weth_variants(self) -> None:
        self.assertEqual(_normalize_symbol("WETH"), "WETH")
        self.assertEqual(_normalize_symbol("weth"), "WETH")
        self.assertEqual(_normalize_symbol("ETH"), "WETH")

    def test_wbtc_variants(self) -> None:
        self.assertEqual(_normalize_symbol("WBTC"), "WBTC")
        self.assertEqual(_normalize_symbol("BTC"), "WBTC")

    def test_other_tokens_unchanged(self) -> None:
        self.assertEqual(_normalize_symbol("USDC"), "USDC")
        self.assertEqual(_normalize_symbol("pepe"), "PEPE")


if __name__ == "__main__":
    unittest.main()
