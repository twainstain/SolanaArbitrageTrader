from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arbitrage_bot.fork_scanner import find_forks, find_all_dex_forks, ForkInfo


def _mock_protocols() -> list[dict]:
    return [
        {"name": "SushiSwap", "slug": "sushiswap", "forkedFrom": "Uniswap V2",
         "category": "Dexs", "chains": ["Ethereum", "Arbitrum", "BSC"], "tvl": 500_000_000},
        {"name": "PancakeSwap", "slug": "pancakeswap", "forkedFrom": "Uniswap V2",
         "category": "Dexs", "chains": ["BSC", "Ethereum"], "tvl": 2_000_000_000},
        {"name": "Camelot", "slug": "camelot", "forkedFrom": "Uniswap V3",
         "category": "Dexs", "chains": ["Arbitrum"], "tvl": 80_000_000},
        {"name": "Quickswap V3", "slug": "quickswap-v3", "forkedFrom": "Uniswap V3",
         "category": "Dexs", "chains": ["Polygon"], "tvl": 50_000_000},
        {"name": "Aave V3 Fork", "slug": "aave-fork", "forkedFrom": "Aave V3",
         "category": "Lending", "chains": ["Ethereum"], "tvl": 10_000_000},
        {"name": "NoFork Protocol", "slug": "nofork", "forkedFrom": None,
         "category": "Dexs", "chains": ["Ethereum"], "tvl": 100_000_000},
        {"name": "TinyDex", "slug": "tinydex", "forkedFrom": "Uniswap V3",
         "category": "Dexs", "chains": ["Ethereum"], "tvl": 5_000},
    ]


def _patch_fetch(func, *args, **kwargs):
    import arbitrage_bot.fork_scanner as fs
    original = fs.fetch_all_protocols
    fs.fetch_all_protocols = lambda **kw: _mock_protocols()
    try:
        return func(*args, **kwargs)
    finally:
        fs.fetch_all_protocols = original


class FindForksTests(unittest.TestCase):
    def test_finds_uniswap_v3_forks(self) -> None:
        results = _patch_fetch(find_forks, parent="Uniswap V3")
        names = {f.name for f in results}
        self.assertIn("Camelot", names)
        self.assertIn("Quickswap V3", names)
        self.assertNotIn("SushiSwap", names)  # V2 fork, not V3

    def test_finds_uniswap_v2_forks(self) -> None:
        results = _patch_fetch(find_forks, parent="Uniswap V2")
        names = {f.name for f in results}
        self.assertIn("SushiSwap", names)
        self.assertIn("PancakeSwap", names)

    def test_filters_by_chain(self) -> None:
        results = _patch_fetch(find_forks, parent="Uniswap V3", chain="Arbitrum")
        names = {f.name for f in results}
        self.assertIn("Camelot", names)
        self.assertNotIn("Quickswap V3", names)  # Polygon only

    def test_filters_by_min_tvl(self) -> None:
        results = _patch_fetch(find_forks, parent="Uniswap V3", min_tvl=60_000_000)
        names = {f.name for f in results}
        self.assertIn("Camelot", names)      # 80M
        self.assertNotIn("TinyDex", names)   # 5K

    def test_excludes_non_forks(self) -> None:
        results = _patch_fetch(find_forks, parent="Uniswap")
        names = {f.name for f in results}
        self.assertNotIn("NoFork Protocol", names)

    def test_sorted_by_tvl_desc(self) -> None:
        results = _patch_fetch(find_forks, parent="Uniswap")
        tvls = [f.tvl for f in results]
        self.assertEqual(tvls, sorted(tvls, reverse=True))


class FindAllDexForksTests(unittest.TestCase):
    def test_finds_all_dex_forks(self) -> None:
        results = _patch_fetch(find_all_dex_forks)
        names = {f.name for f in results}
        self.assertIn("SushiSwap", names)
        self.assertIn("PancakeSwap", names)
        self.assertIn("Camelot", names)
        self.assertNotIn("Aave V3 Fork", names)  # Lending, not Dexes

    def test_filters_dex_forks_by_chain(self) -> None:
        results = _patch_fetch(find_all_dex_forks, chain="BSC")
        names = {f.name for f in results}
        self.assertIn("PancakeSwap", names)
        self.assertIn("SushiSwap", names)
        self.assertNotIn("Camelot", names)  # Arbitrum only


if __name__ == "__main__":
    unittest.main()
