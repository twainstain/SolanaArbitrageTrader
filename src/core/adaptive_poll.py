"""Adaptive poll-interval controller (Phase 2d).

The scanner normally runs at ``poll_interval_seconds``. When recent scans
show spread within a configurable fraction of ``min_profit_base`` — a
near-hit — we downshift to a faster ``fast_poll_seconds`` to catch the
next spike before competitors do.

The controller is a small sliding-window state machine: observe each
scan's best net profit, and return the current interval. It has no
threading concerns — the scanner loop is single-threaded and calls
``observe()`` / ``current_interval()`` sequentially each cycle.

Phase 2d rationale: SOL/USDC spreads are structurally below round-trip
fees under normal conditions (see docs/solana_migration_status.md),
so running at 0.75s all the time burns RPC budget. But during the
brief mispricing spikes where arb exists, we want to be sampling at
0.25s so the window isn't closed by the time we round-trip. Adaptive
polling is the cheap compromise — slow by default, fast on signal.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Deque

D = Decimal
ZERO = D("0")


@dataclass
class AdaptivePoll:
    """Slow/fast poll controller with a rolling near-hit window.

    Fields:
        slow_seconds: default poll interval (``poll_interval_seconds``).
        fast_seconds: optional downshifted interval. If None or equal to
            slow, the controller is effectively disabled (always slow).
        near_hit_ratio: a scan is a near-hit when its best observed net
            profit ≥ ratio × min_profit_base. Default 0.5.
        window: number of recent scans considered. Default 30 (≈20s at 0.75s).
    """
    slow_seconds: float
    fast_seconds: float | None = None
    near_hit_ratio: float = 0.5
    window: int = 30
    _hits: Deque[bool] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.slow_seconds < 0:
            raise ValueError("slow_seconds must be non-negative")
        if self.fast_seconds is not None and self.fast_seconds < 0:
            raise ValueError("fast_seconds must be non-negative")
        if self.near_hit_ratio < 0:
            raise ValueError("near_hit_ratio must be non-negative")
        if self.window <= 0:
            raise ValueError("window must be positive")
        self._hits = deque(maxlen=self.window)

    def observe(self, best_net_profit: Decimal, min_profit_base: Decimal) -> None:
        """Record this scan's best net profit.

        ``best_net_profit`` should be the max across all candidates
        evaluated this scan — passed, rejected, near-miss alike. Pass
        Decimal("0") or a very negative value when no candidate was
        evaluated. ``min_profit_base`` is the actionable threshold from
        config.
        """
        if min_profit_base <= ZERO:
            # Degenerate config: treat every non-negative scan as a hit
            # so the controller doesn't sit forever in slow mode.
            self._hits.append(best_net_profit >= ZERO)
            return
        threshold = min_profit_base * D(str(self.near_hit_ratio))
        self._hits.append(best_net_profit >= threshold)

    def near_hit_in_window(self) -> bool:
        return any(self._hits)

    def current_interval(self) -> float:
        """Return the current poll interval in seconds.

        Returns ``fast_seconds`` if any of the last ``window`` scans was
        a near-hit, else ``slow_seconds``. If ``fast_seconds`` was not
        configured, always returns ``slow_seconds``.
        """
        if self.fast_seconds is None or self.fast_seconds >= self.slow_seconds:
            return self.slow_seconds
        return self.fast_seconds if self.near_hit_in_window() else self.slow_seconds

    def reset(self) -> None:
        """Clear the observation window. Useful for tests or operator resets."""
        self._hits.clear()
