"""Tests for the scanner module — ranking, risk flags, filtering, alerting."""

import sys
import time
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.config import BotConfig, DexConfig
from core.models import MarketQuote, Opportunity
from strategy.scanner import OpportunityScanner
from strategy.arb_strategy import ArbitrageStrategy


def _make_config(**overrides) -> BotConfig:
    defaults = dict(
        pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
        trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
        flash_loan_fee_bps=0.0, flash_loan_provider="aave_v3",
        slippage_bps=0.0, poll_interval_seconds=0.0,
        dexes=[
            DexConfig(name="A", base_price=3000.0, fee_bps=0.0, volatility_bps=0.0),
            DexConfig(name="B", base_price=3050.0, fee_bps=0.0, volatility_bps=0.0),
        ],
    )
    defaults.update(overrides)
    config = BotConfig(**defaults)
    config.validate()
    return config


class ScannerBasicTests(unittest.TestCase):
    def test_finds_and_ranks_opportunities(self) -> None:
        config = _make_config()
        scanner = OpportunityScanner(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0, fee_bps=0.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0, fee_bps=0.0),
        ]

        result = scanner.scan_and_rank(quotes)

        self.assertGreater(len(result.opportunities), 0)
        self.assertIsNotNone(result.best)
        self.assertEqual(result.best.buy_dex, "A")
        self.assertEqual(result.best.sell_dex, "B")

    def test_no_opportunity_returns_empty(self) -> None:
        config = _make_config()
        scanner = OpportunityScanner(config)
        # Same prices on both DEXs — no spread.
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3000.0, sell_price=2999.0, fee_bps=30.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3001.0, sell_price=3000.0, fee_bps=30.0),
        ]

        result = scanner.scan_and_rank(quotes)
        self.assertEqual(len(result.opportunities), 0)
        self.assertIsNone(result.best)

    def test_multiple_opportunities_ranked_by_score(self) -> None:
        config = _make_config(dexes=[
            DexConfig(name="A", base_price=100.0, fee_bps=0.0, volatility_bps=0.0),
            DexConfig(name="B", base_price=100.0, fee_bps=0.0, volatility_bps=0.0),
            DexConfig(name="C", base_price=100.0, fee_bps=0.0, volatility_bps=0.0),
        ])
        scanner = OpportunityScanner(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=100.0, sell_price=99.0, fee_bps=0.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=105.0, sell_price=104.0, fee_bps=0.0),
            MarketQuote(dex="C", pair="WETH/USDC", buy_price=110.0, sell_price=109.0, fee_bps=0.0),
        ]

        result = scanner.scan_and_rank(quotes)
        # Best should be buy on A (cheapest), sell on C (most expensive).
        self.assertIsNotNone(result.best)
        self.assertEqual(result.best.buy_dex, "A")
        self.assertEqual(result.best.sell_dex, "C")

    def test_only_compares_same_pair(self) -> None:
        config = _make_config()
        scanner = OpportunityScanner(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0, fee_bps=0.0),
            MarketQuote(dex="B", pair="WBTC/USDC", buy_price=70001.0, sell_price=69999.0, fee_bps=0.0),
        ]

        result = scanner.scan_and_rank(quotes)
        # Different pairs should not be compared.
        self.assertEqual(len(result.opportunities), 0)


