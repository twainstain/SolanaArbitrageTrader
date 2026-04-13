"""Paper (simulated) trade executor."""

from __future__ import annotations

from arbitrage_bot.config import BotConfig
from arbitrage_bot.models import ExecutionResult, Opportunity


class PaperExecutor:
    """Simulated executor that can model simple execution failure rules."""

    def __init__(self, config: BotConfig) -> None:
        self.config = config

    def execute(self, opportunity: Opportunity) -> ExecutionResult:
        if opportunity.net_profit_base <= 0:
            return ExecutionResult(
                success=False,
                reason="profit turned negative before execution",
                realized_profit_base=0.0,
                opportunity=opportunity,
            )

        return ExecutionResult(
            success=True,
            reason="executed in paper mode",
            realized_profit_base=opportunity.net_profit_base,
            opportunity=opportunity,
        )
