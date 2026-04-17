"""Tests for the circuit breaker auto-pause system."""

import sys
import time
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from platform_adapters import CircuitBreaker, CircuitBreakerConfig, BreakerState


class BasicStateTests(unittest.TestCase):
    def test_starts_closed(self):
        cb = CircuitBreaker()
        self.assertEqual(cb.state, BreakerState.CLOSED)
        allowed, reason = cb.allows_execution()
        self.assertTrue(allowed)
        self.assertEqual(reason, "circuit_closed")

    def test_reset(self):
        cb = CircuitBreaker()
        for _ in range(5):
            cb.record_revert()
        self.assertEqual(cb.state, BreakerState.OPEN)
        cb.reset()
        self.assertEqual(cb.state, BreakerState.CLOSED)


class RevertTripTests(unittest.TestCase):
    def test_trips_after_max_reverts(self):
        config = CircuitBreakerConfig(max_reverts=3, revert_window_seconds=60)
        cb = CircuitBreaker(config)
        cb.record_revert()
        cb.record_revert()
        self.assertEqual(cb.state, BreakerState.CLOSED)
        cb.record_revert()
        self.assertEqual(cb.state, BreakerState.OPEN)
        self.assertEqual(cb.trip_reason, "repeated_reverts")

    def test_does_not_trip_below_threshold(self):
        config = CircuitBreakerConfig(max_reverts=5)
        cb = CircuitBreaker(config)
        for _ in range(4):
            cb.record_revert()
        self.assertEqual(cb.state, BreakerState.CLOSED)

    def test_blocks_execution_when_open(self):
        config = CircuitBreakerConfig(max_reverts=2, cooldown_seconds=9999)
        cb = CircuitBreaker(config)
        cb.record_revert()
        cb.record_revert()
        allowed, reason = cb.allows_execution()
        self.assertFalse(allowed)
        self.assertIn("repeated_reverts", reason)


class RPCDegradationTests(unittest.TestCase):
    def test_trips_after_max_rpc_errors(self):
        config = CircuitBreakerConfig(max_rpc_errors=3, rpc_error_window_seconds=60)
        cb = CircuitBreaker(config)
        cb.record_rpc_error()
        cb.record_rpc_error()
        cb.record_rpc_error()
        self.assertEqual(cb.state, BreakerState.OPEN)
        self.assertEqual(cb.trip_reason, "rpc_degradation")


class StaleDataTests(unittest.TestCase):
    def test_trips_on_stale_data(self):
        config = CircuitBreakerConfig(max_stale_seconds=0.01)
        cb = CircuitBreaker(config)
        time.sleep(0.02)
        self.assertEqual(cb.state, BreakerState.OPEN)
        self.assertEqual(cb.trip_reason, "stale_data")

    def test_fresh_quote_prevents_stale_trip(self):
        config = CircuitBreakerConfig(max_stale_seconds=10)
        cb = CircuitBreaker(config)
        cb.record_fresh_quote()
        self.assertEqual(cb.state, BreakerState.CLOSED)


class BlockWindowTests(unittest.TestCase):
    def test_trips_on_block_window_exposure(self):
        config = CircuitBreakerConfig(max_trades_per_block_window=2, block_window_size=5)
        cb = CircuitBreaker(config)
        cb.record_trade_at_block(100)
        self.assertEqual(cb.state, BreakerState.CLOSED)
        cb.record_trade_at_block(102)
        self.assertEqual(cb.state, BreakerState.OPEN)
        self.assertEqual(cb.trip_reason, "block_window_exposure")

    def test_old_blocks_pruned(self):
        config = CircuitBreakerConfig(max_trades_per_block_window=3, block_window_size=5)
        cb = CircuitBreaker(config)
        cb.record_trade_at_block(100)
        cb.record_trade_at_block(101)
        # Block 110 is >5 away from 100,101 — they get pruned.
        cb.record_trade_at_block(110)
        self.assertEqual(cb.state, BreakerState.CLOSED)


class CooldownTests(unittest.TestCase):
    def test_transitions_to_half_open_after_cooldown(self):
        config = CircuitBreakerConfig(max_reverts=1, cooldown_seconds=0.01)
        cb = CircuitBreaker(config)
        cb.record_revert()
        self.assertEqual(cb.state, BreakerState.OPEN)
        time.sleep(0.02)
        self.assertEqual(cb.state, BreakerState.HALF_OPEN)
        allowed, reason = cb.allows_execution()
        self.assertTrue(allowed)
        self.assertEqual(reason, "half_open_probe")

    def test_success_in_half_open_resets_to_closed(self):
        config = CircuitBreakerConfig(max_reverts=1, cooldown_seconds=0.01)
        cb = CircuitBreaker(config)
        cb.record_revert()
        time.sleep(0.02)
        self.assertEqual(cb.state, BreakerState.HALF_OPEN)
        cb.record_execution_success()
        self.assertEqual(cb.state, BreakerState.CLOSED)


class ToDictTests(unittest.TestCase):
    def test_serializes(self):
        cb = CircuitBreaker()
        d = cb.to_dict()
        self.assertIn("state", d)
        self.assertIn("recent_reverts", d)
        self.assertIn("recent_rpc_errors", d)
        self.assertIn("seconds_since_fresh_quote", d)
        self.assertEqual(d["state"], "closed")


if __name__ == "__main__":
    unittest.main()
