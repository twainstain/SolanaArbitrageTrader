"""Thin adapters mapping ArbitrageTrader domain terms to trading_platform generic API.

The trading_platform uses generic names (record_failure, record_error, is_still_valid)
because it serves multiple products:
  - ArbitrageTrader: EVM flash-loan arb (reverts, RPC errors, profitable)
  - SolanaTrader: Solana DEX arb (tx drops, RPC errors, profitable)
  - PolymarketTrader: prediction markets (order rejects, API errors, odds favorable)

These adapters expose ArbitrageTrader's domain-specific method names while delegating
to the generic platform underneath.  When migrating, replace:
  from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
with:
  from platform_adapters import CircuitBreaker, CircuitBreakerConfig
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from trading_platform.risk.circuit_breaker import (
    BreakerState,
    CircuitBreaker as _PlatformBreaker,
    CircuitBreakerConfig as _PlatformBreakerConfig,
)
from trading_platform.risk.retry import (
    RetryPolicy as _PlatformRetryPolicy,
    RetryResult,
    config_hash,
    execute_with_retry as _platform_execute_with_retry,
)


# ---------------------------------------------------------------------------
# CircuitBreaker adapter
# ---------------------------------------------------------------------------

@dataclass
class CircuitBreakerConfig:
    """ArbitrageTrader-flavored config that maps to platform's generic config.

    AT term             → Platform term
    max_reverts         → max_failures
    revert_window_seconds → failure_window_seconds
    max_rpc_errors      → max_errors
    rpc_error_window_seconds → error_window_seconds
    max_trades_per_block_window → max_events_per_window
    block_window_size   → event_window_size
    """
    max_reverts: int = 3
    revert_window_seconds: float = 300.0
    max_stale_seconds: float = 120.0
    max_rpc_errors: int = 5
    rpc_error_window_seconds: float = 60.0
    max_trades_per_block_window: int = 3
    block_window_size: int = 10
    cooldown_seconds: float = 300.0

    def _to_platform(self) -> _PlatformBreakerConfig:
        return _PlatformBreakerConfig(
            max_failures=self.max_reverts,
            failure_window_seconds=self.revert_window_seconds,
            max_stale_seconds=self.max_stale_seconds,
            max_errors=self.max_rpc_errors,
            error_window_seconds=self.rpc_error_window_seconds,
            max_events_per_window=self.max_trades_per_block_window,
            event_window_size=self.block_window_size,
            cooldown_seconds=self.cooldown_seconds,
        )


class CircuitBreaker:
    """ArbitrageTrader-flavored circuit breaker wrapping the platform's generic one.

    AT method               → Platform method
    record_revert()         → record_failure()
    record_rpc_error()      → record_error()
    record_execution_success() → record_success()
    record_fresh_quote()    → record_fresh_data()
    record_trade_at_block(n) → record_event(n)
    allows_execution()      → (not should_block(), trip_reason)
    """

    # Map platform generic reason strings to AT domain terms.
    _REASON_MAP = {
        "repeated_failures": "repeated_reverts",
        "repeated_errors": "rpc_degradation",
        "external_errors": "rpc_degradation",
        "rate_exceeded": "block_window_exposure",
        "stale_data": "stale_data",
    }

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        if config is None:
            config = CircuitBreakerConfig()
        self._config = config
        self._breaker = _PlatformBreaker(config._to_platform())

    def _map_reason(self, reason: str) -> str:
        return self._REASON_MAP.get(reason, reason)

    def allows_execution(self) -> tuple[bool, str]:
        if self._breaker.should_block():
            return False, self._map_reason(self._breaker.trip_reason)
        if self._breaker.state == BreakerState.HALF_OPEN:
            return True, "half_open_probe"
        return True, "circuit_closed"

    @property
    def is_open(self) -> bool:
        # Trigger staleness check (TP only checks in should_block).
        self._breaker.should_block()
        return self._breaker.state == BreakerState.OPEN

    @property
    def state(self) -> BreakerState:
        # Trigger staleness check so state reflects stale data trips.
        self._breaker.should_block()
        return self._breaker.state

    @property
    def trip_reason(self) -> str:
        return self._map_reason(self._breaker.trip_reason)

    def record_revert(self) -> None:
        self._breaker.record_failure()

    def record_rpc_error(self) -> None:
        self._breaker.record_error()

    def record_execution_success(self) -> None:
        self._breaker.record_success()

    def record_fresh_quote(self) -> None:
        self._breaker.record_fresh_data()

    def record_trade_at_block(self, block_number: int) -> None:
        self._breaker.record_event(block_number)

    def reset(self) -> None:
        self._breaker = _PlatformBreaker(self._config._to_platform())

    def to_dict(self) -> dict:
        """Return status dict with AT-flavored key names."""
        d = self._breaker.status()
        # Map generic keys to AT domain terms
        return {
            "state": d.get("state", "closed"),
            "trip_reason": self._map_reason(d.get("trip_reason", "")),
            "recent_reverts": d.get("recent_failures", 0),
            "recent_rpc_errors": d.get("recent_errors", 0),
            "seconds_since_fresh_quote": d.get("seconds_since_fresh_data", 0.0),
        }


# ---------------------------------------------------------------------------
# RetryPolicy adapter
# ---------------------------------------------------------------------------

# RetryPolicy and RetryResult are identical — re-export directly.
RetryPolicy = _PlatformRetryPolicy


def execute_with_retry(
    execute_fn: Callable[[], tuple[bool, str]],
    is_still_profitable: Callable[[], bool] | None = None,
    policy: _PlatformRetryPolicy | None = None,
    current_config_hash: str = "",
) -> RetryResult:
    """AT-flavored wrapper: maps is_still_profitable → is_still_valid."""
    result = _platform_execute_with_retry(
        execute_fn=execute_fn,
        is_still_valid=is_still_profitable,
        policy=policy or _PlatformRetryPolicy(),
        current_config_hash=current_config_hash,
    )
    # Map TP's "not_valid" back to AT's "not_profitable" in reason strings.
    if result.last_reason and "not_valid" in result.last_reason:
        result = RetryResult(
            success=result.success,
            attempts=result.attempts,
            last_reason=result.last_reason.replace("not_valid", "not_profitable"),
            config_hash=result.config_hash,
        )
    return result


# ---------------------------------------------------------------------------
# Queue adapter
# ---------------------------------------------------------------------------

from dataclasses import dataclass
from typing import Any

from trading_platform.pipeline.queue import (
    PriorityQueue as _PlatformQueue,
    QueuedItem as _PlatformQueuedItem,
)
from core.models import Opportunity


@dataclass
class QueuedCandidate:
    """AT-flavored wrapper around QueuedItem with .opportunity accessor."""
    opportunity: Opportunity
    enqueued_at: float
    priority: float
    scan_marks: dict

    @classmethod
    def _from_platform(cls, item: _PlatformQueuedItem) -> "QueuedCandidate":
        return cls(
            opportunity=item.item,
            enqueued_at=item.enqueued_at,
            priority=item.priority,
            scan_marks=item.metadata or {},
        )


class CandidateQueue:
    """AT-flavored queue wrapping trading_platform's PriorityQueue.

    AT API:  push(opportunity, priority, scan_marks) → bool
    TP API:  push(item, priority, metadata) → bool
    """

    def __init__(self, max_size: int = 100) -> None:
        self._queue = _PlatformQueue(max_size=max_size)

    def push(
        self,
        opportunity: Opportunity,
        priority: float = 0.0,
        scan_marks: dict | None = None,
    ) -> bool:
        return self._queue.push(opportunity, priority=priority, metadata=scan_marks)

    def pop(self) -> QueuedCandidate | None:
        item = self._queue.pop()
        if item is None:
            return None
        return QueuedCandidate._from_platform(item)

    def pop_batch(self, max_count: int = 10) -> list[QueuedCandidate]:
        items = self._queue.pop_batch(max_count)
        return [QueuedCandidate._from_platform(i) for i in items]

    @property
    def is_empty(self) -> bool:
        return self._queue.is_empty

    @property
    def size(self) -> int:
        return self._queue.size

    def clear(self) -> int:
        return self._queue.clear()

    def stats(self) -> dict:
        return self._queue.stats()


# Re-export unchanged utilities.
__all__ = [
    "BreakerState",
    "CandidateQueue",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "QueuedCandidate",
    "RetryPolicy",
    "RetryResult",
    "config_hash",
    "execute_with_retry",
]
