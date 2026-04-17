"""Tests for fee_included flag — ensures on-chain quoter fees aren't double-counted.

Covers:
  - MarketQuote.fee_included field
  - strategy.evaluate_pair skips fee adjustment when fee_included=True
  - strategy.evaluate_pair applies fees normally when fee_included=False
  - Opportunity.fees_pre_included propagated from quotes
  - OnChainMarket sets fee_included=True on quotes
"""

import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.config import BotConfig, DexConfig
from core.models import MarketQuote, Opportunity, ZERO
from strategy.arb_strategy import ArbitrageStrategy

D = Decimal


def _make_config(**overrides) -> BotConfig:
    defaults = dict(
        pair="WETH/USDC",
        base_asset="WETH",
        quote_asset="USDC",
        trade_size=1.0,
        min_profit_base=0.001,
        estimated_gas_cost_base=0.0,
        flash_loan_fee_bps=0.0,
        flash_loan_provider="aave_v3",
        slippage_bps=0.0,
        poll_interval_seconds=0.0,
        dexes=[
            DexConfig(name="A", base_price=3000.0, fee_bps=30.0, volatility_bps=0.0),
            DexConfig(name="B", base_price=3050.0, fee_bps=30.0, volatility_bps=0.0),
        ],
    )
    defaults.update(overrides)
    config = BotConfig(**defaults)
    config.validate()
    return config


# ---------------------------------------------------------------------------
# MarketQuote.fee_included field
# ---------------------------------------------------------------------------

class MarketQuoteFeeIncludedTests(unittest.TestCase):
    def test_default_fee_included_is_false(self) -> None:
        q = MarketQuote(dex="A", pair="WETH/USDC", buy_price=3000, sell_price=2998, fee_bps=30)
        self.assertFalse(q.fee_included)

    def test_fee_included_true(self) -> None:
        q = MarketQuote(dex="A", pair="WETH/USDC", buy_price=3000, sell_price=3000,
                        fee_bps=5, fee_included=True)
        self.assertTrue(q.fee_included)
        # fee_bps should still carry the actual fee tier for display.
        self.assertEqual(q.fee_bps, D("5"))


# ---------------------------------------------------------------------------
# Strategy: fee_included=True skips fee deduction
# ---------------------------------------------------------------------------

