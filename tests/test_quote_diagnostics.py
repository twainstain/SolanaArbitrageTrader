"""Tests for observability.quote_diagnostics.

Solana-adapted: keys are ``venue:pair`` (no chain). Each venue is already
Solana-specific (Jupiter-Best, Jupiter-Direct, Raydium-SOL/USDC, Orca-*).
"""

import sys
import threading
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "trading_platform" / "src"))

from observability.quote_diagnostics import QuoteDiagnostics, QuoteOutcome


class QuoteDiagnosticsTests(unittest.TestCase):
    def test_record_and_snapshot(self) -> None:
        diag = QuoteDiagnostics()
        diag.record("Jupiter-Best", "SOL/USDC", QuoteOutcome.SUCCESS, latency_ms=50.0)
        diag.record("Jupiter-Best", "SOL/USDC", QuoteOutcome.SUCCESS, latency_ms=60.0)
        diag.record("Jupiter-Best", "SOL/USDC", QuoteOutcome.ERROR, error_msg="timeout")

        snap = diag.snapshot()
        key = "Jupiter-Best:SOL/USDC"
        self.assertIn(key, snap)
        self.assertEqual(snap[key]["total_quotes"], 3)
        self.assertEqual(snap[key]["success_count"], 2)

    def test_success_rate_calculation(self) -> None:
        diag = QuoteDiagnostics()
        for _ in range(3):
            diag.record("Raydium-SOL/USDC", "SOL/USDC", QuoteOutcome.SUCCESS)
        for _ in range(2):
            diag.record("Raydium-SOL/USDC", "SOL/USDC", QuoteOutcome.ERROR, error_msg="rpc")

        snap = diag.snapshot()
        key = "Raydium-SOL/USDC:SOL/USDC"
        self.assertAlmostEqual(snap[key]["success_rate"], 0.6, places=2)

    def test_different_keys_tracked_independently(self) -> None:
        diag = QuoteDiagnostics()
        diag.record("Jupiter-Best", "SOL/USDC", QuoteOutcome.SUCCESS)
        diag.record("Orca-SOL/USDC", "SOL/USDC", QuoteOutcome.ERROR, error_msg="fail")

        snap = diag.snapshot()
        self.assertEqual(len(snap), 2)
        self.assertEqual(snap["Jupiter-Best:SOL/USDC"]["success_count"], 1)
        self.assertEqual(snap["Orca-SOL/USDC:SOL/USDC"]["success_count"], 0)

    def test_max_history_limit(self) -> None:
        diag = QuoteDiagnostics(max_history=5)
        for i in range(10):
            diag.record("Jupiter-Best", "SOL/USDC", QuoteOutcome.SUCCESS, latency_ms=float(i))

        snap = diag.snapshot()
        # deque max_len=5 should retain only the 5 most recent records.
        self.assertEqual(snap["Jupiter-Best:SOL/USDC"]["total_quotes"], 5)

    def test_avg_latency_only_counts_nonzero(self) -> None:
        diag = QuoteDiagnostics()
        diag.record("Jupiter-Best", "SOL/USDC", QuoteOutcome.SUCCESS, latency_ms=100.0)
        diag.record("Jupiter-Best", "SOL/USDC", QuoteOutcome.SUCCESS, latency_ms=200.0)
        diag.record("Jupiter-Best", "SOL/USDC", QuoteOutcome.ERROR, error_msg="rpc")  # latency=0

        snap = diag.snapshot()
        # avg of 100 + 200 = 150 (zero-latency record excluded).
        self.assertEqual(snap["Jupiter-Best:SOL/USDC"]["avg_latency_ms"], 150.0)

    def test_thread_safety(self) -> None:
        diag = QuoteDiagnostics(max_history=200)
        errors: list[Exception] = []

        def writer(venue: str, n: int) -> None:
            try:
                for _ in range(n):
                    diag.record(venue, "SOL/USDC", QuoteOutcome.SUCCESS)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer, args=(f"Venue{i}", 100))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        snap = diag.snapshot()
        total = sum(v["total_quotes"] for v in snap.values())
        self.assertEqual(total, 500)

    def test_last_error_tracked(self) -> None:
        diag = QuoteDiagnostics()
        diag.record(
            "Orca-USDC/USDT", "USDC/USDT",
            QuoteOutcome.ERROR, error_msg="pool not found",
        )

        snap = diag.snapshot()
        self.assertEqual(
            snap["Orca-USDC/USDT:USDC/USDT"]["last_error"],
            "pool not found",
        )

    def test_cached_skip_tracked(self) -> None:
        diag = QuoteDiagnostics()
        diag.record("Raydium-SOL/USDC", "SOL/USDC", QuoteOutcome.CACHED_SKIP)

        snap = diag.snapshot()
        self.assertEqual(
            snap["Raydium-SOL/USDC:SOL/USDC"]["last_outcome"],
            "cached_skip",
        )
        self.assertEqual(snap["Raydium-SOL/USDC:SOL/USDC"]["success_count"], 0)

    def test_empty_snapshot(self) -> None:
        diag = QuoteDiagnostics()
        self.assertEqual(diag.snapshot(), {})

    def test_db_persistence(self) -> None:
        """snapshot() → repo.save_diagnostics_snapshot() → DB round-trip."""
        from persistence.db import init_db, close_db
        from persistence.repository import Repository

        diag = QuoteDiagnostics()
        diag.record("Jupiter-Best", "SOL/USDC", QuoteOutcome.SUCCESS, latency_ms=50.0)
        diag.record("Orca-SOL/USDC", "SOL/USDC", QuoteOutcome.ERROR, error_msg="timeout")

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        try:
            conn = init_db(tmp.name)
            repo = Repository(conn)
            snap = diag.snapshot()
            count = repo.save_diagnostics_snapshot(snap)
            self.assertEqual(count, 2)

            rows = conn.execute(
                "SELECT venue, pair, success_count, total_count FROM quote_diagnostics ORDER BY venue"
            ).fetchall()
            self.assertEqual(len(rows), 2)
            venues = {row["venue"] for row in rows}
            self.assertEqual(venues, {"Jupiter-Best", "Orca-SOL/USDC"})
        finally:
            close_db()
            Path(tmp.name).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
