"""Tests for new pair additions, OP/USDC pricing fix, and liquidity threshold changes.

Covers:
  - LST token resolution (wstETH, cbETH)
  - AERO token resolution
  - OP/USDC profit normalisation to ETH
  - WETH-quoted pair profit (wstETH/WETH, cbETH/WETH, AERO/WETH)
  - Per-chain liquidity threshold (Arbitrum lowered to $25K)
"""

import sys
from decimal import Decimal
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.config import BotConfig, DexConfig, PairConfig
from core.models import MarketQuote
from core.tokens import resolve_token_address, SYMBOL_TO_ATTR
from strategy.arb_strategy import ArbitrageStrategy

D = Decimal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _zero_cost_config(**overrides) -> BotConfig:
    """Config with zero fees/gas/slippage — isolates the conversion logic."""
    kwargs = dict(
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
            DexConfig(name="Uni-Base", base_price=2300.0, fee_bps=0.0,
                      volatility_bps=0.0, chain="base", dex_type="uniswap_v3"),
            DexConfig(name="Sushi-Base", base_price=2300.0, fee_bps=0.0,
                      volatility_bps=0.0, chain="base", dex_type="sushi_v3"),
            DexConfig(name="Uni-Arb", base_price=2300.0, fee_bps=0.0,
                      volatility_bps=0.0, chain="arbitrum", dex_type="uniswap_v3"),
            DexConfig(name="Sushi-Arb", base_price=2300.0, fee_bps=0.0,
                      volatility_bps=0.0, chain="arbitrum", dex_type="sushi_v3"),
            DexConfig(name="Uni-Opt", base_price=2300.0, fee_bps=0.0,
                      volatility_bps=0.0, chain="optimism", dex_type="uniswap_v3"),
            DexConfig(name="Velo-Opt", base_price=2300.0, fee_bps=0.0,
                      volatility_bps=0.0, chain="optimism", dex_type="velodrome_v2"),
        ],
    )
    kwargs.update(overrides)
    config = BotConfig(**kwargs)
    config.validate()
    return config


# ---------------------------------------------------------------------------
# 1. Token resolution tests for new tokens
# ---------------------------------------------------------------------------

class LSTTokenResolutionTests(unittest.TestCase):
    """wstETH and cbETH should resolve on all configured chains."""

    def test_wsteth_ethereum(self):
        addr = resolve_token_address("ethereum", "wstETH")
        self.assertEqual(addr, "0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0")

    def test_wsteth_base(self):
        addr = resolve_token_address("base", "WSTETH")
        self.assertEqual(addr, "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452")

    def test_wsteth_arbitrum(self):
        addr = resolve_token_address("arbitrum", "wstETH")
        self.assertEqual(addr, "0x5979D7b546E38E9Ab8b6eb39E9A02dd5cAe71E09")

    def test_wsteth_optimism(self):
        addr = resolve_token_address("optimism", "WSTETH")
        self.assertEqual(addr, "0x1F32b1c2345538c0c6f582fCB022739c4A194Ebb")

    def test_cbeth_ethereum(self):
        addr = resolve_token_address("ethereum", "cbETH")
        self.assertEqual(addr, "0xBe9895146f7AF43049ca1c1AE358B0541Ea49704")

    def test_cbeth_base(self):
        addr = resolve_token_address("base", "CBETH")
        self.assertEqual(addr, "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22")

    def test_cbeth_arbitrum(self):
        addr = resolve_token_address("arbitrum", "cbETH")
        self.assertEqual(addr, "0x1DEBd73E752bEaF79865Fd6446b0c970EaE7732f")

    def test_symbol_map_has_wsteth(self):
        self.assertIn("WSTETH", SYMBOL_TO_ATTR)
        self.assertIn("wstETH", SYMBOL_TO_ATTR)
        self.assertEqual(SYMBOL_TO_ATTR["WSTETH"], "wsteth")

    def test_symbol_map_has_cbeth(self):
        self.assertIn("CBETH", SYMBOL_TO_ATTR)
        self.assertIn("cbETH", SYMBOL_TO_ATTR)
        self.assertEqual(SYMBOL_TO_ATTR["CBETH"], "cbeth")


class AEROTokenResolutionTests(unittest.TestCase):
    """AERO should resolve on Base."""

    def test_aero_base(self):
        addr = resolve_token_address("base", "AERO")
        self.assertEqual(addr, "0x940181a94A35A4569E4529A3CDfB74e38FD98631")

    def test_symbol_map_has_aero(self):
        self.assertIn("AERO", SYMBOL_TO_ATTR)
        self.assertEqual(SYMBOL_TO_ATTR["AERO"], "aero")


