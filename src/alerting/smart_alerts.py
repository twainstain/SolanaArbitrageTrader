"""Smart alerts stub — Phase 3/4 deferral.

The rich hourly/daily EVM summary emails have been quarantined to
``_evm_legacy/src/alerting/smart_alerts.py`` because they reference per-
chain wallet balances, Etherscan links, and flash-loan execution stats
that don't apply on Solana.

Phase 4 (operational hardening) will replace these with Solana-native
summaries: wallet SOL balance, per-venue quote success, slot-to-inclusion
latency, fee spend in lamports, and a rejection funnel.

Until then, ``SmartAlertScheduler`` is a no-op so ``run_event_driven.py``
imports cleanly.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class SmartAlertScheduler:
    """No-op placeholder.  See module docstring."""

    def __init__(self, *_args, **_kwargs) -> None:
        logger.debug("[alerts] SmartAlertScheduler stubbed for Phase 1 (scanner-only)")

    def tick(self, *_args, **_kwargs) -> None:
        return None

    def emit_hourly(self, *_args, **_kwargs) -> None:
        return None

    def emit_daily(self, *_args, **_kwargs) -> None:
        return None

    def close(self) -> None:
        return None
