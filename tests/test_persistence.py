"""Tests for the persistence layer — DB schema, repository CRUD, candidate lifecycle."""

import sys
import tempfile
from decimal import Decimal
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persistence.db import init_db, close_db
from persistence.repository import Repository

D = Decimal


class DBSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self) -> None:
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_tables_created(self) -> None:
        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {r[0] for r in tables}
        expected = {
            "opportunities", "pricing_results", "risk_decisions",
            "simulations", "execution_attempts", "trade_results",
            "system_checkpoints",
        }
        self.assertTrue(expected.issubset(names))

    def test_init_is_idempotent(self) -> None:
        init_db(self.tmp.name)
        init_db(self.tmp.name)
        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        self.assertGreater(len(tables), 0)


class OpportunityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self) -> None:
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_create_and_get(self) -> None:
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="Uniswap", sell_dex="PancakeSwap",
            spread_bps=D("42"),
        )
        self.assertTrue(opp_id.startswith("opp_"))

        opp = self.repo.get_opportunity(opp_id)
        self.assertIsNotNone(opp)
        self.assertEqual(opp["pair"], "WETH/USDC")
        self.assertEqual(opp["status"], "detected")
        self.assertEqual(opp["buy_dex"], "Uniswap")

    def test_update_status(self) -> None:
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("10"),
        )
        self.repo.update_opportunity_status(opp_id, "priced")
        opp = self.repo.get_opportunity(opp_id)
        self.assertEqual(opp["status"], "priced")

    def test_recent_opportunities(self) -> None:
        for i in range(3):
            self.repo.create_opportunity(
                pair=f"PAIR{i}", chain="ethereum",
                buy_dex="A", sell_dex="B", spread_bps=D("10"),
            )
        recent = self.repo.get_recent_opportunities(limit=2)
        self.assertEqual(len(recent), 2)

    def test_count_since(self) -> None:
        self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("10"),
        )
        count = self.repo.count_opportunities_since("2020-01-01T00:00:00")
        self.assertEqual(count, 1)

    def test_get_nonexistent_returns_none(self) -> None:
        self.assertIsNone(self.repo.get_opportunity("opp_doesnotexist"))


class PricingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self) -> None:
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_save_and_get_pricing(self) -> None:
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("42"),
        )
        pid = self.repo.save_pricing(
            opp_id=opp_id,
            input_amount=D("2200"),
            estimated_output=D("2210"),
            fee_cost=D("2.20"),
            slippage_cost=D("1.10"),
            gas_estimate=D("0.002"),
            expected_net_profit=D("0.003"),
        )
        self.assertIsNotNone(pid)

        pricing = self.repo.get_pricing(opp_id)
        self.assertIsNotNone(pricing)
        self.assertEqual(pricing["input_amount"], "2200")
        self.assertEqual(pricing["expected_net_profit"], "0.003")


class RiskDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self) -> None:
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_save_approved(self) -> None:
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("42"),
        )
        self.repo.save_risk_decision(opp_id, approved=True, reason_code="passed_all")
        dec = self.repo.get_risk_decision(opp_id)
        self.assertEqual(dec["approved"], 1)
        self.assertEqual(dec["reason_code"], "passed_all")

    def test_save_rejected_with_snapshot(self) -> None:
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("5"),
        )
        self.repo.save_risk_decision(
            opp_id, approved=False, reason_code="below_min_profit",
            threshold_snapshot={"min_profit": "0.001", "actual": "0.0002"},
        )
        dec = self.repo.get_risk_decision(opp_id)
        self.assertEqual(dec["approved"], 0)
        import json
        snap = json.loads(dec["threshold_snapshot"])
        self.assertEqual(snap["min_profit"], "0.001")


class SimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self) -> None:
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_save_successful_simulation(self) -> None:
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("42"),
        )
        self.repo.save_simulation(opp_id, success=True, expected_net_profit=D("0.005"))
        sim = self.repo.get_simulation(opp_id)
        self.assertEqual(sim["success"], 1)

    def test_save_failed_simulation(self) -> None:
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("42"),
        )
        self.repo.save_simulation(opp_id, success=False, revert_reason="profit_below_minimum")
        sim = self.repo.get_simulation(opp_id)
        self.assertEqual(sim["success"], 0)
        self.assertEqual(sim["revert_reason"], "profit_below_minimum")


class ExecutionAndResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self) -> None:
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_full_execution_lifecycle(self) -> None:
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="Uniswap", sell_dex="PancakeSwap", spread_bps=D("42"),
        )

        exec_id = self.repo.save_execution_attempt(
            opp_id, submission_type="flashbots",
            tx_hash="0xabc123", target_block=12345,
        )
        self.assertIsNotNone(exec_id)

        self.repo.save_trade_result(
            execution_id=exec_id, included=True, reverted=False,
            gas_used=250_000, actual_net_profit=D("0.004"),
            block_number=12345,
        )

        result = self.repo.get_trade_result(exec_id)
        self.assertEqual(result["included"], 1)
        self.assertEqual(result["reverted"], 0)
        self.assertEqual(result["gas_used"], 250_000)
        self.assertEqual(result["actual_net_profit"], "0.004")


class CheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self) -> None:
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_set_and_get(self) -> None:
        self.repo.set_checkpoint("last_block", "12345")
        self.assertEqual(self.repo.get_checkpoint("last_block"), "12345")

    def test_upsert(self) -> None:
        self.repo.set_checkpoint("last_block", "100")
        self.repo.set_checkpoint("last_block", "200")
        self.assertEqual(self.repo.get_checkpoint("last_block"), "200")

    def test_get_missing_returns_none(self) -> None:
        self.assertIsNone(self.repo.get_checkpoint("nonexistent"))


class AggregationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self) -> None:
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_pnl_summary(self) -> None:
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("42"),
        )
        eid = self.repo.save_execution_attempt(opp_id, tx_hash="0x1")
        self.repo.save_trade_result(eid, included=True, actual_net_profit=D("0.005"))

        eid2 = self.repo.save_execution_attempt(opp_id, tx_hash="0x2")
        self.repo.save_trade_result(eid2, included=True, reverted=True, actual_net_profit=D("0"))

        summary = self.repo.get_pnl_summary()
        self.assertEqual(summary["total_trades"], 2)
        self.assertEqual(summary["successful"], 1)
        self.assertEqual(summary["reverted"], 1)

    def test_opportunity_funnel(self) -> None:
        self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("10"),
        )
        opp2 = self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("20"),
        )
        self.repo.update_opportunity_status(opp2, "approved")

        funnel = self.repo.get_opportunity_funnel()
        self.assertEqual(funnel["detected"], 1)
        self.assertEqual(funnel["approved"], 1)


class PostgresConfigTests(unittest.TestCase):
    def test_parse_database_url_defaults_to_sqlite(self):
        from persistence.db import _parse_database_url
        import os
        old = os.environ.pop("DATABASE_URL", None)
        try:
            backend, path = _parse_database_url()
            self.assertEqual(backend, "sqlite")
            self.assertTrue(path.endswith("arbitrage.db"))
        finally:
            if old:
                os.environ["DATABASE_URL"] = old

    def test_parse_postgres_url(self):
        from persistence.db import _parse_database_url
        import os
        os.environ["DATABASE_URL"] = "postgres://user:pass@host/db"
        try:
            backend, url = _parse_database_url()
            self.assertEqual(backend, "postgres")
            self.assertIn("user:pass", url)
        finally:
            del os.environ["DATABASE_URL"]

    def test_parse_postgresql_url(self):
        from persistence.db import _parse_database_url
        import os
        os.environ["DATABASE_URL"] = "postgresql://user:pass@host/db"
        try:
            backend, url = _parse_database_url()
            self.assertEqual(backend, "postgres")
        finally:
            del os.environ["DATABASE_URL"]

    def test_parse_sqlite_url(self):
        from persistence.db import _parse_database_url
        import os
        os.environ["DATABASE_URL"] = "sqlite:///tmp/test.db"
        try:
            backend, path = _parse_database_url()
            self.assertEqual(backend, "sqlite")
            self.assertEqual(path, "tmp/test.db")
        finally:
            del os.environ["DATABASE_URL"]

    def test_db_connection_backend_field(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        conn = init_db(tmp.name)
        self.assertEqual(conn.backend, "sqlite")
        close_db()
        Path(tmp.name).unlink(missing_ok=True)


class BatchCommitTests(unittest.TestCase):
    """Tests for the DbConnection.batch() context manager."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self) -> None:
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_batch_suppresses_intermediate_commits(self) -> None:
        with self.conn.batch():
            opp_id = self.repo.create_opportunity(
                pair="WETH/USDC", chain="ethereum",
                buy_dex="A", sell_dex="B", spread_bps=D("10"),
            )
            self.repo.update_opportunity_status(opp_id, "priced")
        opp = self.repo.get_opportunity(opp_id)
        self.assertEqual(opp["status"], "priced")

    def test_batch_commits_on_exit(self) -> None:
        with self.conn.batch():
            opp_id = self.repo.create_opportunity(
                pair="WETH/USDC", chain="ethereum",
                buy_dex="A", sell_dex="B", spread_bps=D("10"),
            )
        opp = self.repo.get_opportunity(opp_id)
        self.assertIsNotNone(opp)

    def test_nested_batch(self) -> None:
        with self.conn.batch():
            with self.conn.batch():
                opp_id = self.repo.create_opportunity(
                    pair="WETH/USDC", chain="ethereum",
                    buy_dex="A", sell_dex="B", spread_bps=D("10"),
                )
            # Inner batch exited — should NOT have committed yet.
            self.assertEqual(self.conn._batch_depth, 1)
        # Outer batch exited — should have committed.
        self.assertEqual(self.conn._batch_depth, 0)
        opp = self.repo.get_opportunity(opp_id)
        self.assertIsNotNone(opp)

    def test_batch_depth_counter(self) -> None:
        self.assertEqual(self.conn._batch_depth, 0)
        with self.conn.batch():
            self.assertEqual(self.conn._batch_depth, 1)
            with self.conn.batch():
                self.assertEqual(self.conn._batch_depth, 2)
            self.assertEqual(self.conn._batch_depth, 1)
        self.assertEqual(self.conn._batch_depth, 0)

    def test_sqlite_pragmas_applied(self) -> None:
        row = self.conn.execute("PRAGMA synchronous").fetchone()
        # NORMAL = 1
        self.assertEqual(row[0], 1)
        row = self.conn.execute("PRAGMA mmap_size").fetchone()
        self.assertEqual(row[0], 268435456)


if __name__ == "__main__":
    unittest.main()
