"""Tests for the observability metrics collector."""

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from observability.metrics import MetricsCollector


class BasicCounterTests(unittest.TestCase):
    def test_initial_snapshot_is_zero(self):
        m = MetricsCollector()
        s = m.snapshot()
        self.assertEqual(s["opportunities_detected"], 0)
        self.assertEqual(s["executions_submitted"], 0)

    def test_record_opportunity(self):
        m = MetricsCollector()
        m.record_opportunity_detected()
        m.record_opportunity_detected()
        s = m.snapshot()
        self.assertEqual(s["opportunities_detected"], 2)

    def test_record_rejection(self):
        m = MetricsCollector()
        m.record_opportunity_rejected("below_min_profit")
        m.record_opportunity_rejected("below_min_profit")
        m.record_opportunity_rejected("gas_too_expensive")
        s = m.snapshot()
        self.assertEqual(s["opportunities_rejected"], 3)
        self.assertEqual(s["rejection_reasons"]["below_min_profit"], 2)
        self.assertEqual(s["rejection_reasons"]["gas_too_expensive"], 1)

    def test_record_simulation(self):
        m = MetricsCollector()
        m.record_simulation(passed=True)
        m.record_simulation(passed=True)
        m.record_simulation(passed=False)
        s = m.snapshot()
        self.assertEqual(s["simulations_run"], 3)
        self.assertEqual(s["simulations_passed"], 2)
        self.assertAlmostEqual(s["simulation_success_rate_pct"], 66.7, delta=0.1)


class ExecutionMetricsTests(unittest.TestCase):
    def test_execution_included(self):
        m = MetricsCollector()
        m.record_execution_submitted()
        m.record_execution_result(included=True, reverted=False, gas_used=250000, actual_profit=0.005)
        s = m.snapshot()
        self.assertEqual(s["executions_submitted"], 1)
        self.assertEqual(s["executions_included"], 1)
        self.assertEqual(s["inclusion_rate_pct"], 100.0)
        self.assertAlmostEqual(s["total_actual_profit"], 0.005)
        self.assertEqual(s["total_gas_used"], 250000)

    def test_execution_reverted(self):
        m = MetricsCollector()
        m.record_execution_result(included=True, reverted=True)
        s = m.snapshot()
        self.assertEqual(s["executions_reverted"], 1)
        self.assertEqual(s["revert_rate_pct"], 100.0)

    def test_execution_not_included(self):
        m = MetricsCollector()
        m.record_execution_result(included=False, reverted=False)
        s = m.snapshot()
        self.assertEqual(s["executions_not_included"], 1)

    def test_mixed_results(self):
        m = MetricsCollector()
        m.record_execution_result(included=True, reverted=False, actual_profit=0.01)
        m.record_execution_result(included=True, reverted=True)
        m.record_execution_result(included=False, reverted=False)
        s = m.snapshot()
        self.assertEqual(s["executions_included"], 1)
        self.assertEqual(s["executions_reverted"], 1)
        self.assertEqual(s["executions_not_included"], 1)
        self.assertAlmostEqual(s["inclusion_rate_pct"], 33.3, delta=0.1)


class LatencyTests(unittest.TestCase):
    def test_latency_tracking(self):
        m = MetricsCollector()
        m.record_latency_ms(100)
        m.record_latency_ms(200)
        m.record_latency_ms(300)
        s = m.snapshot()
        self.assertAlmostEqual(s["avg_latency_ms"], 200.0)
        self.assertGreater(s["p95_latency_ms"], 0)

    def test_empty_latency(self):
        m = MetricsCollector()
        s = m.snapshot()
        self.assertEqual(s["avg_latency_ms"], 0)
        self.assertEqual(s["p95_latency_ms"], 0)


class ResetTests(unittest.TestCase):
    def test_reset_clears_everything(self):
        m = MetricsCollector()
        m.record_opportunity_detected()
        m.record_execution_result(included=True, reverted=False, actual_profit=0.01)
        m.reset()
        s = m.snapshot()
        self.assertEqual(s["opportunities_detected"], 0)
        self.assertEqual(s["executions_included"], 0)
        self.assertAlmostEqual(s["total_actual_profit"], 0)


class UptimeTests(unittest.TestCase):
    def test_uptime_non_negative(self):
        m = MetricsCollector()
        s = m.snapshot()
        self.assertGreaterEqual(s["uptime_seconds"], 0)

    def test_opps_per_minute(self):
        m = MetricsCollector()
        m.record_opportunity_detected()
        s = m.snapshot()
        # Should be > 0 since we detected one in < 1 minute
        self.assertGreater(s["opportunities_per_minute"], 0)


if __name__ == "__main__":
    unittest.main()
