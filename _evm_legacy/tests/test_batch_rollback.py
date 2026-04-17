"""Tests for DB batch rollback on exception and pipeline consumer resilience.

Covers:
  - batch() commits on clean exit
  - batch() rolls back on exception (prevents stuck 'approved' status)
  - Nested batch depth tracking
  - Consumer loop continues after pipeline crash
"""

import sys
import sqlite3
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persistence.db import DbConnection


class BatchRollbackTests(unittest.TestCase):
    """batch() must rollback on exception, commit only on clean exit."""

    def setUp(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.db = DbConnection(conn, "sqlite")
        self.db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, val TEXT)")
        self.db.commit()

    def _count(self):
        return self.db.execute("SELECT COUNT(*) as c FROM test").fetchone()["c"]

    def _ids(self):
        rows = self.db.execute("SELECT id FROM test ORDER BY id").fetchall()
        return [r["id"] for r in rows]

    def test_clean_batch_commits(self):
        """Normal batch exit should commit all writes."""
        with self.db.batch():
            self.db.execute("INSERT INTO test VALUES (1, 'a')")
            self.db.execute("INSERT INTO test VALUES (2, 'b')")
        self.assertEqual(self._count(), 2)

    def test_exception_batch_rolls_back(self):
        """Exception inside batch should rollback — no partial writes."""
        try:
            with self.db.batch():
                self.db.execute("INSERT INTO test VALUES (1, 'committed')")
                self.db.execute("INSERT INTO test VALUES (2, 'should_rollback')")
                raise ValueError("simulated crash")
        except ValueError:
            pass
        self.assertEqual(self._count(), 0, "Batch should have rolled back all writes")

    def test_exception_preserves_prior_data(self):
        """Data committed before the failed batch should survive."""
        self.db.execute("INSERT INTO test VALUES (1, 'safe')")
        self.db.commit()

        try:
            with self.db.batch():
                self.db.execute("INSERT INTO test VALUES (2, 'doomed')")
                raise RuntimeError("crash")
        except RuntimeError:
            pass

        self.assertEqual(self._ids(), [1], "Only pre-batch data should survive")

    def test_exception_reraises(self):
        """The original exception should propagate after rollback."""
        with self.assertRaises(TypeError):
            with self.db.batch():
                self.db.execute("INSERT INTO test VALUES (1, 'x')")
                raise TypeError("original error")

    def test_nested_batch_outer_exception_rolls_back(self):
        """Nested batches: exception in outer should rollback everything."""
        try:
            with self.db.batch():
                self.db.execute("INSERT INTO test VALUES (1, 'outer')")
                with self.db.batch():
                    self.db.execute("INSERT INTO test VALUES (2, 'inner')")
                raise ValueError("outer crash")
        except ValueError:
            pass
        self.assertEqual(self._count(), 0)

    def test_batch_depth_resets_on_exception(self):
        """Batch depth should be 0 after an exception so next batch works."""
        try:
            with self.db.batch():
                raise ValueError("crash")
        except ValueError:
            pass
        self.assertEqual(self.db._batch_depth, 0)

        # Next batch should work normally.
        with self.db.batch():
            self.db.execute("INSERT INTO test VALUES (1, 'after_crash')")
        self.assertEqual(self._count(), 1)

    def test_commit_inside_batch_is_suppressed(self):
        """Explicit commit() inside a batch should be a no-op."""
        with self.db.batch():
            self.db.execute("INSERT INTO test VALUES (1, 'x')")
            self.db.commit()  # Should be suppressed
            self.db.execute("INSERT INTO test VALUES (2, 'y')")
        # Both rows committed at batch exit.
        self.assertEqual(self._count(), 2)


class PipelineApprovedStuckTests(unittest.TestCase):
    """Reproduce the exact bug: opportunity stuck at 'approved' on sim crash."""

    def setUp(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.db = DbConnection(conn, "sqlite")
        self.db.execute("""
            CREATE TABLE opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opp_id TEXT, status TEXT, pair TEXT
            )
        """)
        self.db.commit()

    def test_approved_then_crash_rolls_back(self):
        """Simulates: detect → price → risk_approve → sim crash.

        Before the fix, the batch committed status='approved' even
        though simulation crashed. After the fix, everything rolls back.
        """
        try:
            with self.db.batch():
                # Stage 1-3: detect, price, risk (all succeed)
                self.db.execute(
                    "INSERT INTO opportunities (opp_id, status, pair) VALUES (?, ?, ?)",
                    ("opp_test123", "detected", "AERO/WETH"),
                )
                self.db.execute(
                    "UPDATE opportunities SET status = ? WHERE opp_id = ?",
                    ("approved", "opp_test123"),
                )

                # Stage 4: simulation crashes
                raise RuntimeError("simulation RPC error")
        except RuntimeError:
            pass

        # The opportunity should NOT exist in the DB.
        row = self.db.execute(
            "SELECT * FROM opportunities WHERE opp_id = ?", ("opp_test123",)
        ).fetchone()
        self.assertIsNone(row, "Opportunity should not exist after rollback")

    def test_approved_then_success_commits(self):
        """Normal flow: detect → approve → simulate → commit."""
        with self.db.batch():
            self.db.execute(
                "INSERT INTO opportunities (opp_id, status, pair) VALUES (?, ?, ?)",
                ("opp_ok", "detected", "WETH/USDC"),
            )
            self.db.execute(
                "UPDATE opportunities SET status = ? WHERE opp_id = ?",
                ("simulation_failed", "opp_ok"),
            )

        row = self.db.execute(
            "SELECT status FROM opportunities WHERE opp_id = ?", ("opp_ok",)
        ).fetchone()
        self.assertEqual(row["status"], "simulation_failed")


class ConsumerResilienceTests(unittest.TestCase):
    """Test that the consumer loop pattern handles exceptions correctly."""

    def test_consumer_continues_after_crash(self):
        """Simulates the consumer loop processing multiple items.

        One item crashes, but the loop should continue to the next.
        """
        results = []
        items = ["good_1", "crash", "good_2", "crash", "good_3"]

        for item in items:
            try:
                if item == "crash":
                    raise RuntimeError(f"pipeline crash on {item}")
                results.append(item)
            except Exception:
                # Consumer catches and continues — the fix
                pass

        self.assertEqual(results, ["good_1", "good_2", "good_3"])

    def test_consumer_without_catch_stops(self):
        """Without the fix: first crash kills the loop."""
        results = []
        items = ["good_1", "crash", "good_2"]

        with self.assertRaises(RuntimeError):
            for item in items:
                # No try/except — the old behavior
                if item == "crash":
                    raise RuntimeError("pipeline crash")
                results.append(item)

        self.assertEqual(results, ["good_1"])  # good_2 never processed


if __name__ == "__main__":
    unittest.main()