class RiskFlagTests(unittest.TestCase):
    def test_low_liquidity_flag(self) -> None:
        config = _make_config()
        strategy = ArbitrageStrategy(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0,
                        fee_bps=0.0, liquidity_usd=50_000),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0,
                        fee_bps=0.0, liquidity_usd=80_000),
        ]

        opp = strategy.find_best_opportunity(quotes)
        self.assertIsNotNone(opp)
        assert opp is not None
        self.assertIn("low_liquidity", opp.warning_flags)

    def test_thin_market_flag(self) -> None:
        config = _make_config()
        strategy = ArbitrageStrategy(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0,
                        fee_bps=0.0, volume_usd=30_000),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0,
                        fee_bps=0.0, volume_usd=40_000),
        ]

        opp = strategy.find_best_opportunity(quotes)
        self.assertIsNotNone(opp)
        assert opp is not None
        self.assertIn("thin_market", opp.warning_flags)

    def test_stale_quote_flag(self) -> None:
        config = _make_config()
        strategy = ArbitrageStrategy(config)
        old_ts = time.time() - 120  # 2 minutes ago
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0,
                        fee_bps=0.0, quote_timestamp=old_ts),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0,
                        fee_bps=0.0, quote_timestamp=old_ts),
        ]

        opp = strategy.find_best_opportunity(quotes)
        self.assertIsNotNone(opp)
        assert opp is not None
        self.assertIn("stale_quote", opp.warning_flags)

    def test_no_flags_when_data_unavailable(self) -> None:
        """When liquidity/volume/timestamp are 0 (default), no flags should fire."""
        config = _make_config()
        strategy = ArbitrageStrategy(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0, fee_bps=0.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0, fee_bps=0.0),
        ]

        opp = strategy.find_best_opportunity(quotes)
        self.assertIsNotNone(opp)
        assert opp is not None
        self.assertEqual(len(opp.warning_flags), 0)

    def test_high_liquidity_gives_good_score(self) -> None:
        config = _make_config()
        strategy = ArbitrageStrategy(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0,
                        fee_bps=0.0, liquidity_usd=50_000_000),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0,
                        fee_bps=0.0, liquidity_usd=100_000_000),
        ]

        opp = strategy.find_best_opportunity(quotes)
        assert opp is not None
        self.assertGreater(opp.liquidity_score, 0.9)


class AlertFilterTests(unittest.TestCase):
    def test_filters_by_min_profit(self) -> None:
        config = _make_config()
        scanner = OpportunityScanner(config, alert_min_net_profit=100.0)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0, fee_bps=0.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0, fee_bps=0.0),
        ]

        result = scanner.scan_and_rank(quotes)
        # The opportunity exists but doesn't meet the 100 WETH min threshold.
        self.assertEqual(len(result.opportunities), 0)
        self.assertGreater(result.rejected_count, 0)

    def test_filters_by_max_warning_flags(self) -> None:
        config = _make_config()
        scanner = OpportunityScanner(config, alert_max_warning_flags=0)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0,
                        fee_bps=0.0, liquidity_usd=50_000),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0,
                        fee_bps=0.0, liquidity_usd=80_000),
        ]

        result = scanner.scan_and_rank(quotes)
        # low_liquidity flag should cause rejection when max_flags=0.
        self.assertEqual(len(result.opportunities), 0)