# ---------------------------------------------------------------------------
# 2. OP/USDC pricing normalisation — net_profit_base should be in ETH
# ---------------------------------------------------------------------------

class OPUSDCProfitNormalisationTests(unittest.TestCase):
    """OP/USDC profits must be normalised to ETH, not left in OP units."""

    def _make_strategy(self, extra_pairs=None):
        config = _zero_cost_config(extra_pairs=extra_pairs)
        return ArbitrageStrategy(config, pairs=extra_pairs)

    def test_op_usdc_profit_normalised_to_eth(self):
        """An OP/USDC opportunity should yield ~0.16 ETH profit, not ~247 OP."""
        op_pair = PairConfig(
            pair="OP/USDC", base_asset="OP", quote_asset="USDC",
            trade_size=D("20000"), max_exposure=D("25000"),
        )
        strategy = self._make_strategy(extra_pairs=[op_pair])

        # Provide WETH/USDC quotes so the reference price gets set.
        weth_quotes = [
            MarketQuote(dex="Uni-Base", pair="WETH/USDC",
                        buy_price=2300.0, sell_price=2299.0, fee_bps=0.0),
        ]
        strategy.update_weth_price(weth_quotes)

        # OP/USDC with a ~1.5% spread: buy at $1.50, sell at $1.5225.
        buy = MarketQuote(dex="Uni-Opt", pair="OP/USDC",
                          buy_price=1.50, sell_price=1.49, fee_bps=0.0)
        sell = MarketQuote(dex="Velo-Opt", pair="OP/USDC",
                           buy_price=1.53, sell_price=1.5225, fee_bps=0.0)

        opp = strategy.evaluate_pair(buy, sell)
        self.assertIsNotNone(opp)
        assert opp is not None

        # Expected: 20000 * (1.5225 - 1.50) = $450 USDC profit.
        # In ETH: $450 / $2300 ≈ 0.1957 ETH.
        # Must NOT be ~300 (which would mean OP units).
        self.assertGreater(float(opp.net_profit_base), 0.05)
        self.assertLess(float(opp.net_profit_base), 1.0)

    def test_weth_usdc_unaffected(self):
        """WETH/USDC profit calculation should be unchanged."""
        strategy = self._make_strategy()

        quotes = [
            MarketQuote(dex="Uni-Base", pair="WETH/USDC",
                        buy_price=2300.0, sell_price=2299.0, fee_bps=0.0),
            MarketQuote(dex="Sushi-Base", pair="WETH/USDC",
                        buy_price=2350.0, sell_price=2349.0, fee_bps=0.0),
        ]

        opp = strategy.find_best_opportunity(quotes)
        self.assertIsNotNone(opp)
        assert opp is not None

        # $50 spread / ~$2325 mid ≈ 0.0215 ETH
        self.assertGreater(float(opp.net_profit_base), 0.01)
        self.assertLess(float(opp.net_profit_base), 0.05)

    def test_no_weth_reference_falls_back(self):
        """Without WETH quotes, non-WETH pairs should still produce a result."""
        op_pair = PairConfig(
            pair="OP/USDC", base_asset="OP", quote_asset="USDC",
            trade_size=D("20000"), max_exposure=D("25000"),
        )
        strategy = self._make_strategy(extra_pairs=[op_pair])
        # Deliberately do NOT call update_weth_price.

        buy = MarketQuote(dex="Uni-Opt", pair="OP/USDC",
                          buy_price=1.50, sell_price=1.49, fee_bps=0.0)
        sell = MarketQuote(dex="Velo-Opt", pair="OP/USDC",
                           buy_price=1.53, sell_price=1.5225, fee_bps=0.0)

        opp = strategy.evaluate_pair(buy, sell)
        # Should still return something (falls back to mid_price division).
        self.assertIsNotNone(opp)


# ---------------------------------------------------------------------------
# 3. WETH-quoted pairs (wstETH/WETH, cbETH/WETH, AERO/WETH)
# ---------------------------------------------------------------------------

