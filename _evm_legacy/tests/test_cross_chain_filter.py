"""Tests for cross-chain filtering and liquidity depth validation."""

import sys
from decimal import Decimal
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.models import Opportunity, MarketQuote, ZERO
from core.config import BotConfig, DexConfig
from strategy.scanner import OpportunityScanner

D = Decimal


class CrossChainDetectionTests(unittest.TestCase):
    """Test the is_cross_chain property on Opportunity."""

    def test_same_chain(self):
        opp = Opportunity(
            pair="WETH/USDC", buy_dex="Uniswap-Ethereum", sell_dex="Sushi-Ethereum",
            trade_size=D("1"), cost_to_buy_quote=D("2200"),
            proceeds_from_sell_quote=D("2210"), gross_profit_quote=D("10"),
            net_profit_quote=D("8"), net_profit_base=D("0.005"),
        )
        self.assertFalse(opp.is_cross_chain)

    def test_cross_chain(self):
        opp = Opportunity(
            pair="WETH/USDC", buy_dex="Uniswap-Avalanche", sell_dex="Uniswap-Ethereum",
            trade_size=D("1"), cost_to_buy_quote=D("400"),
            proceeds_from_sell_quote=D("2200"), gross_profit_quote=D("1800"),
            net_profit_quote=D("1700"), net_profit_base=D("1.5"),
        )
        self.assertTrue(opp.is_cross_chain)

    def test_no_chain_suffix(self):
        opp = Opportunity(
            pair="WETH/USDC", buy_dex="A", sell_dex="B",
            trade_size=D("1"), cost_to_buy_quote=D("2200"),
            proceeds_from_sell_quote=D("2210"), gross_profit_quote=D("10"),
            net_profit_quote=D("8"), net_profit_base=D("0.005"),
        )
        # No "-Chain" suffix — can't determine, assume same chain.
        self.assertFalse(opp.is_cross_chain)

    def test_case_insensitive(self):
        opp = Opportunity(
            pair="WETH/USDC", buy_dex="Uniswap-ethereum", sell_dex="Sushi-Ethereum",
            trade_size=D("1"), cost_to_buy_quote=D("2200"),
            proceeds_from_sell_quote=D("2210"), gross_profit_quote=D("10"),
            net_profit_quote=D("8"), net_profit_base=D("0.005"),
        )
        self.assertFalse(opp.is_cross_chain)


class ScannerCrossChainFilterTests(unittest.TestCase):
    """Test that the scanner filters out cross-chain opportunities."""

    def _make_config(self):
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=0.0, flash_loan_provider="aave_v3",
            slippage_bps=0.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Uniswap-Ethereum", base_price=3000.0, fee_bps=0.0,
                          volatility_bps=0.0, chain="ethereum"),
                DexConfig(name="Uniswap-Arbitrum", base_price=3000.0, fee_bps=0.0,
                          volatility_bps=0.0, chain="arbitrum"),
                DexConfig(name="Sushi-Ethereum", base_price=3000.0, fee_bps=0.0,
                          volatility_bps=0.0, chain="ethereum"),
            ],
        )
        config.validate()
        return config

    def test_cross_chain_filtered_out(self):
        """Cross-chain opportunities should not appear in scanner results."""
        config = self._make_config()
        scanner = OpportunityScanner(config)

        # Ethereum quotes at $3000, Arbitrum at $3100 — cross-chain spread.
        quotes = [
            MarketQuote(dex="Uniswap-Ethereum", pair="WETH/USDC",
                        buy_price=3001.0, sell_price=2999.0, fee_bps=0.0),
            MarketQuote(dex="Uniswap-Arbitrum", pair="WETH/USDC",
                        buy_price=3101.0, sell_price=3099.0, fee_bps=0.0),
        ]

        result = scanner.scan_and_rank(quotes)
        # Cross-chain should be filtered — no results.
        self.assertEqual(len(result.opportunities), 0)

    def test_same_chain_kept(self):
        """Same-chain opportunities should pass through."""
        config = self._make_config()
        scanner = OpportunityScanner(config)

        # Two DEXs on Ethereum with spread.
        quotes = [
            MarketQuote(dex="Uniswap-Ethereum", pair="WETH/USDC",
                        buy_price=3001.0, sell_price=2999.0, fee_bps=0.0),
            MarketQuote(dex="Sushi-Ethereum", pair="WETH/USDC",
                        buy_price=3081.0, sell_price=3079.0, fee_bps=0.0),
        ]

        result = scanner.scan_and_rank(quotes)
        self.assertGreater(len(result.opportunities), 0)
        # Verify it's same-chain.
        for opp in result.opportunities:
            self.assertFalse(opp.is_cross_chain)


class LowLiquidityFilterTests(unittest.TestCase):
    """Test that scanner filters opportunities from pools with very low liquidity."""

    def _make_config(self):
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=0.0, flash_loan_provider="aave_v3",
            slippage_bps=0.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Uniswap-Ethereum", base_price=3000.0, fee_bps=0.0,
                          volatility_bps=0.0, chain="ethereum"),
                DexConfig(name="Sushi-Ethereum", base_price=3000.0, fee_bps=0.0,
                          volatility_bps=0.0, chain="ethereum"),
            ],
        )
        config.validate()
        return config

    def test_low_liquidity_filtered(self):
        """Pools with <$1M liquidity should be filtered out."""
        config = self._make_config()
        scanner = OpportunityScanner(config)

        quotes = [
            MarketQuote(dex="Uniswap-Ethereum", pair="WETH/USDC",
                        buy_price=3001.0, sell_price=2999.0, fee_bps=0.0,
                        liquidity_usd=500_000),  # $500K — too thin for flash loan arb
            MarketQuote(dex="Sushi-Ethereum", pair="WETH/USDC",
                        buy_price=3081.0, sell_price=3079.0, fee_bps=0.0,
                        liquidity_usd=500_000),
        ]

        result = scanner.scan_and_rank(quotes)
        self.assertEqual(len(result.opportunities), 0)

    def test_good_liquidity_passes(self):
        """Pools with >$1M liquidity should pass."""
        config = self._make_config()
        scanner = OpportunityScanner(config)

        quotes = [
            MarketQuote(dex="Uniswap-Ethereum", pair="WETH/USDC",
                        buy_price=3001.0, sell_price=2999.0, fee_bps=0.0,
                        liquidity_usd=5_000_000),  # $5M — deep enough
            MarketQuote(dex="Sushi-Ethereum", pair="WETH/USDC",
                        buy_price=3081.0, sell_price=3079.0, fee_bps=0.0,
                        liquidity_usd=5_000_000),
        ]

        result = scanner.scan_and_rank(quotes)
        self.assertGreater(len(result.opportunities), 0)

    def test_no_liquidity_data_passes(self):
        """When liquidity_usd is 0 (unknown), don't filter — let it through."""
        config = self._make_config()
        scanner = OpportunityScanner(config)

        quotes = [
            MarketQuote(dex="Uniswap-Ethereum", pair="WETH/USDC",
                        buy_price=3001.0, sell_price=2999.0, fee_bps=0.0),
            MarketQuote(dex="Sushi-Ethereum", pair="WETH/USDC",
                        buy_price=3081.0, sell_price=3079.0, fee_bps=0.0),
        ]

        result = scanner.scan_and_rank(quotes)
        self.assertGreater(len(result.opportunities), 0)


if __name__ == "__main__":
    unittest.main()
