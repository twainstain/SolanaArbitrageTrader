"""Circuit breaker — auto-pause execution on repeated failures.

Generic state machine: CLOSED → OPEN → HALF_OPEN → CLOSED.

Products configure trip conditions via CircuitBreakerConfig.
The breaker tracks events in sliding windows and trips when
thresholds are breached. Auto-recovers after cooldown.

This is product-agnostic — works for DEX reverts, CLOB rejections,
API errors, or any failure pattern.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock

logger = logging.getLogger(__name__)


class BreakerState(str, Enum):
    """Circuit breaker states."""
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Tripped — execution blocked
    HALF_OPEN = "half_open" # Cooldown expired — one probe allowed


@dataclass
class CircuitBreakerConfig:
    """Configurable thresholds for the circuit breaker."""
    # Failures: trip after N failures within window_seconds
    max_failures: int = 3
    failure_window_seconds: float = 300.0

    # Errors: trip after N errors within window_seconds (separate from failures)
    max_errors: int = 5
    error_window_seconds: float = 60.0

    # Staleness: trip if no fresh data for this many seconds
    max_stale_seconds: float = 120.0

    # Rate: max events within window
    max_events_per_window: int = 3
    event_window_size: int = 10

    # Cooldown: how long OPEN state lasts before allowing a probe
    cooldown_seconds: float = 300.0


class CircuitBreaker:
    """Thread-safe circuit breaker with sliding window tracking.

    Usage:
        breaker = CircuitBreaker()

        # Before executing:
        if breaker.should_block():
            return  # Execution paused

        # After success:
        breaker.record_success()

        # After failure:
        breaker.record_failure()

        # After external error:
        breaker.record_error()

        # After receiving fresh data:
        breaker.record_fresh_data()
    """

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        self._config = config or CircuitBreakerConfig()
        self._lock = Lock()
        self._state = BreakerState.CLOSED
        self._trip_reason = ""
        self._tripped_at: float = 0
        self._last_fresh_data_at: float = time.time()
        self._failures: deque = deque()
        self._errors: deque = deque()
        self._events: deque = deque()

    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._check_recovery()
            return self._state

    @property
    def trip_reason(self) -> str:
        with self._lock:
            return self._trip_reason

    def should_block(self) -> bool:
        """Return True if execution should be blocked."""
        with self._lock:
            self._check_recovery()
            self._check_staleness()
            return self._state == BreakerState.OPEN

    def record_failure(self) -> None:
        """Record a failure (revert, rejected order, etc.)."""
        now = time.time()
        with self._lock:
            self._failures.append(now)
            self._prune(self._failures, self._config.failure_window_seconds)
            if len(self._failures) >= self._config.max_failures:
                self._trip("repeated_failures")

    def record_error(self) -> None:
        """Record an external error (RPC, API, etc.)."""
        now = time.time()
        with self._lock:
            self._errors.append(now)
            self._prune(self._errors, self._config.error_window_seconds)
            if len(self._errors) >= self._config.max_errors:
                self._trip("external_errors")

    def record_success(self) -> None:
        """Record a successful execution. Resets HALF_OPEN to CLOSED."""
        with self._lock:
            if self._state == BreakerState.HALF_OPEN:
                self._state = BreakerState.CLOSED
                self._trip_reason = ""
                logger.info("Circuit breaker CLOSED — probe succeeded")

    def record_fresh_data(self) -> None:
        """Record receipt of fresh data (quote, tick, etc.)."""
        with self._lock:
            self._last_fresh_data_at = time.time()

    def record_event(self, block_or_seq: int = 0) -> None:
        """Record a rate-limited event (trade, order, etc.)."""
        with self._lock:
            self._events.append(block_or_seq)
            while (len(self._events) > 0
                   and self._events[0] < block_or_seq - self._config.event_window_size):
                self._events.popleft()
            if len(self._events) >= self._config.max_events_per_window:
                self._trip("rate_exceeded")

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self._state.value,
                "trip_reason": self._trip_reason,
                "recent_failures": len(self._failures),
                "recent_errors": len(self._errors),
                "seconds_since_fresh_data": round(time.time() - self._last_fresh_data_at, 1),
            }

    def _trip(self, reason: str) -> None:
        """Trip the breaker (must hold lock)."""
        if self._state != BreakerState.OPEN:
            self._state = BreakerState.OPEN
            self._trip_reason = reason
            self._tripped_at = time.time()
            logger.warning("Circuit breaker OPEN — reason: %s", reason)

    def _check_recovery(self) -> None:
        """Check if cooldown has expired (must hold lock)."""
        if self._state == BreakerState.OPEN:
            elapsed = time.time() - self._tripped_at
            if elapsed >= self._config.cooldown_seconds:
                self._state = BreakerState.HALF_OPEN
                logger.info("Circuit breaker HALF_OPEN — probe allowed after %.0fs cooldown", elapsed)

    def _check_staleness(self) -> None:
        """Check for stale data (must hold lock)."""
        elapsed = time.time() - self._last_fresh_data_at
        if elapsed >= self._config.max_stale_seconds:
            self._trip("stale_data")

    @staticmethod
    def _prune(dq: deque, window_seconds: float) -> None:
        """Remove entries older than window_seconds."""
        cutoff = time.time() - window_seconds
        while dq and dq[0] < cutoff:
            dq.popleft()