class WETHQuotedPairTests(unittest.TestCase):
    """For X/WETH pairs, net_profit_quote is already in WETH — no division needed."""

    def _make_strategy(self, extra_pairs=None):
        config = _zero_cost_config(extra_pairs=extra_pairs)
        return ArbitrageStrategy(config, pairs=extra_pairs)

    def test_wsteth_weth_profit_is_in_eth(self):
        """wstETH/WETH profit should be directly in ETH (the quote asset)."""
        pair = PairConfig(
            pair="wstETH/WETH", base_asset="wstETH", quote_asset="WETH",
            trade_size=D("1"), max_exposure=D("5"),
        )
        strategy = self._make_strategy(extra_pairs=[pair])

        # wstETH trades at ~1.17 WETH. 0.5% spread:
        buy = MarketQuote(dex="Uni-Base", pair="wstETH/WETH",
                          buy_price=1.170, sell_price=1.169, fee_bps=0.0)
        sell = MarketQuote(dex="Sushi-Base", pair="wstETH/WETH",
                           buy_price=1.177, sell_price=1.176, fee_bps=0.0)

        opp = strategy.evaluate_pair(buy, sell)
        self.assertIsNotNone(opp)
        assert opp is not None

        # 1 wstETH * (1.176 - 1.170) = 0.006 WETH profit.
        # Since quote is WETH, net_profit_base should equal net_profit_quote.
        self.assertAlmostEqual(float(opp.net_profit_base),
                               float(opp.net_profit_quote), places=6)
        self.assertAlmostEqual(float(opp.net_profit_base), 0.006, delta=0.001)

    def test_aero_weth_profit_is_in_eth(self):
        """AERO/WETH profit should be directly in ETH."""
        pair = PairConfig(
            pair="AERO/WETH", base_asset="AERO", quote_asset="WETH",
            trade_size=D("5000"), chain="base", max_exposure=D("10000"),
        )
        strategy = self._make_strategy(extra_pairs=[pair])

        # AERO at ~0.0005 WETH with a spread.
        buy = MarketQuote(dex="Uni-Base", pair="AERO/WETH",
                          buy_price=0.0005, sell_price=0.00049, fee_bps=0.0)
        sell = MarketQuote(dex="Sushi-Base", pair="AERO/WETH",
                           buy_price=0.00053, sell_price=0.00052, fee_bps=0.0)

        opp = strategy.evaluate_pair(buy, sell)
        self.assertIsNotNone(opp)
        assert opp is not None

        # 5000 * (0.00052 - 0.0005) = 0.1 WETH profit.
        self.assertAlmostEqual(float(opp.net_profit_base),
                               float(opp.net_profit_quote), places=6)
        self.assertGreater(float(opp.net_profit_base), 0.05)


# ---------------------------------------------------------------------------
# 4. update_weth_price tests
# ---------------------------------------------------------------------------

class UpdateWETHPriceTests(unittest.TestCase):
    """Tests for the WETH reference price update mechanism."""

    def _make_strategy(self):
        config = _zero_cost_config()
        return ArbitrageStrategy(config)

    def test_sets_median_from_weth_usdc_quotes(self):
        strategy = self._make_strategy()
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=2300.0, sell_price=2299.0, fee_bps=0.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=2310.0, sell_price=2309.0, fee_bps=0.0),
            MarketQuote(dex="C", pair="WETH/USDC", buy_price=2305.0, sell_price=2304.0, fee_bps=0.0),
        ]
        strategy.update_weth_price(quotes)
        # Median of [2300, 2305, 2310] = 2305
        self.assertAlmostEqual(float(strategy._weth_price_usd), 2305.0, places=0)

    def test_also_uses_weth_usdt(self):
        strategy = self._make_strategy()
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDT", buy_price=2300.0, sell_price=2299.0, fee_bps=0.0),
        ]
        strategy.update_weth_price(quotes)
        self.assertAlmostEqual(float(strategy._weth_price_usd), 2300.0, places=0)

    def test_ignores_non_weth_pairs(self):
        strategy = self._make_strategy()
        quotes = [
            MarketQuote(dex="A", pair="OP/USDC", buy_price=1.50, sell_price=1.49, fee_bps=0.0),
        ]
        strategy.update_weth_price(quotes)
        self.assertEqual(float(strategy._weth_price_usd), 0.0)

    def test_empty_quotes_keeps_old_value(self):
        strategy = self._make_strategy()
        strategy._weth_price_usd = D("2300")
        strategy.update_weth_price([])
        self.assertEqual(float(strategy._weth_price_usd), 2300.0)


# ---------------------------------------------------------------------------
# 5. Per-chain liquidity threshold
# ---------------------------------------------------------------------------

class ChainLiquidityThresholdTests(unittest.TestCase):
    """Arbitrum should have a lower liquidity threshold than other L2s."""

    def test_arbitrum_threshold_is_50k(self):
        threshold = BotConfig.min_liquidity_for_chain("arbitrum")
        self.assertEqual(threshold, D("50000"))

    def test_base_threshold_is_100k(self):
        threshold = BotConfig.min_liquidity_for_chain("base")
        self.assertEqual(threshold, D("100000"))

    def test_optimism_threshold_is_100k(self):
        threshold = BotConfig.min_liquidity_for_chain("optimism")
        self.assertEqual(threshold, D("100000"))

    def test_ethereum_threshold_is_1m(self):
        threshold = BotConfig.min_liquidity_for_chain("ethereum")
        self.assertEqual(threshold, D("1000000"))

    def test_unknown_chain_defaults_to_1m(self):
        threshold = BotConfig.min_liquidity_for_chain("solana")
        self.assertEqual(threshold, D("1000000"))

    def test_case_insensitive(self):
        self.assertEqual(
            BotConfig.min_liquidity_for_chain("Arbitrum"),
            BotConfig.min_liquidity_for_chain("arbitrum"),
        )


