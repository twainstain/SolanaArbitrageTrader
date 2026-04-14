import io
import logging
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from alerting.dispatcher import AlertDispatcher
from bot import ArbitrageBot
from config import BotConfig, DexConfig
from market import SimulatedMarket
from models import ExecutionResult, Opportunity, ZERO
from decimal import Decimal

D = Decimal


class _FakeBackend:
    """Records all alerts sent for assertion."""
    def __init__(self):
        self._name = "test"
        self.received: list[tuple] = []

    @property
    def name(self) -> str:
        return self._name

    def send(self, event_type, message, details=None):
        self.received.append((event_type, message, details))
        return True


class _FailingMarket:
    """Market that always raises."""
    def get_quotes(self):
        raise RuntimeError("RPC timeout")


class _FailingExecutor:
    """Executor that always returns failure."""
    def execute(self, opp):
        return ExecutionResult(
            success=False, reason="slippage_exceeded",
            realized_profit_base=ZERO, opportunity=opp,
        )


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
    """Run the bot and capture log output from the bot logger."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(message)s"))

    bot_logger = logging.getLogger("bot")
    bot_logger.addHandler(handler)
    bot_logger.setLevel(logging.DEBUG)
    try:
        bot.run(**run_kwargs)
    finally:
        bot_logger.removeHandler(handler)

    return stream.getvalue()


class BotTests(unittest.TestCase):
    def test_base_asset_for_opportunity_uses_opportunity_pair(self) -> None:
        opp = Opportunity(
            pair="OP/USDC",
            buy_dex="A",
            sell_dex="B",
            trade_size=1.0,
            cost_to_buy_quote=1.0,
            proceeds_from_sell_quote=1.1,
            gross_profit_quote=0.1,
            net_profit_quote=0.08,
            net_profit_base=0.01,
        )
        self.assertEqual(ArbitrageBot._base_asset_for_opportunity(opp), "OP")

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


class BotAlertingTests(unittest.TestCase):
    """Tests that the bot fires alerts through the dispatcher."""

    def test_opportunity_found_fires_alert(self):
        config = _make_bot_config()
        market = SimulatedMarket(config, seed=1)
        backend = _FakeBackend()
        dispatcher = AlertDispatcher([backend])
        bot = ArbitrageBot(config, market=market, dispatcher=dispatcher)

        bot.run(iterations=1, sleep=False, dry_run=True)

        events = [r[0] for r in backend.received]
        self.assertIn("opportunity_found", events)

    def test_trade_executed_fires_alert(self):
        config = _make_bot_config()
        market = SimulatedMarket(config, seed=1)
        backend = _FakeBackend()
        dispatcher = AlertDispatcher([backend])
        bot = ArbitrageBot(config, market=market, dispatcher=dispatcher)

        bot.run(iterations=1, sleep=False, dry_run=False)

        events = [r[0] for r in backend.received]
        self.assertIn("trade_executed", events)

    def test_market_error_fires_system_error(self):
        config = _make_bot_config()
        backend = _FakeBackend()
        dispatcher = AlertDispatcher([backend])
        bot = ArbitrageBot(config, market=_FailingMarket(), dispatcher=dispatcher)

        bot.run(iterations=1, sleep=False)

        events = [r[0] for r in backend.received]
        self.assertIn("system_error", events)
        # Verify the error message mentions the market
        system_errors = [r for r in backend.received if r[0] == "system_error"]
        self.assertIn("market", system_errors[0][1].lower())

    def test_executor_failure_fires_system_error(self):
        config = _make_bot_config()
        market = SimulatedMarket(config, seed=1)
        backend = _FakeBackend()
        dispatcher = AlertDispatcher([backend])
        bot = ArbitrageBot(
            config, market=market, executor=_FailingExecutor(),
            dispatcher=dispatcher,
        )

        bot.run(iterations=1, sleep=False, dry_run=False)

        events = [r[0] for r in backend.received]
        self.assertIn("system_error", events)
        system_errors = [r for r in backend.received if r[0] == "system_error"]
        self.assertIn("slippage_exceeded", system_errors[0][1])

    def test_daily_summary_fires_at_end(self):
        config = _make_bot_config()
        market = SimulatedMarket(config, seed=1)
        backend = _FakeBackend()
        dispatcher = AlertDispatcher([backend])
        bot = ArbitrageBot(config, market=market, dispatcher=dispatcher)

        bot.run(iterations=2, sleep=False, dry_run=True)

        events = [r[0] for r in backend.received]
        self.assertIn("daily_summary", events)

    def test_no_backends_doesnt_crash(self):
        """Bot works fine with an empty dispatcher (no backends configured)."""
        config = _make_bot_config()
        market = SimulatedMarket(config, seed=1)
        bot = ArbitrageBot(config, market=market)  # default empty dispatcher

        bot.run(iterations=1, sleep=False)
        # No crash = success

    def test_dry_run_fires_opportunity_but_not_trade(self):
        config = _make_bot_config()
        market = SimulatedMarket(config, seed=1)
        backend = _FakeBackend()
        dispatcher = AlertDispatcher([backend])
        bot = ArbitrageBot(config, market=market, dispatcher=dispatcher)

        bot.run(iterations=1, sleep=False, dry_run=True)

        events = [r[0] for r in backend.received]
        self.assertIn("opportunity_found", events)
        self.assertNotIn("trade_executed", events)


if __name__ == "__main__":
    unittest.main()
