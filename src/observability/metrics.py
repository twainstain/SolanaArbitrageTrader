"""In-memory metrics collector for the arbitrage system.

Per the architecture doc, tracks:
  - opportunities detected per minute
  - candidates rejected per reason
  - simulation success rate
  - execution inclusion rate
  - revert rate
  - average expected PnL
  - average actual PnL
  - gas cost distribution
  - latency from detect → submit
  - data freshness lag

Thread-safe via threading.Lock. Exposes metrics as a dict for the API.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class _Counters:
    opportunities_detected: int = 0
    opportunities_rejected: int = 0
    simulations_run: int = 0
    simulations_passed: int = 0
    executions_submitted: int = 0
    executions_included: int = 0
    executions_reverted: int = 0
    executions_not_included: int = 0
    total_expected_profit: float = 0.0
    total_actual_profit: float = 0.0
    total_fee_paid_lamports: int = 0
    rejection_reasons: dict = field(default_factory=lambda: defaultdict(int))
    latencies_ms: list = field(default_factory=list)


class MetricsCollector:
    """Thread-safe in-memory metrics for the bot runtime.

    Call record_* methods from the pipeline/bot loop.
    Call snapshot() from the API to get the current state.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._c = _Counters()
        self._start_time = time.time()

    def record_opportunity_detected(self) -> None:
        with self._lock:
            self._c.opportunities_detected += 1

    def record_opportunity_rejected(self, reason: str) -> None:
        with self._lock:
            self._c.opportunities_rejected += 1
            self._c.rejection_reasons[reason] += 1

    def record_simulation(self, passed: bool) -> None:
        with self._lock:
            self._c.simulations_run += 1
            if passed:
                self._c.simulations_passed += 1

    def record_execution_submitted(self) -> None:
        with self._lock:
            self._c.executions_submitted += 1

    def record_execution_result(
        self,
        included: bool,
        reverted: bool,
        fee_paid_lamports: int = 0,
        actual_profit: float = 0.0,
    ) -> None:
        with self._lock:
            if included and not reverted:
                self._c.executions_included += 1
                self._c.total_actual_profit += actual_profit
            elif reverted:
                self._c.executions_reverted += 1
            else:
                self._c.executions_not_included += 1
            self._c.total_fee_paid_lamports += fee_paid_lamports

    def record_expected_profit(self, profit: float) -> None:
        with self._lock:
            self._c.total_expected_profit += profit

    def record_latency_ms(self, detect_to_submit_ms: float) -> None:
        with self._lock:
            self._c.latencies_ms.append(detect_to_submit_ms)
            # Keep only last 1000 latency samples.
            if len(self._c.latencies_ms) > 1000:
                self._c.latencies_ms = self._c.latencies_ms[-1000:]

    def snapshot(self) -> dict:
        """Return a point-in-time snapshot of all metrics."""
        with self._lock:
            c = self._c
            uptime = time.time() - self._start_time
            uptime_min = max(uptime / 60, 1)

            sim_rate = (c.simulations_passed / c.simulations_run * 100
                        if c.simulations_run > 0 else 0)
            total_exec = c.executions_included + c.executions_reverted + c.executions_not_included
            inclusion_rate = (c.executions_included / total_exec * 100
                              if total_exec > 0 else 0)
            revert_rate = (c.executions_reverted / total_exec * 100
                           if total_exec > 0 else 0)

            avg_latency = (sum(c.latencies_ms) / len(c.latencies_ms)
                           if c.latencies_ms else 0)
            p95_latency = (sorted(c.latencies_ms)[int(len(c.latencies_ms) * 0.95)]
                           if c.latencies_ms else 0)

            return {
                "uptime_seconds": round(uptime, 1),
                "opportunities_detected": c.opportunities_detected,
                "opportunities_per_minute": round(c.opportunities_detected / uptime_min, 2),
                "opportunities_rejected": c.opportunities_rejected,
                "rejection_reasons": dict(c.rejection_reasons),
                "simulations_run": c.simulations_run,
                "simulations_passed": c.simulations_passed,
                "simulation_success_rate_pct": round(sim_rate, 1),
                "executions_submitted": c.executions_submitted,
                "executions_included": c.executions_included,
                "executions_reverted": c.executions_reverted,
                "executions_not_included": c.executions_not_included,
                "inclusion_rate_pct": round(inclusion_rate, 1),
                "revert_rate_pct": round(revert_rate, 1),
                "total_expected_profit": round(c.total_expected_profit, 8),
                "total_actual_profit": round(c.total_actual_profit, 8),
                "total_fee_paid_lamports": c.total_fee_paid_lamports,
                "avg_latency_ms": round(avg_latency, 1),
                "p95_latency_ms": round(p95_latency, 1),
            }

    def reset(self) -> None:
        """Reset all counters. Useful for testing or daily resets."""
        with self._lock:
            self._c = _Counters()
            self._start_time = time.time()
