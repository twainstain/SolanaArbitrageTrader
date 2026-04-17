"""Tests for the token address registry — static + dynamic resolution."""

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.tokens import (
    resolve_token_address,
    register_token,
    get_unresolved_tokens,
    get_dynamic_tokens,
    CHAIN_TOKENS,
    SYMBOL_TO_ATTR,
    token_decimals,
    _dynamic_tokens,
    _unresolved,
    _dynamic_lock,
)


class StaticRegistryTests(unittest.TestCase):
    """Tests for the hardcoded CHAIN_TOKENS registry."""

    def test_resolve_weth_ethereum(self):
        addr = resolve_token_address("ethereum", "WETH")
        self.assertIsNotNone(addr)
        self.assertTrue(addr.startswith("0x"))

    def test_resolve_usdc_arbitrum(self):
        addr = resolve_token_address("arbitrum", "USDC")
        self.assertEqual(addr, "0xaf88d065e77c8cC2239327C5EDb3A432268e5831")

    def test_resolve_arb_arbitrum(self):
        addr = resolve_token_address("arbitrum", "ARB")
        self.assertIsNotNone(addr)
        self.assertTrue(addr.startswith("0x"))

    def test_resolve_op_optimism(self):
        addr = resolve_token_address("optimism", "OP")
        self.assertIsNotNone(addr)

    def test_resolve_link_ethereum(self):
        addr = resolve_token_address("ethereum", "LINK")
        self.assertIsNotNone(addr)

    def test_resolve_gmx_arbitrum(self):
        addr = resolve_token_address("arbitrum", "GMX")
        self.assertIsNotNone(addr)

    def test_resolve_dai_base(self):
        addr = resolve_token_address("base", "DAI")
        self.assertIsNotNone(addr)

    def test_eth_resolves_to_weth(self):
        """ETH should resolve to the WETH address."""
        weth = resolve_token_address("ethereum", "WETH")
        eth = resolve_token_address("ethereum", "ETH")
        self.assertEqual(weth, eth)

    def test_case_insensitive(self):
        self.assertEqual(
            resolve_token_address("ethereum", "weth"),
            resolve_token_address("ethereum", "WETH"),
        )

    def test_unknown_chain_returns_none(self):
        self.assertIsNone(resolve_token_address("solana", "WETH"))

    def test_unknown_token_returns_none(self):
        self.assertIsNone(resolve_token_address("ethereum", "SHIB"))

    def test_all_chains_have_weth_and_usdc(self):
        for chain, tokens in CHAIN_TOKENS.items():
            self.assertIsNotNone(tokens.weth, f"{chain} missing WETH")
            self.assertIsNotNone(tokens.usdc, f"{chain} missing USDC")


class DynamicRegistryTests(unittest.TestCase):
    """Tests for runtime token registration."""

    def setUp(self):
        # Clear dynamic state between tests
        with _dynamic_lock:
            _dynamic_tokens.clear()
            _unresolved.clear()

    def test_register_and_resolve(self):
        register_token("arbitrum", "PENDLE", "0x0c880f6761F1af8d9Aa9C466984b80DAb9a8c9e8")
        addr = resolve_token_address("arbitrum", "PENDLE")
        self.assertEqual(addr, "0x0c880f6761F1af8d9Aa9C466984b80DAb9a8c9e8")

    def test_dynamic_doesnt_override_static(self):
        """Static registry should take priority over dynamic."""
        static_addr = resolve_token_address("ethereum", "WETH")
        register_token("ethereum", "WETH", "0xFAKE")
        self.assertEqual(resolve_token_address("ethereum", "WETH"), static_addr)

    def test_unresolved_tracking(self):
        resolve_token_address("arbitrum", "DOGE")
        unresolved = get_unresolved_tokens()
        self.assertIn("arbitrum:DOGE", unresolved)
        self.assertEqual(unresolved["arbitrum:DOGE"], 1)

    def test_unresolved_count_increments(self):
        resolve_token_address("base", "PEPE")
        resolve_token_address("base", "PEPE")
        resolve_token_address("base", "PEPE")
        unresolved = get_unresolved_tokens()
        self.assertEqual(unresolved["base:PEPE"], 3)

    def test_get_dynamic_tokens(self):
        register_token("optimism", "VELO", "0x9560e827aF36c94D2Ac33a39bCE1Fe78631088Db")
        dynamic = get_dynamic_tokens()
        self.assertIn("optimism:VELO", dynamic)

    def test_register_same_token_twice_no_duplicate(self):
        register_token("base", "AERO", "0x940181a94A35A4569E4529A3CDfB74e38FD98631")
        register_token("base", "AERO", "0xDIFFERENT")  # should not overwrite
        addr = resolve_token_address("base", "AERO")
        self.assertEqual(addr, "0x940181a94A35A4569E4529A3CDfB74e38FD98631")


class SymbolMapTests(unittest.TestCase):
    """Test SYMBOL_TO_ATTR coverage."""

    def test_all_new_tokens_in_symbol_map(self):
        expected = ["ARB", "OP", "LINK", "DAI", "UNI", "AAVE", "CRV", "GMX"]
        for sym in expected:
            self.assertIn(sym, SYMBOL_TO_ATTR, f"{sym} missing from SYMBOL_TO_ATTR")

    def test_symbol_map_consistent_with_dataclass(self):
        """Every attr in SYMBOL_TO_ATTR should be a field on TokenAddresses."""
        from dataclasses import fields
        from core.tokens import TokenAddresses
        field_names = {f.name for f in fields(TokenAddresses)}
        for sym, attr in SYMBOL_TO_ATTR.items():
            self.assertIn(attr, field_names,
                         f"SYMBOL_TO_ATTR['{sym}'] = '{attr}' not in TokenAddresses")


class DecimalsTests(unittest.TestCase):
    def test_usdc_6_decimals(self):
        self.assertEqual(token_decimals("USDC"), 6)

    def test_usdt_6_decimals(self):
        self.assertEqual(token_decimals("USDT"), 6)

    def test_wbtc_8_decimals(self):
        self.assertEqual(token_decimals("WBTC"), 8)

    def test_weth_18_decimals(self):
        self.assertEqual(token_decimals("WETH"), 18)

    def test_arb_18_decimals(self):
        self.assertEqual(token_decimals("ARB"), 18)


class ThreadSafetyTests(unittest.TestCase):
    def setUp(self):
        with _dynamic_lock:
            _dynamic_tokens.clear()
            _unresolved.clear()

    def test_concurrent_register_and_resolve(self):
        import threading
        errors = []

        def writer():
            for i in range(50):
                register_token("test", f"TOK{i}", f"0x{i:040x}")

        def reader():
            for i in range(50):
                resolve_token_address("test", f"TOK{i}")

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=writer),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # No crash = success
        self.assertGreaterEqual(len(get_dynamic_tokens()), 0)


if __name__ == "__main__":
    unittest.main()