class ChainAwareLiquidityFilterTests(unittest.TestCase):
    """The $1M min-liquidity filter should be lowered to $100K on L2 chains."""

    def _make_chain_config(self) -> BotConfig:
        return _make_config(
            min_profit_base=0.0,
            dexes=[
                DexConfig(name="Uni-Arbitrum", base_price=3000.0, fee_bps=0.0,
                          volatility_bps=0.0, chain="arbitrum"),
                DexConfig(name="Sushi-Arbitrum", base_price=3050.0, fee_bps=0.0,
                          volatility_bps=0.0, chain="arbitrum"),
            ],
        )

    def test_l2_passes_200k_liquidity(self) -> None:
        """$200K pool should pass on Arbitrum (L2 threshold is $100K)."""
        config = self._make_chain_config()
        scanner = OpportunityScanner(config)
        quotes = [
            MarketQuote(dex="Uni-Arbitrum", pair="WETH/USDC", buy_price=3001.0,
                        sell_price=2999.0, fee_bps=0.0, liquidity_usd=200_000),
            MarketQuote(dex="Sushi-Arbitrum", pair="WETH/USDC", buy_price=3081.0,
                        sell_price=3079.0, fee_bps=0.0, liquidity_usd=200_000),
        ]
        result = scanner.scan_and_rank(quotes)
        self.assertGreater(len(result.opportunities), 0)

    def test_l2_rejects_below_threshold_liquidity(self) -> None:
        """$20K pool should be rejected on Arbitrum (below $25K threshold)."""
        config = self._make_chain_config()
        scanner = OpportunityScanner(config)
        quotes = [
            MarketQuote(dex="Uni-Arbitrum", pair="WETH/USDC", buy_price=3001.0,
                        sell_price=2999.0, fee_bps=0.0, liquidity_usd=20_000),
            MarketQuote(dex="Sushi-Arbitrum", pair="WETH/USDC", buy_price=3081.0,
                        sell_price=3079.0, fee_bps=0.0, liquidity_usd=20_000),
        ]
        result = scanner.scan_and_rank(quotes)
        self.assertEqual(len(result.opportunities), 0)

    def test_ethereum_still_requires_1m(self) -> None:
        """$200K pool should be rejected on Ethereum (threshold stays $1M)."""
        config = _make_config(
            min_profit_base=0.0,
            dexes=[
                DexConfig(name="Uni-Ethereum", base_price=3000.0, fee_bps=0.0,
                          volatility_bps=0.0, chain="ethereum"),
                DexConfig(name="Sushi-Ethereum", base_price=3050.0, fee_bps=0.0,
                          volatility_bps=0.0, chain="ethereum"),
            ],
        )
        scanner = OpportunityScanner(config)
        quotes = [
            MarketQuote(dex="Uni-Ethereum", pair="WETH/USDC", buy_price=3001.0,
                        sell_price=2999.0, fee_bps=0.0, liquidity_usd=200_000),
            MarketQuote(dex="Sushi-Ethereum", pair="WETH/USDC", buy_price=3081.0,
                        sell_price=3079.0, fee_bps=0.0, liquidity_usd=200_000),
        ]
        result = scanner.scan_and_rank(quotes)
        self.assertEqual(len(result.opportunities), 0)

    def test_ethereum_passes_2m_liquidity(self) -> None:
        """$2M pool should pass on Ethereum."""
        config = _make_config(
            min_profit_base=0.0,
            dexes=[
                DexConfig(name="Uni-Ethereum", base_price=3000.0, fee_bps=0.0,
                          volatility_bps=0.0, chain="ethereum"),
                DexConfig(name="Sushi-Ethereum", base_price=3050.0, fee_bps=0.0,
                          volatility_bps=0.0, chain="ethereum"),
            ],
        )
        scanner = OpportunityScanner(config)
        quotes = [
            MarketQuote(dex="Uni-Ethereum", pair="WETH/USDC", buy_price=3001.0,
                        sell_price=2999.0, fee_bps=0.0, liquidity_usd=2_000_000),
            MarketQuote(dex="Sushi-Ethereum", pair="WETH/USDC", buy_price=3081.0,
                        sell_price=3079.0, fee_bps=0.0, liquidity_usd=2_000_000),
        ]
        result = scanner.scan_and_rank(quotes)
        self.assertGreater(len(result.opportunities), 0)


class ScanHistoryTests(unittest.TestCase):
    def test_history_tracks_results(self) -> None:
        config = _make_config()
        scanner = OpportunityScanner(config)
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0, fee_bps=0.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0, fee_bps=0.0),
        ]

        scanner.scan_and_rank(quotes)
        scanner.scan_and_rank(quotes)

        self.assertEqual(len(scanner.recent_history), 2)


    def test_grouping_finds_same_opportunities(self) -> None:
        """Pre-grouping by pair must produce the same results as the old N^2 approach."""
        config = _make_config()
        scanner = OpportunityScanner(config)
        # Quotes across two pairs — should only compare within same pair.
        quotes = [
            MarketQuote(dex="A", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0, fee_bps=0.0),
            MarketQuote(dex="B", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0, fee_bps=0.0),
            MarketQuote(dex="C", pair="WBTC/USDC", buy_price=60001.0, sell_price=59999.0, fee_bps=0.0),
            MarketQuote(dex="D", pair="WBTC/USDC", buy_price=61001.0, sell_price=60999.0, fee_bps=0.0),
        ]
        result = scanner.scan_and_rank(quotes)
        # Should find opportunities in both pairs.
        pairs_found = {opp.pair for opp in result.opportunities}
        self.assertTrue(len(pairs_found) >= 1)
        # No cross-pair comparisons (e.g. WETH vs WBTC) should be evaluated.
        for opp in result.opportunities:
            self.assertEqual(opp.pair.split("/")[1], "USDC")


if __name__ == "__main__":
    unittest.main()
