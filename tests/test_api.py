"""Tests for the API control plane."""

import sys
import tempfile
from decimal import Decimal
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from persistence.db import init_db, close_db
from persistence.repository import Repository
from risk.policy import RiskPolicy
from api.app import create_app

D = Decimal


class _APITestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)
        self.policy = RiskPolicy(execution_enabled=False)
        app = create_app(risk_policy=self.policy, repo=self.repo, require_auth=False)
        self.client = TestClient(app)

    def tearDown(self):
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)


class HealthTests(_APITestBase):
    def test_health_returns_ok(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("execution_enabled", data)


class KillSwitchTests(_APITestBase):
    def test_get_execution_status(self):
        resp = self.client.get("/execution")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["execution_enabled"])

    def test_enable_execution(self):
        resp = self.client.post("/execution", json={"enabled": True})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["execution_enabled"])

        # Verify it persists
        resp2 = self.client.get("/execution")
        self.assertTrue(resp2.json()["execution_enabled"])

    def test_disable_execution(self):
        self.policy.execution_enabled = True
        resp = self.client.post("/execution", json={"enabled": False})
        self.assertFalse(resp.json()["execution_enabled"])


class RiskPolicyTests(_APITestBase):
    def test_get_policy(self):
        resp = self.client.get("/risk/policy")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("min_net_profit", data)
        self.assertIn("max_trades_per_hour", data)


class OpportunityEndpointTests(_APITestBase):
    def test_list_empty(self):
        resp = self.client.get("/opportunities")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_list_with_data(self):
        self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("42"),
        )
        resp = self.client.get("/opportunities")
        self.assertEqual(len(resp.json()), 1)

    def test_get_by_id(self):
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("42"),
        )
        resp = self.client.get(f"/opportunities/{opp_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["pair"], "WETH/USDC")

    def test_get_nonexistent_returns_404(self):
        resp = self.client.get("/opportunities/opp_doesnotexist")
        self.assertEqual(resp.status_code, 404)

    def test_get_pricing(self):
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("42"),
        )
        self.repo.save_pricing(opp_id, D("2200"), D("2210"), D("2"), D("1"), D("0.002"), D("0.005"))
        resp = self.client.get(f"/opportunities/{opp_id}/pricing")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["input_amount"], "2200")

    def test_get_risk_decision(self):
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("42"),
        )
        self.repo.save_risk_decision(opp_id, approved=True, reason_code="passed")
        resp = self.client.get(f"/opportunities/{opp_id}/risk")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["approved"], 1)


class AggregationEndpointTests(_APITestBase):
    def test_pnl_empty(self):
        resp = self.client.get("/pnl")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["total_trades"], 0)

    def test_funnel_empty(self):
        resp = self.client.get("/funnel")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {})

    def test_funnel_with_data(self):
        self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("10"),
        )
        opp2 = self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("20"),
        )
        self.repo.update_opportunity_status(opp2, "approved")

        resp = self.client.get("/funnel")
        data = resp.json()
        self.assertEqual(data["detected"], 1)
        self.assertEqual(data["approved"], 1)

    def test_operations_returns_operational_metadata(self):
        self.repo.set_checkpoint("discovery_snapshot_source", "db_cache")
        self.repo.set_checkpoint("discovery_pair_count", "7")
        self.repo.set_checkpoint("monitored_pools_synced", "3")

        pair_id = self.repo.save_pair(
            pair="WETH/USDC",
            chain="ethereum",
            base_token="WETH",
            quote_token="USDC",
        )
        self.repo.save_pool(
            pair_id=pair_id,
            chain="ethereum",
            dex="uniswap_v3",
            address="0xpool",
        )

        resp = self.client.get("/operations")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["db_backend"], "sqlite")
        self.assertEqual(data["enabled_pools_total"], 1)
        self.assertEqual(data["discovery_snapshot_source"], "db_cache")
        self.assertEqual(data["last_discovery_pair_count"], 7)
        self.assertEqual(data["last_monitored_pools_synced"], 3)

    def test_dashboard_html_includes_operations_section(self):
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn('id="operations-grid"', body)
        self.assertIn("fetchJSON('/operations')", body)


class PauseEndpointTests(_APITestBase):
    def test_get_pause_status(self):
        resp = self.client.get("/pause")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["paused"])

    def test_pause_and_resume(self):
        resp = self.client.post("/pause", json={"paused": True})
        self.assertTrue(resp.json()["paused"])
        resp2 = self.client.post("/pause", json={"paused": False})
        self.assertFalse(resp2.json()["paused"])


class ReplayEndpointTests(_APITestBase):
    def test_replay_existing_opportunity(self):
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("42"),
        )
        self.repo.save_pricing(opp_id, D("2200"), D("2210"), D("2"), D("1"), D("0.002"), D("0.005"))
        self.repo.save_risk_decision(opp_id, approved=True, reason_code="passed")

        resp = self.client.post(f"/opportunities/{opp_id}/replay")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("opportunity", data)
        self.assertIn("original_pricing", data)
        self.assertIn("replay_risk_verdict", data)
        self.assertIn("current_policy", data)

    def test_replay_nonexistent_returns_404(self):
        resp = self.client.post("/opportunities/opp_fake/replay")
        self.assertEqual(resp.status_code, 404)


class DiagnosticsEndpointTests(_APITestBase):
    def test_diagnostics_returns_empty_when_not_configured(self):
        resp = self.client.get("/diagnostics/quotes")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["dexes"], {})

    def test_diagnostics_returns_data_when_configured(self):
        from observability.quote_diagnostics import QuoteDiagnostics, QuoteOutcome
        import api.app as app_mod
        diag = QuoteDiagnostics()
        diag.record("Uniswap", "ethereum", "WETH/USDC", QuoteOutcome.SUCCESS, latency_ms=50.0)
        diag.record("Uniswap", "ethereum", "WETH/USDC", QuoteOutcome.ERROR, error_msg="timeout")
        old = app_mod._diagnostics
        app_mod._diagnostics = diag
        try:
            resp = self.client.get("/diagnostics/quotes")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("Uniswap", data["dexes"])
            entries = data["dexes"]["Uniswap"]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["total_quotes"], 2)
            self.assertEqual(entries[0]["success_count"], 1)
        finally:
            app_mod._diagnostics = old


if __name__ == "__main__":
    unittest.main()
