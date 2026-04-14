"""Per-DEX quote diagnostics collector.

Tracks success/failure/timeout/zero outcomes for each (dex, chain, pair)
combination.  Thread-safe for use with OnChainMarket's ThreadPoolExecutor.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum


class QuoteOutcome(Enum):
    SUCCESS = "success"
    ZERO = "zero"
    TIMEOUT = "timeout"
    ERROR = "error"
    CACHED_SKIP = "cached_skip"


@dataclass
class QuoteRecord:
    outcome: QuoteOutcome
    timestamp: float
    latency_ms: float = 0.0
    error_msg: str = ""


class QuoteDiagnostics:
    """Collects per-DEX quote health metrics."""

    def __init__(self, max_history: int = 50) -> None:
        self._lock = threading.Lock()
        self._max_history = max_history
        self._history: dict[str, deque[QuoteRecord]] = defaultdict(
            lambda: deque(maxlen=max_history)
        )

    def record(
        self,
        dex: str,
        chain: str,
        pair: str,
        outcome: QuoteOutcome,
        latency_ms: float = 0.0,
        error_msg: str = "",
    ) -> None:
        key = f"{dex}:{chain}:{pair}"
        rec = QuoteRecord(
            outcome=outcome,
            timestamp=time.time(),
            latency_ms=latency_ms,
            error_msg=error_msg,
        )
        with self._lock:
            self._history[key].append(rec)

    def snapshot(self) -> dict[str, dict]:
        """Return per-key health summary for API consumption."""
        with self._lock:
            result: dict[str, dict] = {}
            for key, records in self._history.items():
                total = len(records)
                successes = sum(1 for r in records if r.outcome == QuoteOutcome.SUCCESS)
                last = records[-1] if records else None
                latencies = [r.latency_ms for r in records if r.latency_ms > 0]
                result[key] = {
                    "total_quotes": total,
                    "success_count": successes,
                    "success_rate": round(successes / total, 3) if total > 0 else 0.0,
                    "last_outcome": last.outcome.value if last else None,
                    "last_timestamp": last.timestamp if last else None,
                    "last_error": last.error_msg if last and last.error_msg else None,
                    "avg_latency_ms": (
                        round(sum(latencies) / len(latencies), 1)
                        if latencies else 0.0
                    ),
                }
            return result
