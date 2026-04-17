import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.config import BotConfig, DexConfig
from execution.executor import PaperExecutor
from core.models import Opportunity


def _make_config() -> BotConfig:
    config = BotConfig(
        pair="WETH/USDC",
        base_asset="WETH",
        quote_asset="USDC",
        trade_size=1.0,
        min_profit_base=0.01,
        estimated_gas_cost_base=0.003,
        flash_loan_fee_bps=9.0,
        flash_loan_provider="aave_v3",
        slippage_bps=10.0,
        poll_interval_seconds=0.0,
        dexes=[
            DexConfig(name="A", base_price=3000.0, fee_bps=30.0, volatility_bps=0.0),
            DexConfig(name="B", base_price=3050.0, fee_bps=30.0, volatility_bps=0.0),
        ],
    )
    config.validate()
    return config


def _make_opportunity(net_profit_base: float = 0.05) -> Opportunity:
    return Opportunity(
        pair="WETH/USDC",
        buy_dex="A",
        sell_dex="B",
        trade_size=1.0,
        cost_to_buy_quote=3001.0,
        proceeds_from_sell_quote=3079.0,
        gross_profit_quote=78.0,
        net_profit_quote=50.0,
        net_profit_base=net_profit_base,
    )


class PaperExecutorTests(unittest.TestCase):
    def test_executes_profitable_opportunity(self) -> None:
        config = _make_config()
        executor = PaperExecutor(config)
        opp = _make_opportunity(net_profit_base=0.05)

        result = executor.execute(opp)

        from decimal import Decimal
        self.assertTrue(result.success)
        self.assertEqual(result.realized_profit_base, Decimal("0.05"))
        self.assertIs(result.opportunity, opp)

    def test_rejects_zero_profit(self) -> None:
        config = _make_config()
        executor = PaperExecutor(config)
        opp = _make_opportunity(net_profit_base=0.0)

        result = executor.execute(opp)

        self.assertFalse(result.success)
        self.assertEqual(result.realized_profit_base, 0.0)
        self.assertIn("negative", result.reason)

    def test_rejects_negative_profit(self) -> None:
        config = _make_config()
        executor = PaperExecutor(config)
        opp = _make_opportunity(net_profit_base=-0.01)

        result = executor.execute(opp)

        self.assertFalse(result.success)
        self.assertEqual(result.realized_profit_base, 0.0)

    def test_result_references_original_opportunity(self) -> None:
        config = _make_config()
        executor = PaperExecutor(config)
        opp = _make_opportunity()

        result = executor.execute(opp)

        self.assertIs(result.opportunity, opp)
        self.assertEqual(result.opportunity.buy_dex, "A")
        self.assertEqual(result.opportunity.sell_dex, "B")


class PaperExecutorEdgeCaseTests(unittest.TestCase):
    """Additional edge cases for PaperExecutor."""

    def test_very_small_profit_still_executes(self) -> None:
        """Even tiny profits should succeed (the strategy already filters)."""
        config = _make_config()
        executor = PaperExecutor(config)
        opp = _make_opportunity(net_profit_base=0.000001)
        result = executor.execute(opp)
        self.assertTrue(result.success)

    def test_large_profit_executes(self) -> None:
        config = _make_config()
        executor = PaperExecutor(config)
        opp = _make_opportunity(net_profit_base=10.0)
        result = executor.execute(opp)
        self.assertTrue(result.success)

    def test_result_realized_profit_equals_expected(self) -> None:
        """Paper executor assumes perfect execution — realized = expected."""
        config = _make_config()
        executor = PaperExecutor(config)
        opp = _make_opportunity(net_profit_base=0.123456)
        result = executor.execute(opp)
        from decimal import Decimal
        self.assertEqual(result.realized_profit_base, Decimal("0.123456"))

    def test_result_reason_contains_paper_mode(self) -> None:
        config = _make_config()
        executor = PaperExecutor(config)
        result = executor.execute(_make_opportunity())
        self.assertIn("paper", result.reason.lower())

    def test_failed_result_has_zero_profit(self) -> None:
        config = _make_config()
        executor = PaperExecutor(config)
        result = executor.execute(_make_opportunity(net_profit_base=-5.0))
        from decimal import Decimal
        self.assertEqual(result.realized_profit_base, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
