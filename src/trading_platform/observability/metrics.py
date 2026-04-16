"""Thread-safe metrics collector with configurable counters.

Generic pattern: register counter names, increment them, take snapshots.
Products define their own counter names — the collector doesn't know
what "opportunities_detected" or "orders_placed" means.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from threading import Lock

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Generic thread-safe metrics collector.

    Usage:
        metrics = MetricsCollector()
        metrics.increment("opportunities_detected")
        metrics.increment("rejected", tag="below_min_profit")
        metrics.record_latency(145.2)
        snap = metrics.snapshot()
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._tagged_counters: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._gauges: dict[str, float] = {}
        self._latencies: list[float] = []
        self._start_time = time.time()

    def increment(self, name: str, amount: int = 1, tag: str | None = None) -> None:
        """Increment a counter, optionally with a tag for breakdown."""
        with self._lock:
            self._counters[name] += amount
            if tag:
                self._tagged_counters[name][tag] += amount

    def set_gauge(self, name: str, value: float) -> None:
        """Set a gauge to a specific value."""
        with self._lock:
            self._gauges[name] = value

    def record_latency(self, ms: float) -> None:
        """Record a latency sample in milliseconds."""
        with self._lock:
            self._latencies.append(ms)
            if len(self._latencies) > 1000:
                self._latencies = self._latencies[-1000:]

    def snapshot(self) -> dict:
        """Return a point-in-time snapshot of all metrics."""
        with self._lock:
            uptime = time.time() - self._start_time
            uptime_min = max(uptime / 60, 1)

            avg_latency = sum(self._latencies) / len(self._latencies) if self._latencies else 0
            sorted_lat = sorted(self._latencies) if self._latencies else []
            p95_latency = sorted_lat[int(len(sorted_lat) * 0.95)] if sorted_lat else 0

            return {
                "uptime_seconds": round(uptime, 1),
                "counters": dict(self._counters),
                "tagged_counters": {k: dict(v) for k, v in self._tagged_counters.items()},
                "gauges": dict(self._gauges),
                "avg_latency_ms": round(avg_latency, 1),
                "p95_latency_ms": round(p95_latency, 1),
                "latency_samples": len(self._latencies),
            }
