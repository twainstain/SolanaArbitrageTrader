import io
import logging
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arbitrage_bot.bot import ArbitrageBot
from arbitrage_bot.config import BotConfig, DexConfig
from arbitrage_bot.market import SimulatedMarket


def _make_bot_config(**overrides: object) -> BotConfig:
    defaults: dict = dict(
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
            DexConfig(name="A", base_price=3000.0, fee_bps=0.0, volatility_bps=0.0),
            DexConfig(name="B", base_price=3030.0, fee_bps=0.0, volatility_bps=0.0),
        ],
    )
    defaults.update(overrides)
    config = BotConfig(**defaults)
    config.validate()
    return config


def _capture_log_output(bot: ArbitrageBot, **run_kwargs) -> str:
    """Run the bot and capture log output from the arbitrage_bot.bot logger."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(message)s"))

    bot_logger = logging.getLogger("arbitrage_bot.bot")
    bot_logger.addHandler(handler)
    bot_logger.setLevel(logging.DEBUG)
    try:
        bot.run(**run_kwargs)
    finally:
        bot_logger.removeHandler(handler)

    return stream.getvalue()


class BotTests(unittest.TestCase):
    def test_bot_runs_without_sleep(self) -> None:
        config = _make_bot_config()
        market = SimulatedMarket(config, seed=1)
        bot = ArbitrageBot(config, market=market)

        output = _capture_log_output(bot, iterations=1, sleep=False)
        self.assertIn("[scan 1]", output)

    def test_bot_dry_run_does_not_execute(self) -> None:
        config = _make_bot_config()
        market = SimulatedMarket(config, seed=1)
        bot = ArbitrageBot(config, market=market)

        output = _capture_log_output(bot, iterations=3, sleep=False, dry_run=True)
        self.assertIn("DRY-RUN", output)
        self.assertNotIn("[exec", output)

    def test_bot_summary_printed(self) -> None:
        config = _make_bot_config()
        market = SimulatedMarket(config, seed=1)
        bot = ArbitrageBot(config, market=market)

        output = _capture_log_output(bot, iterations=2, sleep=False)
        self.assertIn("--- Summary", output)
        self.assertIn("Scans: 2", output)


if __name__ == "__main__":
    unittest.main()
