"""Paper (simulated) trade executor for SolanaTrader.

PaperExecutor is the default in Phase 1 (scanner-only).  It never touches
the Solana RPC and simply re-reports the opportunity's net profit.

A real ``SolanaExecutor`` (Jupiter swap instruction + priority fee + submit
via RPC or Jito) is stubbed in ``execution.solana_executor`` and will be
implemented in Phase 3.
"""

from __future__ import annotations

from core.config import BotConfig
from core.models import ZERO, ExecutionResult, Opportunity


class PaperExecutor:
    """Simulated executor — never sends a transaction."""

    def __init__(self, config: BotConfig) -> None:
        self.config = config

    def execute(self, opportunity: Opportunity) -> ExecutionResult:
        if opportunity.net_profit_base <= ZERO:
            return ExecutionResult(
                success=False,
                reason="profit turned negative before execution",
                realized_profit_base=ZERO,
                opportunity=opportunity,
            )
        return ExecutionResult(
            success=True,
            reason="executed in paper mode",
            realized_profit_base=opportunity.net_profit_base,
            opportunity=opportunity,
        )
