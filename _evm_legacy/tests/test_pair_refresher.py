"""Tests for the background pair refresher."""

import sys
import time
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from registry.discovery import DiscoveredPair
from registry.pair_refresher import PairRefresher
from persistence.db import init_db, close_db
from persistence.repository import Repository


def _fake_pairs(n=3):
    return [
        DiscoveredPair(
            pair_name=f"WETH/USDC-{i}",
            base_symbol="WETH",
            quote_symbol="USDC",
            chain="ethereum" if i % 2 == 0 else "arbitrum",
            dex_count=3,
            dex_names=["Uniswap", "SushiSwap", "PancakeSwap"],
            total_volume_24h=1_000_000 - i * 100_000,
            total_liquidity=5_000_000 - i * 500_000,
        )
        for i in range(n)
    ]


class BasicRefresherTests(unittest.TestCase):
    @patch("registry.pair_refresher.discover_best_pairs")
    def test_start_does_immediate_refresh(self, mock_discover):
        mock_discover.return_value = _fake_pairs()
        r = PairRefresher(interval_seconds=9999)
        r.start()
        r.stop()

        mock_discover.assert_called_once()
        self.assertEqual(r.pair_count, 3)

    @patch("registry.pair_refresher.discover_best_pairs")
    def test_get_pairs_returns_cached(self, mock_discover):
        mock_discover.return_value = _fake_pairs(5)
        r = PairRefresher(interval_seconds=9999)
        r.start()
        r.stop()

        pairs = r.get_pairs()
        self.assertEqual(len(pairs), 5)
        self.assertEqual(pairs[0].pair_name, "WETH/USDC-0")

    @patch("registry.pair_refresher.discover_best_pairs")
    def test_empty_before_start(self, mock_discover):
        r = PairRefresher()
        self.assertEqual(r.pair_count, 0)
        self.assertEqual(r.get_pairs(), [])

    @patch("registry.pair_refresher.discover_best_pairs")
    def test_stats(self, mock_discover):
        mock_discover.return_value = _fake_pairs(2)
        r = PairRefresher(interval_seconds=3600)
        r.start()
        r.stop()

        stats = r.stats()
        self.assertEqual(stats["pair_count"], 2)
        self.assertEqual(stats["refresh_count"], 1)
        self.assertEqual(stats["interval_minutes"], 60.0)
        self.assertIn("pairs", stats)

    @patch("registry.pair_refresher.discover_best_pairs")
    def test_refresh_failure_doesnt_crash(self, mock_discover):
        mock_discover.side_effect = Exception("API down")
        r = PairRefresher(interval_seconds=9999)
        r.start()
        r.stop()

        # Should not crash, just log error and return empty
        self.assertEqual(r.pair_count, 0)

    @patch("registry.pair_refresher.discover_best_pairs")
    def test_refresh_failure_keeps_old_data(self, mock_discover):
        mock_discover.return_value = _fake_pairs(3)
        r = PairRefresher(interval_seconds=9999)
        r.start()

        self.assertEqual(r.pair_count, 3)

        # Second refresh fails
        mock_discover.side_effect = Exception("API down")
        r._refresh()

        # Old data preserved
        self.assertEqual(r.pair_count, 3)
        r.stop()


class ThreadSafetyTests(unittest.TestCase):
    @patch("registry.pair_refresher.discover_best_pairs")
    def test_concurrent_access(self, mock_discover):
        mock_discover.return_value = _fake_pairs()
        r = PairRefresher(interval_seconds=9999)
        r.start()

        import threading
        errors = []

        def reader():
            for _ in range(20):
                try:
                    r.get_pairs()
                    r.stats()
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        r.stop()
        self.assertEqual(len(errors), 0)


class ConfigTests(unittest.TestCase):
    @patch("registry.pair_refresher.discover_best_pairs")
    def test_custom_chains(self, mock_discover):
        mock_discover.return_value = []
        r = PairRefresher(chains=["base", "optimism"], interval_seconds=9999)
        r.start()
        r.stop()

        call_kwargs = mock_discover.call_args[1]
        self.assertEqual(call_kwargs["chains"], ["base", "optimism"])

    @patch("registry.pair_refresher.discover_best_pairs")
    def test_custom_volume_threshold(self, mock_discover):
        mock_discover.return_value = []
        r = PairRefresher(min_volume=500_000, interval_seconds=9999)
        r.start()
        r.stop()

        call_kwargs = mock_discover.call_args[1]
        self.assertEqual(call_kwargs["min_volume"], 500_000)

    def test_default_interval_is_1_hour(self):
        r = PairRefresher()
        self.assertEqual(r.interval, 3600)


class RefreshTimingTests(unittest.TestCase):
    @patch("registry.pair_refresher.discover_best_pairs")
    def test_last_refresh_age(self, mock_discover):
        mock_discover.return_value = _fake_pairs()
        r = PairRefresher(interval_seconds=9999)
        r.start()
        r.stop()

        age = r.last_refresh_age_minutes
        self.assertGreaterEqual(age, 0)
        self.assertLess(age, 1)  # just refreshed

    def test_age_before_start(self):
        r = PairRefresher()
        self.assertEqual(r.last_refresh_age_minutes, -1)


class PersistenceWarmStartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self) -> None:
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    @patch("registry.pair_refresher.discover_best_pairs")
    def test_start_loads_cached_pairs_without_initial_network_refresh(self, mock_discover):
        self.repo.replace_discovered_pairs(_fake_pairs(2))
        r = PairRefresher(interval_seconds=9999, repository=self.repo)
        r.start()
        r.stop()

        mock_discover.assert_not_called()
        self.assertEqual(r.pair_count, 2)
        self.assertEqual(r.snapshot_source, "db_cache")

    @patch("registry.pair_refresher.discover_best_pairs")
    def test_refresh_persists_pairs_to_repository(self, mock_discover):
        mock_discover.return_value = _fake_pairs(3)
        r = PairRefresher(interval_seconds=9999, repository=self.repo)
        r.start()
        r.stop()

        stored = self.repo.get_discovered_pairs()
        self.assertEqual(len(stored), 3)
        self.assertEqual(stored[0].pair_name, "WETH/USDC-0")
        self.assertEqual(r.snapshot_source, "network")


if __name__ == "__main__":
    unittest.main()