class StrategyFeeIncludedTests(unittest.TestCase):
    """Verify evaluate_pair respects fee_included flag."""

    def test_fee_included_no_double_counting(self) -> None:
        """With fee_included=True, the quoted prices ARE the execution prices.
        No additional fee should be deducted."""
        config = _make_config(min_profit_base=0.0001)
        strategy = ArbitrageStrategy(config)

        # On-chain quotes: fees already baked in. buy and sell at same price
        # (quoter returns the exact output amount after fees).
        buy_q = MarketQuote(
            dex="A", pair="WETH/USDC",
            buy_price=2380, sell_price=2380,
            fee_bps=30, fee_included=True,
        )
        sell_q = MarketQuote(
            dex="B", pair="WETH/USDC",
            buy_price=2395, sell_price=2395,
            fee_bps=5, fee_included=True,
        )

        opp = strategy.evaluate_pair(buy_q, sell_q)
        self.assertIsNotNone(opp)
        assert opp is not None

        # Net profit should be close to the raw spread ($15) since no
        # additional fee deduction happens.  In base asset: ~15/2387.5 ≈ 0.00628
        self.assertGreater(opp.net_profit_base, D("0.005"))
        self.assertTrue(opp.fees_pre_included)

    def test_fee_not_included_deducts_fees(self) -> None:
        """With fee_included=False (default), fees ARE deducted by strategy."""
        config = _make_config(min_profit_base=0.0001)
        strategy = ArbitrageStrategy(config)

        buy_q = MarketQuote(
            dex="A", pair="WETH/USDC",
            buy_price=2380, sell_price=2378,
            fee_bps=30, fee_included=False,
        )
        sell_q = MarketQuote(
            dex="B", pair="WETH/USDC",
            buy_price=2397, sell_price=2395,
            fee_bps=30, fee_included=False,
        )

        opp = strategy.evaluate_pair(buy_q, sell_q)
        self.assertIsNotNone(opp)
        assert opp is not None

        # With 30 bps fees on both sides, profit should be reduced.
        self.assertFalse(opp.fees_pre_included)
        # dex_fee_cost_quote should be positive (fees were calculated).
        self.assertGreater(opp.dex_fee_cost_quote, ZERO)

    def test_fee_included_vs_not_profit_difference(self) -> None:
        """Same prices, fee_included=True should yield higher net profit
        than fee_included=False since no extra fees are deducted."""
        config = _make_config(min_profit_base=0.0001)
        strategy = ArbitrageStrategy(config)

        buy_q_incl = MarketQuote(
            dex="A", pair="WETH/USDC",
            buy_price=2380, sell_price=2380,
            fee_bps=30, fee_included=True,
        )
        sell_q_incl = MarketQuote(
            dex="B", pair="WETH/USDC",
            buy_price=2395, sell_price=2395,
            fee_bps=30, fee_included=True,
        )

        buy_q_raw = MarketQuote(
            dex="A", pair="WETH/USDC",
            buy_price=2380, sell_price=2380,
            fee_bps=30, fee_included=False,
        )
        sell_q_raw = MarketQuote(
            dex="B", pair="WETH/USDC",
            buy_price=2395, sell_price=2395,
            fee_bps=30, fee_included=False,
        )

        opp_incl = strategy.evaluate_pair(buy_q_incl, sell_q_incl)
        opp_raw = strategy.evaluate_pair(buy_q_raw, sell_q_raw)

        self.assertIsNotNone(opp_incl)
        self.assertIsNotNone(opp_raw)
        assert opp_incl is not None and opp_raw is not None

        # fee_included should yield higher profit (no fee deduction).
        self.assertGreater(opp_incl.net_profit_base, opp_raw.net_profit_base)

    def test_fee_included_zero_fee_bps_equivalent(self) -> None:
        """fee_included=True with fee_bps=30 should produce the same profit
        as fee_included=False with fee_bps=0 (both skip deduction)."""
        config = _make_config(min_profit_base=0.0001)
        strategy = ArbitrageStrategy(config)

        buy_q_incl = MarketQuote(
            dex="A", pair="WETH/USDC",
            buy_price=2380, sell_price=2380,
            fee_bps=30, fee_included=True,
        )
        sell_q_incl = MarketQuote(
            dex="B", pair="WETH/USDC",
            buy_price=2395, sell_price=2395,
            fee_bps=30, fee_included=True,
        )

        buy_q_zero = MarketQuote(
            dex="A", pair="WETH/USDC",
            buy_price=2380, sell_price=2380,
            fee_bps=0, fee_included=False,
        )
        sell_q_zero = MarketQuote(
            dex="B", pair="WETH/USDC",
            buy_price=2395, sell_price=2395,
            fee_bps=0, fee_included=False,
        )

        opp_incl = strategy.evaluate_pair(buy_q_incl, sell_q_incl)
        opp_zero = strategy.evaluate_pair(buy_q_zero, sell_q_zero)

        self.assertIsNotNone(opp_incl)
        self.assertIsNotNone(opp_zero)
        assert opp_incl is not None and opp_zero is not None

        # Both should have identical net profit (no fee deduction either way).
        self.assertAlmostEqual(
            float(opp_incl.net_profit_base),
            float(opp_zero.net_profit_base),
            places=6,
        )

    def test_dex_fee_cost_display_when_fee_included(self) -> None:
        """When fee_included=True, dex_fee_cost_quote should still be set
        (estimated from fee_bps for display) — not zero."""
        config = _make_config(min_profit_base=0.0001)
        strategy = ArbitrageStrategy(config)

        buy_q = MarketQuote(
            dex="A", pair="WETH/USDC",
            buy_price=2380, sell_price=2380,
            fee_bps=30, fee_included=True,
        )
        sell_q = MarketQuote(
            dex="B", pair="WETH/USDC",
            buy_price=2395, sell_price=2395,
            fee_bps=5, fee_included=True,
        )

        opp = strategy.evaluate_pair(buy_q, sell_q)
        self.assertIsNotNone(opp)
        assert opp is not None

        # dex_fee_cost_quote is an estimate for display, should be > 0.
        self.assertGreater(opp.dex_fee_cost_quote, ZERO)


# ---------------------------------------------------------------------------
# OnChainMarket: quotes have fee_included=True
# ---------------------------------------------------------------------------

