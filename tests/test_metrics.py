"""Tests for observability.metrics.MetricsCollector.

The collector powers the `/metrics` HTTP endpoint consumed by the dashboard
and external Prometheus-ish scrapers. Bugs here show up as silent counter
drift in prod.
"""

import sys
import threading
import time
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "trading_platform" / "src"))

from observability.metrics import MetricsCollector


class MetricsCollectorTests(unittest.TestCase):
    def test_initial_snapshot_has_zero_counters(self):
        m = MetricsCollector()
        snap = m.snapshot()
        self.assertEqual(snap["opportunities_detected"], 0)
        self.assertEqual(snap["opportunities_rejected"], 0)
        self.assertEqual(snap["simulations_run"], 0)
        self.assertEqual(snap["executions_submitted"], 0)
        self.assertEqual(snap["rejection_reasons"], {})
        self.assertEqual(snap["avg_latency_ms"], 0)
        self.assertEqual(snap["p95_latency_ms"], 0)
        self.assertGreaterEqual(snap["uptime_seconds"], 0)

    def test_opportunity_detected_increments(self):
        m = MetricsCollector()
        m.record_opportunity_detected()
        m.record_opportunity_detected()
        m.record_opportunity_detected()
        self.assertEqual(m.snapshot()["opportunities_detected"], 3)

    def test_opportunity_rejected_tracks_reason(self):
        m = MetricsCollector()
        m.record_opportunity_rejected("below_min_profit")
        m.record_opportunity_rejected("below_min_profit")
        m.record_opportunity_rejected("low_liquidity")
        snap = m.snapshot()
        self.assertEqual(snap["opportunities_rejected"], 3)
        self.assertEqual(
            snap["rejection_reasons"],
            {"below_min_profit": 2, "low_liquidity": 1},
        )

    def test_simulation_success_rate(self):
        m = MetricsCollector()
        # 3 passes, 1 fail → 75% success rate.
        for _ in range(3):
            m.record_simulation(passed=True)
        m.record_simulation(passed=False)
        snap = m.snapshot()
        self.assertEqual(snap["simulations_run"], 4)
        self.assertEqual(snap["simulations_passed"], 3)
        self.assertEqual(snap["simulation_success_rate_pct"], 75.0)

    def test_execution_result_inclusion_and_revert_rates(self):
        m = MetricsCollector()
        # 2 included, 1 reverted, 1 not-included → 50% inclusion, 25% revert.
        m.record_execution_result(included=True, reverted=False,
                                  fee_paid_lamports=5000, actual_profit=0.01)
        m.record_execution_result(included=True, reverted=False,
                                  fee_paid_lamports=6000, actual_profit=0.02)
        m.record_execution_result(included=False, reverted=True,
                                  fee_paid_lamports=5000)
        m.record_execution_result(included=False, reverted=False,
                                  fee_paid_lamports=0)
        snap = m.snapshot()
        self.assertEqual(snap["executions_included"], 2)
        self.assertEqual(snap["executions_reverted"], 1)
        self.assertEqual(snap["executions_not_included"], 1)
        self.assertEqual(snap["inclusion_rate_pct"], 50.0)
        self.assertEqual(snap["revert_rate_pct"], 25.0)
        self.assertEqual(snap["total_fee_paid_lamports"], 16000)
        self.assertAlmostEqual(snap["total_actual_profit"], 0.03, places=6)

    def test_expected_profit_accumulates(self):
        m = MetricsCollector()
        m.record_expected_profit(0.001)
        m.record_expected_profit(0.002)
        m.record_expected_profit(0.003)
        snap = m.snapshot()
        self.assertAlmostEqual(snap["total_expected_profit"], 0.006, places=6)

    def test_latency_stats(self):
        m = MetricsCollector()
        for v in [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]:
            m.record_latency_ms(v)
        snap = m.snapshot()
        self.assertEqual(snap["avg_latency_ms"], 55.0)
        # p95 of 10..100 is index int(10*0.95)=9 → 100ms
        self.assertEqual(snap["p95_latency_ms"], 100.0)

    def test_latency_bounded_to_1000_samples(self):
        m = MetricsCollector()
        # Record 1500 — internal buffer should trim to last 1000.
        for i in range(1500):
            m.record_latency_ms(float(i))
        # Internal state — reach in to verify the cap.
        self.assertEqual(len(m._c.latencies_ms), 1000)
        # The retained samples are the most recent 1000 (500..1499).
        self.assertEqual(m._c.latencies_ms[0], 500.0)
        self.assertEqual(m._c.latencies_ms[-1], 1499.0)

    def test_reset_clears_counters_and_resets_uptime(self):
        m = MetricsCollector()
        m.record_opportunity_detected()
        m.record_execution_submitted()
        m.record_latency_ms(50.0)
        # snapshot rounds uptime to 1 decimal — sleep past 0.1s so it's non-zero.
        time.sleep(0.15)
        pre_reset_uptime = m.snapshot()["uptime_seconds"]
        self.assertGreater(pre_reset_uptime, 0)

        m.reset()
        snap = m.snapshot()
        self.assertEqual(snap["opportunities_detected"], 0)
        self.assertEqual(snap["executions_submitted"], 0)
        self.assertEqual(snap["avg_latency_ms"], 0)
        # Uptime restarted → strictly less than before.
        self.assertLess(snap["uptime_seconds"], pre_reset_uptime)

    def test_opportunities_per_minute_uses_uptime(self):
        m = MetricsCollector()
        # snapshot() clamps uptime to at least 1 minute for the rate calc.
        m.record_opportunity_detected()
        m.record_opportunity_detected()
        snap = m.snapshot()
        # 2 opps in <1 min → reported as "per minute" clamped at 1-min floor.
        self.assertEqual(snap["opportunities_per_minute"], 2.0)

    def test_thread_safety(self):
        m = MetricsCollector()
        errors: list[Exception] = []

        def writer(n: int) -> None:
            try:
                for _ in range(n):
                    m.record_opportunity_detected()
                    m.record_opportunity_rejected("x")
                    m.record_latency_ms(1.0)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(200,)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        snap = m.snapshot()
        self.assertEqual(snap["opportunities_detected"], 1000)
        self.assertEqual(snap["opportunities_rejected"], 1000)
        self.assertEqual(snap["rejection_reasons"]["x"], 1000)


if __name__ == "__main__":
    unittest.main()
