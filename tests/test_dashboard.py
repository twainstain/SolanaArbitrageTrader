"""Tests for the dashboard API endpoints and time-windowed aggregations."""

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


class _DashboardTestBase(unittest.TestCase):
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

    def _seed_data(self):
        """Create some test opportunities for aggregation."""
        for i, chain in enumerate(["ethereum", "ethereum", "arbitrum", "base"]):
            opp_id = self.repo.create_opportunity(
                pair="WETH/USDC", chain=chain,
                buy_dex="Uni", sell_dex="Pancake", spread_bps=D("42"),
            )
            if i % 2 == 0:
                self.repo.update_opportunity_status(opp_id, "approved")
            else:
                self.repo.update_opportunity_status(opp_id, "rejected")


class DashboardHTMLTests(_DashboardTestBase):
    def test_dashboard_returns_html(self):
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("Arbitrage Trader Dashboard", resp.text)


class TimeWindowTests(_DashboardTestBase):
    def test_window_24h_empty(self):
        resp = self.client.get("/dashboard/window/24h")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["window"], "24h")
        self.assertEqual(data["chain"], "all")
        self.assertEqual(data["opportunities"]["total"], 0)

    def test_window_with_data(self):
        self._seed_data()
        resp = self.client.get("/dashboard/window/24h")
        data = resp.json()
        self.assertEqual(data["opportunities"]["total"], 4)

    def test_window_filtered_by_chain(self):
        self._seed_data()
        resp = self.client.get("/dashboard/window/24h?chain=ethereum")
        data = resp.json()
        self.assertEqual(data["chain"], "ethereum")
        self.assertEqual(data["opportunities"]["total"], 2)

    def test_all_windows(self):
        self._seed_data()
        resp = self.client.get("/dashboard/windows")
        data = resp.json()
        self.assertIn("15m", data)
        self.assertIn("1h", data)
        self.assertIn("24h", data)
        self.assertIn("1m", data)
        self.assertEqual(data["24h"]["opportunities"]["total"], 4)

    def test_invalid_window(self):
        resp = self.client.get("/dashboard/window/99h")
        data = resp.json()
        self.assertIn("error", data)


class ChainSummaryTests(_DashboardTestBase):
    def test_chains_empty(self):
        resp = self.client.get("/dashboard/chains")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_chains_with_data(self):
        self._seed_data()
        resp = self.client.get("/dashboard/chains")
        data = resp.json()
        self.assertGreater(len(data), 0)
        # Ethereum has the most (2), should be first.
        chains = [c["chain"] for c in data]
        self.assertIn("ethereum", chains)
        self.assertIn("arbitrum", chains)

    def test_chains_custom_window(self):
        self._seed_data()
        resp = self.client.get("/dashboard/chains?window=1h")
        self.assertEqual(resp.status_code, 200)


class ProfitAggregationTests(_DashboardTestBase):
    """Test profit aggregation in time windows."""

    def _seed_with_pricing(self):
        """Create opportunities with pricing data."""
        for i, (chain, profit) in enumerate([
            ("optimism", D("0.08")),
            ("optimism", D("0.05")),
            ("arbitrum", D("0.12")),
            ("ethereum", D("-0.002")),  # negative — should be excluded from profit
        ]):
            opp_id = self.repo.create_opportunity(
                pair="WETH/USDC", chain=chain,
                buy_dex=f"Uni-{chain.title()}", sell_dex=f"Sushi-{chain.title()}",
                spread_bps=D("5.0"),
            )
            self.repo.save_pricing(
                opp_id=opp_id,
                input_amount=D("2200"), estimated_output=D("2300"),
                fee_cost=D("10"), slippage_cost=D("3"),
                gas_estimate=D("0.002"), expected_net_profit=profit,
            )
            self.repo.update_opportunity_status(opp_id, "simulation_approved")

    def test_profit_in_window_response(self):
        self._seed_with_pricing()
        resp = self.client.get("/dashboard/window/24h")
        data = resp.json()
        self.assertIn("profit", data)
        p = data["profit"]
        self.assertIn("total_expected_profit", p)
        self.assertIn("avg_expected_profit", p)
        self.assertIn("max_expected_profit", p)
        self.assertIn("priced_count", p)

    def test_profit_totals_correct(self):
        self._seed_with_pricing()
        resp = self.client.get("/dashboard/window/24h")
        p = resp.json()["profit"]
        # Only positive profits: 0.08 + 0.05 + 0.12 = 0.25
        self.assertAlmostEqual(p["total_expected_profit"], 0.25, places=4)
        self.assertEqual(p["priced_count"], 3)  # 3 profitable, 1 negative excluded

    def test_profit_max_correct(self):
        self._seed_with_pricing()
        resp = self.client.get("/dashboard/window/24h")
        p = resp.json()["profit"]
        self.assertAlmostEqual(p["max_expected_profit"], 0.12, places=4)

    def test_profit_avg_correct(self):
        self._seed_with_pricing()
        resp = self.client.get("/dashboard/window/24h")
        p = resp.json()["profit"]
        # avg of 0.08, 0.05, 0.12 = 0.0833...
        self.assertAlmostEqual(p["avg_expected_profit"], 0.0833, places=3)

    def test_profit_filtered_by_chain(self):
        self._seed_with_pricing()
        resp = self.client.get("/dashboard/window/24h?chain=optimism")
        p = resp.json()["profit"]
        # Optimism: 0.08 + 0.05 = 0.13
        self.assertAlmostEqual(p["total_expected_profit"], 0.13, places=4)
        self.assertEqual(p["priced_count"], 2)

    def test_profit_empty_when_no_data(self):
        resp = self.client.get("/dashboard/window/24h")
        p = resp.json()["profit"]
        self.assertEqual(p["total_expected_profit"], 0)
        self.assertEqual(p["priced_count"], 0)

    def test_all_windows_include_profit(self):
        self._seed_with_pricing()
        resp = self.client.get("/dashboard/windows")
        data = resp.json()
        for key in ["5m", "15m", "1h", "24h"]:
            self.assertIn("profit", data[key], f"Missing profit in {key}")

    def test_dashboard_html_contains_profit_labels(self):
        resp = self.client.get("/dashboard")
        self.assertIn("Expected Profit", resp.text)
        self.assertIn("Avg Profit", resp.text)
        self.assertIn("Best Single Opp", resp.text)


if __name__ == "__main__":
    unittest.main()