class OnChainMarketFeeIncludedTests(unittest.TestCase):
    """Verify that OnChainMarket sets fee_included=True on all quotes."""

    def _build_quotes_with_mock(self) -> list:
        from market.onchain_market import OnChainMarket

        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=10.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Uniswap-Eth", base_price=0, fee_bps=30.0,
                          volatility_bps=0, chain="ethereum", dex_type="uniswap_v3"),
                DexConfig(name="Sushi-Eth", base_price=0, fee_bps=30.0,
                          volatility_bps=0, chain="ethereum", dex_type="sushi_v3"),
            ],
        )
        config.validate()

        mock_w3 = MagicMock()

        def make_contract_mock(price_per_weth: float) -> MagicMock:
            contract = MagicMock()
            def quote_fn(params):
                amount_in = params[2] if isinstance(params, (list, tuple)) else 10**18
                weth_count = amount_in / 10**18
                usdc_out = int(price_per_weth * weth_count * 10**6)
                result_mock = MagicMock()
                result_mock.call.return_value = [usdc_out, 0, 0, 150_000]
                return result_mock
            contract.functions.quoteExactInputSingle.side_effect = quote_fn
            return contract

        generic_contract = make_contract_mock(2200.0)
        mock_w3.eth.contract = lambda address, abi: generic_contract

        with patch("market.onchain_market.Web3") as MockWeb3:
            MockWeb3.HTTPProvider = MagicMock()
            MockWeb3.return_value = mock_w3
            MockWeb3.to_checksum_address = lambda x: x
            market = OnChainMarket(config)

        market._w3 = {"ethereum": mock_w3}

        import market.onchain_market as ocm
        original_web3 = ocm.Web3
        with patch("market.onchain_market.Web3") as MockWeb3:
            MockWeb3.to_checksum_address = lambda x: x
            ocm.Web3 = MockWeb3
            try:
                mock_w3.eth.contract = lambda address, abi: generic_contract
                quotes = market.get_quotes()
            finally:
                ocm.Web3 = original_web3

        return quotes

    def test_all_quotes_have_fee_included_true(self) -> None:
        quotes = self._build_quotes_with_mock()
        self.assertGreater(len(quotes), 0)
        for q in quotes:
            self.assertTrue(q.fee_included,
                            f"Quote from {q.dex} should have fee_included=True")

    def test_quotes_have_actual_fee_bps(self) -> None:
        """fee_bps should reflect the actual pool fee tier, not 0."""
        quotes = self._build_quotes_with_mock()
        for q in quotes:
            self.assertGreater(q.fee_bps, ZERO,
                               f"Quote from {q.dex} should have fee_bps > 0 for display")

    def test_buy_price_equals_sell_price(self) -> None:
        """On-chain quotes should have buy_price == sell_price (no synthetic spread)."""
        quotes = self._build_quotes_with_mock()
        for q in quotes:
            self.assertEqual(q.buy_price, q.sell_price,
                             f"Quote from {q.dex}: on-chain should have buy=sell")


# ---------------------------------------------------------------------------
# Opportunity.fees_pre_included field
# ---------------------------------------------------------------------------

class OpportunityFeeFieldTests(unittest.TestCase):
    def test_default_fees_pre_included_is_false(self) -> None:
        opp = Opportunity(
            pair="WETH/USDC", buy_dex="A", sell_dex="B",
            trade_size=1, cost_to_buy_quote=2380,
            proceeds_from_sell_quote=2395,
            gross_profit_quote=15, net_profit_quote=10,
            net_profit_base=D("0.004"),
        )
        self.assertFalse(opp.fees_pre_included)

    def test_fees_pre_included_set_true(self) -> None:
        opp = Opportunity(
            pair="WETH/USDC", buy_dex="A", sell_dex="B",
            trade_size=1, cost_to_buy_quote=2380,
            proceeds_from_sell_quote=2395,
            gross_profit_quote=15, net_profit_quote=10,
            net_profit_base=D("0.004"),
            fees_pre_included=True,
        )
        self.assertTrue(opp.fees_pre_included)


# ---------------------------------------------------------------------------
# Risk policy includes fee_included in analysis
# ---------------------------------------------------------------------------

class RiskPolicyFeeIncludedTests(unittest.TestCase):
    def test_analysis_includes_fee_included(self) -> None:
        from risk.policy import RiskPolicy

        policy = RiskPolicy(execution_enabled=False, min_net_profit=D("0.0001"))
        opp = Opportunity(
            pair="WETH/USDC", buy_dex="A", sell_dex="B",
            trade_size=1, cost_to_buy_quote=2380,
            proceeds_from_sell_quote=2395,
            gross_profit_quote=15, net_profit_quote=10,
            net_profit_base=D("0.004"),
            gross_spread_pct=D("0.63"),
            fees_pre_included=True,
        )
        verdict = policy.evaluate(opp)
        self.assertIn("fee_included", verdict.details)
        self.assertTrue(verdict.details["fee_included"])


if __name__ == "__main__":
    unittest.main()
