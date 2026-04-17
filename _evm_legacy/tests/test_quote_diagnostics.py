import sys
import threading
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from observability.quote_diagnostics import QuoteDiagnostics, QuoteOutcome


class QuoteDiagnosticsTests(unittest.TestCase):
    def test_record_and_snapshot(self) -> None:
        diag = QuoteDiagnostics()
        diag.record("Uniswap", "ethereum", "WETH/USDC", QuoteOutcome.SUCCESS, latency_ms=50.0)
        diag.record("Uniswap", "ethereum", "WETH/USDC", QuoteOutcome.SUCCESS, latency_ms=60.0)
        diag.record("Uniswap", "ethereum", "WETH/USDC", QuoteOutcome.ERROR, error_msg="timeout")

        snap = diag.snapshot()
        key = "Uniswap:ethereum:WETH/USDC"
        self.assertIn(key, snap)
        self.assertEqual(snap[key]["total_quotes"], 3)
        self.assertEqual(snap[key]["success_count"], 2)

    def test_success_rate_calculation(self) -> None:
        diag = QuoteDiagnostics()
        for _ in range(3):
            diag.record("Sushi", "arbitrum", "WETH/USDC", QuoteOutcome.SUCCESS)
        for _ in range(2):
            diag.record("Sushi", "arbitrum", "WETH/USDC", QuoteOutcome.ERROR, error_msg="rpc")

        snap = diag.snapshot()
        key = "Sushi:arbitrum:WETH/USDC"
        self.assertAlmostEqual(snap[key]["success_rate"], 0.6, places=2)

    def test_different_keys_tracked_independently(self) -> None:
        diag = QuoteDiagnostics()
        diag.record("Uni", "eth", "WETH/USDC", QuoteOutcome.SUCCESS)
        diag.record("Sushi", "eth", "WETH/USDC", QuoteOutcome.ERROR, error_msg="fail")

        snap = diag.snapshot()
        self.assertEqual(len(snap), 2)
        self.assertEqual(snap["Uni:eth:WETH/USDC"]["success_count"], 1)
        self.assertEqual(snap["Sushi:eth:WETH/USDC"]["success_count"], 0)

    def test_max_history_limit(self) -> None:
        diag = QuoteDiagnostics(max_history=5)
        for i in range(10):
            diag.record("Uni", "eth", "WETH/USDC", QuoteOutcome.SUCCESS, latency_ms=float(i))

        snap = diag.snapshot()
        self.assertEqual(snap["Uni:eth:WETH/USDC"]["total_quotes"], 5)

    def test_thread_safety(self) -> None:
        diag = QuoteDiagnostics(max_history=200)
        errors = []

        def writer(dex: str, n: int) -> None:
            try:
                for _ in range(n):
                    diag.record(dex, "eth", "WETH/USDC", QuoteOutcome.SUCCESS)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(f"DEX{i}", 100)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        snap = diag.snapshot()
        total = sum(v["total_quotes"] for v in snap.values())
        self.assertEqual(total, 500)

    def test_last_error_tracked(self) -> None:
        diag = QuoteDiagnostics()
        diag.record("Velo", "optimism", "OP/USDC", QuoteOutcome.ERROR, error_msg="zero returned")

        snap = diag.snapshot()
        self.assertEqual(snap["Velo:optimism:OP/USDC"]["last_error"], "zero returned")

    def test_db_persistence(self) -> None:
        """Verify snapshot can be persisted to DB via repository."""
        import tempfile
        diag = QuoteDiagnostics()
        diag.record("Uniswap", "ethereum", "WETH/USDC", QuoteOutcome.SUCCESS, latency_ms=50.0)
        diag.record("Sushi", "arbitrum", "WETH/USDC", QuoteOutcome.ERROR, error_msg="timeout")

        from persistence.db import init_db, close_db
        from persistence.repository import Repository

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        try:
            conn = init_db(tmp.name)
            repo = Repository(conn)
            snap = diag.snapshot()
            count = repo.save_diagnostics_snapshot(snap)
            self.assertEqual(count, 2)

            # Verify rows in DB.
            rows = conn.execute("SELECT * FROM quote_diagnostics").fetchall()
            self.assertEqual(len(rows), 2)
        finally:
            close_db()
            from pathlib import Path
            Path(tmp.name).unlink(missing_ok=True)

    def test_cached_skip_tracked(self) -> None:
        diag = QuoteDiagnostics()
        diag.record("Sushi", "base", "WETH/USDC", QuoteOutcome.CACHED_SKIP)

        snap = diag.snapshot()
        self.assertEqual(snap["Sushi:base:WETH/USDC"]["last_outcome"], "cached_skip")
        self.assertEqual(snap["Sushi:base:WETH/USDC"]["success_count"], 0)


if __name__ == "__main__":
    unittest.main()
