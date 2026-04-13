import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arbitrage_bot.config import BotConfig, DexConfig
from arbitrage_bot.executor import PaperExecutor
from arbitrage_bot.models import Opportunity


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

        self.assertTrue(result.success)
        self.assertEqual(result.realized_profit_base, 0.05)
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


if __name__ == "__main__":
    unittest.main()
