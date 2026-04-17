"""Tests for retry logic and RPC failover."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from platform_adapters import RetryPolicy, execute_with_retry, config_hash
from data.rpc_failover import RpcProvider


# ---------------------------------------------------------------
# Retry tests
# ---------------------------------------------------------------

class RetrySuccessTests(unittest.TestCase):
    def test_succeeds_first_try(self):
        result = execute_with_retry(
            execute_fn=lambda: (True, "ok"),
            policy=RetryPolicy(max_retries=2, delay_seconds=0),
        )
        self.assertTrue(result.success)
        self.assertEqual(result.attempts, 1)

    def test_succeeds_on_retry(self):
        attempts = [0]
        def execute():
            attempts[0] += 1
            if attempts[0] < 3:
                return False, "transient_error"
            return True, "ok"

        result = execute_with_retry(
            execute_fn=execute,
            policy=RetryPolicy(max_retries=2, delay_seconds=0),
        )
        self.assertTrue(result.success)
        self.assertEqual(result.attempts, 3)


class RetryExhaustionTests(unittest.TestCase):
    def test_fails_after_max_retries(self):
        result = execute_with_retry(
            execute_fn=lambda: (False, "always_fails"),
            policy=RetryPolicy(max_retries=2, delay_seconds=0),
        )
        self.assertFalse(result.success)
        self.assertEqual(result.attempts, 3)  # 1 initial + 2 retries
        self.assertEqual(result.last_reason, "always_fails")


class RetryReEvaluationTests(unittest.TestCase):
    def test_aborts_if_no_longer_profitable(self):
        calls = [0]
        def execute():
            calls[0] += 1
            return False, "failed"

        result = execute_with_retry(
            execute_fn=execute,
            is_still_profitable=lambda: False,
            policy=RetryPolicy(max_retries=2, delay_seconds=0),
        )
        self.assertFalse(result.success)
        self.assertEqual(calls[0], 1)  # only tried once, aborted before retry
        self.assertIn("not_profitable", result.last_reason)

    def test_retries_if_still_profitable(self):
        calls = [0]
        def execute():
            calls[0] += 1
            if calls[0] < 2:
                return False, "transient"
            return True, "ok"

        result = execute_with_retry(
            execute_fn=execute,
            is_still_profitable=lambda: True,
            policy=RetryPolicy(max_retries=2, delay_seconds=0),
        )
        self.assertTrue(result.success)
        self.assertEqual(calls[0], 2)


class ConfigHashTests(unittest.TestCase):
    def test_deterministic(self):
        d = {"trade_size": "1.0", "min_profit": "0.001"}
        self.assertEqual(config_hash(d), config_hash(d))

    def test_different_configs_different_hash(self):
        d1 = {"trade_size": "1.0"}
        d2 = {"trade_size": "2.0"}
        self.assertNotEqual(config_hash(d1), config_hash(d2))

    def test_returns_16_chars(self):
        self.assertEqual(len(config_hash({"a": 1})), 16)


# ---------------------------------------------------------------
# RPC Failover tests
# ---------------------------------------------------------------

class RpcProviderBasicTests(unittest.TestCase):
    def test_creates_with_urls(self):
        rpc = RpcProvider("ethereum", ["https://a.com", "https://b.com"])
        self.assertEqual(rpc.endpoint_count, 2)
        self.assertEqual(rpc.chain, "ethereum")

    def test_raises_with_empty_urls(self):
        with self.assertRaises(ValueError):
            RpcProvider("ethereum", [])

    def test_current_url(self):
        rpc = RpcProvider("ethereum", ["https://a.com", "https://b.com"])
        self.assertEqual(rpc.current_url, "https://a.com")


class RpcFailoverTests(unittest.TestCase):
    def test_rotates_after_max_errors(self):
        rpc = RpcProvider("ethereum", ["https://a.com", "https://b.com"],
                          max_errors_before_disable=2, backoff_seconds=60)
        rpc.record_error()
        self.assertEqual(rpc.current_url, "https://a.com")
        rpc.record_error()
        # Should have rotated to b
        self.assertEqual(rpc.current_url, "https://b.com")

    def test_success_resets_error_count(self):
        rpc = RpcProvider("ethereum", ["https://a.com", "https://b.com"],
                          max_errors_before_disable=3)
        rpc.record_error()
        rpc.record_error()
        rpc.record_success()
        # Error count reset — third error should NOT rotate.
        rpc.record_error()
        self.assertEqual(rpc.current_url, "https://a.com")

    def test_all_disabled_reenables_best(self):
        rpc = RpcProvider("ethereum", ["https://a.com", "https://b.com"],
                          max_errors_before_disable=1, backoff_seconds=9999)
        rpc.record_error()  # disables a, rotates to b
        rpc.record_error()  # disables b, rotates to a (but a also disabled)
        # get_web3 should re-enable the least-recently-errored one.
        url = rpc._select_endpoint()
        self.assertIn(url, ["https://a.com", "https://b.com"])


class RpcToDictTests(unittest.TestCase):
    def test_serializes(self):
        rpc = RpcProvider("ethereum", ["https://a.com"])
        d = rpc.to_dict()
        self.assertEqual(d["chain"], "ethereum")
        self.assertEqual(len(d["endpoints"]), 1)
        self.assertFalse(d["endpoints"][0]["disabled"])


if __name__ == "__main__":
    unittest.main()
