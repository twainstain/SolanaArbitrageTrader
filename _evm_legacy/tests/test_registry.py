"""Tests for the pair registry."""

import sys
from decimal import Decimal
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from registry.pairs import (
    PairRegistry, PairEntry, PoolEntry,
    LiquidityClass, RiskCategory,
)

D = Decimal


def _make_pool(dex="Uniswap", chain="ethereum", fee=D("30"), enabled=True) -> PoolEntry:
    return PoolEntry(
        pool_address="0x" + "ab" * 20, dex=dex, chain=chain,
        fee_tier_bps=fee, dex_type="uniswap_v3",
        liquidity_class=LiquidityClass.HIGH, enabled=enabled,
    )


def _make_pair(pair="WETH/USDC", chain="ethereum", pools=None) -> PairEntry:
    return PairEntry(
        pair=pair, base_asset="WETH", quote_asset="USDC",
        base_decimals=18, quote_decimals=6, chain=chain,
        pools=tuple(pools) if pools else (_make_pool(),),
    )


class RegistryBasicTests(unittest.TestCase):
    def test_register_and_get(self):
        reg = PairRegistry()
        entry = _make_pair()
        reg.register(entry)
        self.assertEqual(reg.pair_count, 1)
        got = reg.get("WETH/USDC")
        self.assertEqual(got.pair, "WETH/USDC")

    def test_get_nonexistent_returns_none(self):
        reg = PairRegistry()
        self.assertIsNone(reg.get("FAKE/PAIR"))

    def test_all_pairs(self):
        reg = PairRegistry()
        reg.register(_make_pair("WETH/USDC"))
        reg.register(_make_pair("WBTC/USDC"))
        self.assertEqual(len(reg.all_pairs()), 2)

    def test_remove(self):
        reg = PairRegistry()
        reg.register(_make_pair())
        self.assertTrue(reg.remove("WETH/USDC"))
        self.assertEqual(reg.pair_count, 0)
        self.assertFalse(reg.remove("WETH/USDC"))

    def test_pool_count(self):
        reg = PairRegistry()
        reg.register(_make_pair(pools=[_make_pool("A"), _make_pool("B")]))
        self.assertEqual(reg.pool_count, 2)


class EnabledFilterTests(unittest.TestCase):
    def test_enabled_pairs_filters_disabled_pools(self):
        reg = PairRegistry()
        reg.register(_make_pair("GOOD", pools=[_make_pool(enabled=True)]))
        reg.register(_make_pair("BAD", pools=[_make_pool(enabled=False)]))
        enabled = reg.enabled_pairs()
        self.assertEqual(len(enabled), 1)
        self.assertEqual(enabled[0].pair, "GOOD")

    def test_pools_for_pair_filters_disabled(self):
        reg = PairRegistry()
        reg.register(_make_pair(pools=[
            _make_pool("Uni", enabled=True),
            _make_pool("Sushi", enabled=False),
        ]))
        pools = reg.pools_for_pair("WETH/USDC")
        self.assertEqual(len(pools), 1)
        self.assertEqual(pools[0].dex, "Uni")


class ChainFilterTests(unittest.TestCase):
    def test_pairs_on_chain(self):
        reg = PairRegistry()
        reg.register(_make_pair("ETH_PAIR", chain="ethereum"))
        reg.register(_make_pair("ARB_PAIR", chain="arbitrum"))
        eth = reg.pairs_on_chain("ethereum")
        self.assertEqual(len(eth), 1)
        self.assertEqual(eth[0].pair, "ETH_PAIR")


class DefaultRegistryTests(unittest.TestCase):
    def test_default_ethereum_has_three_pairs(self):
        reg = PairRegistry.default_ethereum()
        self.assertEqual(reg.pair_count, 3)
        self.assertIsNotNone(reg.get("WETH/USDC"))
        self.assertIsNotNone(reg.get("WETH/USDT"))
        self.assertIsNotNone(reg.get("WBTC/USDC"))

    def test_weth_usdc_has_multiple_pools(self):
        reg = PairRegistry.default_ethereum()
        pools = reg.pools_for_pair("WETH/USDC")
        self.assertGreater(len(pools), 1)

    def test_all_are_blue_chip(self):
        reg = PairRegistry.default_ethereum()
        for pair in reg.all_pairs():
            self.assertEqual(pair.risk_category, RiskCategory.BLUE_CHIP)


class MetadataTests(unittest.TestCase):
    def test_liquidity_class(self):
        pool = _make_pool()
        self.assertEqual(pool.liquidity_class, LiquidityClass.HIGH)

    def test_risk_category(self):
        entry = _make_pair()
        self.assertEqual(entry.risk_category, RiskCategory.BLUE_CHIP)

    def test_max_trade_size_default(self):
        entry = _make_pair()
        self.assertEqual(entry.max_trade_size, D("10"))


if __name__ == "__main__":
    unittest.main()