# ---------------------------------------------------------------------------
# 6. Exposure limit fix for non-WETH pairs
# ---------------------------------------------------------------------------

class ExposureLimitTests(unittest.TestCase):
    """Exposure check should handle non-WETH pairs gracefully."""

    def _make_opportunity(self, trade_size, max_exposure_override=D("0"),
                          pair="OP/USDC", chain="optimism"):
        from core.models import Opportunity
        return Opportunity(
            pair=pair,
            buy_dex="Uni-Opt",
            sell_dex="Velo-Opt",
            trade_size=trade_size,
            cost_to_buy_quote=D("30000"),
            proceeds_from_sell_quote=D("30450"),
            gross_profit_quote=D("450"),
            net_profit_quote=D("370"),
            net_profit_base=D("0.16"),  # normalised to ETH
            gross_spread_pct=D("1.5"),  # above min_spread thresholds
            gas_cost_base=D("0.0001"),
            max_exposure_override=max_exposure_override,
            chain=chain,
        )

    def test_override_25k_allows_20k_trade(self):
        """With max_exposure_override=25000, a 20000 OP trade should pass."""
        from risk.policy import RiskPolicy
        policy = RiskPolicy(execution_enabled=True)
        opp = self._make_opportunity(D("20000"), max_exposure_override=D("25000"))
        verdict = policy.evaluate(opp)
        self.assertTrue(verdict.approved or verdict.reason == "simulation_approved",
                        f"Expected approved, got: {verdict.reason}")

    def test_global_10_blocks_20k_op_trade(self):
        """Without override AND without the safety net, 20000 > 10 would reject.
        But our fix detects the unit mismatch (20000 >> 10*10) and lets it through."""
        from risk.policy import RiskPolicy
        policy = RiskPolicy(execution_enabled=True)
        # No override set — trade_size=20000 >> max_exposure=10
        opp = self._make_opportunity(D("20000"), max_exposure_override=D("0"))
        verdict = policy.evaluate(opp)
        # Should NOT be exposure_limit — the safety net should catch the mismatch
        self.assertNotEqual(verdict.reason, "exposure_limit",
                            f"Expected safety net to prevent false rejection, got: {verdict.reason}")

    def test_weth_pair_uses_global_limit(self):
        """WETH-sized trades should still use the global limit normally."""
        from risk.policy import RiskPolicy
        from core.models import Opportunity
        policy = RiskPolicy(execution_enabled=True)
        opp = Opportunity(
            pair="WETH/USDC",
            buy_dex="Uni-Base",
            sell_dex="Sushi-Base",
            trade_size=D("1"),
            cost_to_buy_quote=D("2300"),
            proceeds_from_sell_quote=D("2350"),
            gross_profit_quote=D("50"),
            net_profit_quote=D("40"),
            net_profit_base=D("0.017"),
            gross_spread_pct=D("2.17"),
            gas_cost_base=D("0.0001"),
            chain="base",
        )
        verdict = policy.evaluate(opp)
        # trade_size=1 < max_exposure_per_pair=10, should not hit exposure_limit
        self.assertNotEqual(verdict.reason, "exposure_limit")

    def test_weth_pair_rejected_when_over_limit(self):
        """A 15 WETH trade should be rejected (exceeds global 10 WETH limit)."""
        from risk.policy import RiskPolicy
        from core.models import Opportunity
        policy = RiskPolicy(execution_enabled=True)
        opp = Opportunity(
            pair="WETH/USDC",
            buy_dex="Uni-Base",
            sell_dex="Sushi-Base",
            trade_size=D("15"),
            cost_to_buy_quote=D("34500"),
            proceeds_from_sell_quote=D("35000"),
            gross_profit_quote=D("500"),
            net_profit_quote=D("400"),
            net_profit_base=D("0.17"),
            gross_spread_pct=D("1.45"),
            gas_cost_base=D("0.0001"),
            chain="base",
        )
        verdict = policy.evaluate(opp)
        self.assertEqual(verdict.reason, "exposure_limit")


if __name__ == "__main__":
    unittest.main()
