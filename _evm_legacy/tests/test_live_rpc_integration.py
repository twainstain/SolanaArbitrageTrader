"""Integration tests that hit live RPC endpoints.

These tests are SKIPPED by default. Run them manually with:
    python3.11 -m pytest tests/test_live_rpc_integration.py -v --run-live

Requires:
    - Internet connectivity
    - Public RPCs are rate-limited — don't run in CI
"""

import os
import sys
from pathlib import Path
from decimal import Decimal
import unittest

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Skip unless --run-live flag is passed via conftest or env var.
SKIP_LIVE = not (os.environ.get("RUN_LIVE_TESTS") == "1")
skip_reason = "Live RPC tests skipped — set RUN_LIVE_TESTS=1 to enable"

D = Decimal


def _make_single_dex_config(dex_name, chain, dex_type, fee_bps, pair="WETH/USDC",
                             base="WETH", quote="USDC", trade_size=1.0):
    from core.config import BotConfig, DexConfig
    # Need at least 2 DEXes for config validation; add a dummy.
    config = BotConfig(
        pair=pair, base_asset=base, quote_asset=quote,
        trade_size=trade_size, min_profit_base=0.0,
        estimated_gas_cost_base=0.0, flash_loan_fee_bps=9.0,
        flash_loan_provider="aave_v3", slippage_bps=10.0,
        poll_interval_seconds=0.0,
        dexes=[
            DexConfig(name=dex_name, base_price=0, fee_bps=fee_bps,
                      volatility_bps=0, chain=chain, dex_type=dex_type),
            DexConfig(name="Dummy", base_price=0, fee_bps=30.0,
                      volatility_bps=0, chain=chain, dex_type="uniswap_v3"),
        ],
    )
    config.validate()
    return config


@pytest.mark.skipif(SKIP_LIVE, reason=skip_reason)
class UniswapV3LiveTests(unittest.TestCase):
    """Verify Uniswap V3 quoter returns sane prices on Ethereum."""

    def test_ethereum_weth_usdc(self):
        from market.onchain_market import OnChainMarket
        config = _make_single_dex_config("Uniswap-Ethereum", "ethereum", "uniswap_v3", 5.0)
        market = OnChainMarket(config)
        price, fee = market._quote_uniswap_v3(
            "ethereum",
            "0xC02aaA39b223FE8D0A0e5c4F27eAD9083C756Cc2",
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            "WETH", "USDC",
        )
        self.assertGreater(price, D("500"))
        self.assertLess(price, D("50000"))
        self.assertGreater(fee, 0)

    def test_arbitrum_weth_usdc(self):
        from market.onchain_market import OnChainMarket
        config = _make_single_dex_config("Uniswap-Arbitrum", "arbitrum", "uniswap_v3", 5.0)
        market = OnChainMarket(config)
        price, fee = market._quote_uniswap_v3(
            "arbitrum",
            "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
            "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
            "WETH", "USDC",
        )
        self.assertGreater(price, D("500"))
        self.assertLess(price, D("50000"))


@pytest.mark.skipif(SKIP_LIVE, reason=skip_reason)
class VelodromeLiveTests(unittest.TestCase):
    """Verify Velodrome V2 quoter on Optimism returns quotes."""

    def test_optimism_op_usdc(self):
        from market.onchain_market import OnChainMarket
        from core.tokens import resolve_token_address
        config = _make_single_dex_config(
            "Velodrome-Optimism", "optimism", "velodrome_v2", 20.0,
            pair="OP/USDC", base="OP", quote="USDC", trade_size=250.0,
        )
        market = OnChainMarket(config)
        op_addr = resolve_token_address("optimism", "OP")
        usdc_addr = resolve_token_address("optimism", "USDC")
        price, fee = market._quote_velodrome(
            "optimism", op_addr, usdc_addr,
            "velodrome_v2", "OP", "USDC",
        )
        # OP should be $0.50 - $20 range
        self.assertGreater(price, D("0.1"))
        self.assertLess(price, D("100"))

    def test_optimism_weth_usdc_with_bridged_fallback(self):
        from market.onchain_market import OnChainMarket
        from core.tokens import resolve_token_address
        config = _make_single_dex_config(
            "Velodrome-Optimism", "optimism", "velodrome_v2", 20.0,
        )
        market = OnChainMarket(config)
        weth_addr = resolve_token_address("optimism", "WETH")
        usdc_addr = resolve_token_address("optimism", "USDC")
        # This should fall back to USDC.e if native USDC pool is empty
        price, fee = market._quote_velodrome(
            "optimism", weth_addr, usdc_addr,
            "velodrome_v2", "WETH", "USDC",
        )
        self.assertGreater(price, D("500"))
        self.assertLess(price, D("50000"))


