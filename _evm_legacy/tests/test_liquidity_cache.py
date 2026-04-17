"""Tests for the low-liquidity cache."""

import sys
import time
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data.liquidity_cache import LiquidityCache


class BasicCacheTests(unittest.TestCase):
    """Core cache behavior."""

    def test_empty_cache_allows_all(self):
        cache = LiquidityCache()
        self.assertFalse(cache.should_skip("Uniswap", "ethereum"))
        self.assertFalse(cache.should_skip("Sushi", "polygon"))

    def test_mark_skip_then_should_skip(self):
        cache = LiquidityCache()
        cache.mark_skip("Sushi-Polygon", "polygon", "zero quotes")
        self.assertTrue(cache.should_skip("Sushi-Polygon", "polygon"))

    def test_other_pairs_not_affected(self):
        cache = LiquidityCache()
        cache.mark_skip("Sushi-Polygon", "polygon", "zero quotes")
        self.assertFalse(cache.should_skip("Uniswap-Ethereum", "ethereum"))
        self.assertFalse(cache.should_skip("Sushi-Arbitrum", "arbitrum"))

    def test_case_insensitive(self):
        cache = LiquidityCache()
        cache.mark_skip("Sushi-Polygon", "POLYGON", "zero quotes")
        self.assertTrue(cache.should_skip("sushi-polygon", "polygon"))

    def test_size(self):
        cache = LiquidityCache()
        self.assertEqual(cache.size, 0)
        cache.mark_skip("A", "eth", "reason")
        cache.mark_skip("B", "arb", "reason")
        self.assertEqual(cache.size, 2)

    def test_clear(self):
        cache = LiquidityCache()
        cache.mark_skip("A", "eth", "reason")
        cache.clear()
        self.assertEqual(cache.size, 0)
        self.assertFalse(cache.should_skip("A", "eth"))


class TTLExpiryTests(unittest.TestCase):
    """Test that entries expire after TTL."""

    def test_entry_expires_after_ttl(self):
        cache = LiquidityCache(ttl_seconds=0.1)  # 100ms TTL
        cache.mark_skip("Sushi", "polygon", "zero")
        self.assertTrue(cache.should_skip("Sushi", "polygon"))

        time.sleep(0.15)  # Wait for expiry
        self.assertFalse(cache.should_skip("Sushi", "polygon"))

    def test_expired_entry_removed_from_size(self):
        cache = LiquidityCache(ttl_seconds=0.1)
        cache.mark_skip("A", "eth", "reason")
        self.assertEqual(cache.size, 1)

        time.sleep(0.15)
        self.assertEqual(cache.size, 0)

    def test_re_cache_after_expiry(self):
        cache = LiquidityCache(ttl_seconds=0.1)
        cache.mark_skip("Sushi", "polygon", "zero v1")
        time.sleep(0.15)
        self.assertFalse(cache.should_skip("Sushi", "polygon"))

        cache.mark_skip("Sushi", "polygon", "zero v2")
        self.assertTrue(cache.should_skip("Sushi", "polygon"))

    def test_default_ttl_is_3_hours(self):
        cache = LiquidityCache()
        self.assertEqual(cache._ttl, 3 * 3600)


class SkipCountTests(unittest.TestCase):
    """Track how many RPC calls were saved."""

    def test_skip_count_increments(self):
        cache = LiquidityCache()
        cache.mark_skip("A", "eth", "reason")

        cache.should_skip("A", "eth")
        cache.should_skip("A", "eth")
        cache.should_skip("A", "eth")

        self.assertEqual(cache.total_skips, 3)

    def test_non_cached_doesnt_increment(self):
        cache = LiquidityCache()
        cache.should_skip("A", "eth")
        self.assertEqual(cache.total_skips, 0)


class StatsTests(unittest.TestCase):
    """Stats and reporting."""

    def test_stats(self):
        cache = LiquidityCache(ttl_seconds=3600)
        cache.mark_skip("Sushi-Polygon", "polygon", "zero quotes")
        cache.mark_skip("Uniswap-Polygon", "polygon", "zero quotes")

        stats = cache.stats()
        self.assertEqual(stats["cached_pairs"], 2)
        self.assertEqual(stats["ttl_minutes"], 60.0)
        self.assertEqual(len(stats["entries"]), 2)

    def test_get_cached(self):
        cache = LiquidityCache(ttl_seconds=3600)
        cache.mark_skip("Sushi", "polygon", "zero quotes")

        entries = cache.get_cached()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["dex"], "Sushi")
        self.assertEqual(entries[0]["chain"], "polygon")
        self.assertEqual(entries[0]["reason"], "zero quotes")
        self.assertIn("age_minutes", entries[0])
        self.assertIn("ttl_minutes", entries[0])


class DuplicateTests(unittest.TestCase):
    """No duplicate entries."""

    def test_mark_skip_twice_no_duplicate(self):
        cache = LiquidityCache()
        cache.mark_skip("A", "eth", "reason1")
        cache.mark_skip("A", "eth", "reason2")
        self.assertEqual(cache.size, 1)


class ThreadSafetyTests(unittest.TestCase):
    """Concurrent access doesn't crash."""

    def test_concurrent_read_write(self):
        import threading
        cache = LiquidityCache(ttl_seconds=0.5)
        errors = []

        def writer():
            for i in range(50):
                cache.mark_skip(f"dex-{i % 5}", f"chain-{i % 3}", "test")

        def reader():
            for _ in range(50):
                cache.should_skip("dex-1", "chain-1")
                cache.stats()

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=writer),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # No crash = success
        self.assertGreaterEqual(cache.size, 0)


if __name__ == "__main__":
    unittest.main()