@pytest.mark.skipif(SKIP_LIVE, reason=skip_reason)
class AerodromeLiveTests(unittest.TestCase):
    """Verify Aerodrome quoter on Base returns quotes."""

    def test_base_weth_usdc(self):
        from market.onchain_market import OnChainMarket
        from core.tokens import resolve_token_address
        config = _make_single_dex_config(
            "Aerodrome-Base", "base", "aerodrome", 20.0,
        )
        market = OnChainMarket(config)
        weth_addr = resolve_token_address("base", "WETH")
        usdc_addr = resolve_token_address("base", "USDC")
        price, fee = market._quote_velodrome(
            "base", weth_addr, usdc_addr,
            "aerodrome", "WETH", "USDC",
        )
        self.assertGreater(price, D("500"))
        self.assertLess(price, D("50000"))


@pytest.mark.skipif(SKIP_LIVE, reason=skip_reason)
class CamelotLiveTests(unittest.TestCase):
    """Verify Camelot V3 quoter on Arbitrum returns quotes."""

    def test_arbitrum_weth_usdc(self):
        from market.onchain_market import OnChainMarket
        from core.tokens import resolve_token_address
        config = _make_single_dex_config(
            "Camelot-Arbitrum", "arbitrum", "camelot_v3", 15.0,
        )
        market = OnChainMarket(config)
        weth_addr = resolve_token_address("arbitrum", "WETH")
        usdc_addr = resolve_token_address("arbitrum", "USDC")
        price, fee = market._quote_camelot_v3(
            "arbitrum", weth_addr, usdc_addr, "WETH", "USDC",
        )
        self.assertGreater(price, D("500"))
        self.assertLess(price, D("50000"))


@pytest.mark.skipif(SKIP_LIVE, reason=skip_reason)
class LiquidityEstimationLiveTests(unittest.TestCase):
    """Verify TVL estimation produces reasonable values on live pools."""

    def test_uniswap_ethereum_deep_pool(self):
        from market.onchain_market import OnChainMarket
        from core.tokens import resolve_token_address
        config = _make_single_dex_config("Uniswap-Ethereum", "ethereum", "uniswap_v3", 5.0)
        market = OnChainMarket(config)
        weth = resolve_token_address("ethereum", "WETH")
        usdc = resolve_token_address("ethereum", "USDC")
        price, _ = market._quote_uniswap_v3("ethereum", weth, usdc, "WETH", "USDC")
        tvl = market._estimate_liquidity_usd(
            "ethereum", weth, usdc, "uniswap_v3", "WETH", "USDC", price,
        )
        # Uniswap V3 WETH/USDC on Ethereum is one of the deepest pools
        self.assertGreater(tvl, D("1000000"))  # should be >$1M


@pytest.mark.skipif(SKIP_LIVE, reason=skip_reason)
class PoolDiscoveryLiveTests(unittest.TestCase):
    """Verify factory pool discovery works against real chain."""

    def test_discover_uniswap_v3_pools_ethereum(self):
        from web3 import Web3
        from registry.pool_discovery import discover_uniswap_v3_pools
        from core.contracts import PUBLIC_RPC_URLS
        w3 = Web3(Web3.HTTPProvider(PUBLIC_RPC_URLS["ethereum"], request_kwargs={"timeout": 15}))
        pools = discover_uniswap_v3_pools(
            w3, "ethereum",
            "0xC02aaA39b223FE8D0A0e5c4F27eAD9083C756Cc2",  # WETH
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
        )
        self.assertGreater(len(pools), 0)
        for p in pools:
            self.assertTrue(p["address"].startswith("0x"))
            self.assertNotEqual(p["address"], "0x" + "0" * 40)


if __name__ == "__main__":
    unittest.main()
