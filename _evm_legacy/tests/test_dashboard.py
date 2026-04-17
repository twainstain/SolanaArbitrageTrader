"""Tests for the dashboard API endpoints, time-windowed aggregations, and custom ranges."""

import sys
import tempfile
from datetime import datetime, timedelta, timezone
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

    def _seed_full_lifecycle(self):
        """Seed opportunity through full lifecycle: detect -> price -> risk -> sim -> execute -> result."""
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="arbitrum",
            buy_dex="Uniswap-Arb", sell_dex="Sushi-Arb", spread_bps=D("55"),
        )
        self.repo.save_pricing(
            opp_id, D("2200"), D("2215"), D("5"), D("2"),
            D("0.003"), D("0.008"),
        )
        self.repo.save_risk_decision(opp_id, approved=True, reason_code="passed")
        self.repo.save_simulation(opp_id, success=True, expected_net_profit=D("0.008"))
        exec_id = self.repo.save_execution_attempt(
            opp_id, submission_type="flashbots",
            tx_hash="0xabcdef1234", target_block=19000001,
        )
        self.repo.save_trade_result(
            exec_id, included=True, gas_used=250000,
            realized_profit_quote=D("12.5"), gas_cost_base=D("0.002"),
            profit_currency="USDC", actual_net_profit=D("0.006"),
            block_number=19000002,
        )
        self.repo.update_opportunity_status(opp_id, "included")
        return opp_id


# ──────────────────────────────────────────────────────────────────────
# Dashboard HTML Tests
# ──────────────────────────────────────────────────────────────────────

class DashboardHTMLTests(_DashboardTestBase):
    def test_dashboard_returns_html(self):
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("Arbitrage Trader Dashboard", resp.text)

    def test_dashboard_contains_est_label(self):
        resp = self.client.get("/dashboard")
        self.assertIn("EST", resp.text)

    def test_dashboard_contains_range_inputs(self):
        resp = self.client.get("/dashboard")
        self.assertIn("range-start", resp.text)
        self.assertIn("range-end", resp.text)
        self.assertIn("applyRange", resp.text)
        self.assertIn("clearRange", resp.text)

    def test_dashboard_contains_toest_function(self):
        resp = self.client.get("/dashboard")
        self.assertIn("toEST", resp.text)
        self.assertIn("America/New_York", resp.text)

    def test_dashboard_no_cache_headers(self):
        resp = self.client.get("/dashboard")
        self.assertIn("no-cache", resp.headers.get("cache-control", ""))

    def test_ops_dashboard_contains_live_readiness_labels(self):
        resp = self.client.get("/ops")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Live Stack", resp.text)
        self.assertIn("Launch Ready", resp.text)
        self.assertIn("Executable Chains", resp.text)
        self.assertIn("Executable Venues", resp.text)
        self.assertIn("Executor Config", resp.text)
        self.assertIn("Realized Quote Profit", resp.text)
        self.assertIn("Gas Cost (Base)", resp.text)
        self.assertIn("Net Profit (Base)", resp.text)

    def test_opportunity_detail_page_loads(self):
        resp = self.client.get("/opportunity/opp_test123")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("Opportunity Detail", resp.text)

    def test_opportunity_detail_has_toest(self):
        resp = self.client.get("/opportunity/opp_test123")
        self.assertIn("toEST", resp.text)
        self.assertIn("America/New_York", resp.text)


# ──────────────────────────────────────────────────────────────────────
# Time Window Tests
# ──────────────────────────────────────────────────────────────────────

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

    def test_window_5m_returns_data(self):
        self._seed_data()
        resp = self.client.get("/dashboard/window/5m")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["window"], "5m")
        # Data just created should be within 5m.
        self.assertEqual(data["opportunities"]["total"], 4)

    def test_window_1w_returns_data(self):
        self._seed_data()
        resp = self.client.get("/dashboard/window/1w")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["opportunities"]["total"], 4)


# ──────────────────────────────────────────────────────────────────────
# Custom Range Tests
# ──────────────────────────────────────────────────────────────────────

class CustomRangeTests(_DashboardTestBase):
    def test_range_returns_data(self):
        self._seed_data()
        start = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        resp = self.client.get(f"/dashboard/range?start={start}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["window"], "custom")
        self.assertEqual(data["opportunities"]["total"], 4)

    def test_range_with_end(self):
        self._seed_data()
        start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        resp = self.client.get(f"/dashboard/range?start={start}&end={end}")
        data = resp.json()
        self.assertEqual(data["opportunities"]["total"], 4)
        self.assertIn("start", data)
        self.assertIn("end", data)

    def test_range_excludes_older_data(self):
        self._seed_data()
        # Start in the future — should find nothing.
        start = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        resp = self.client.get(f"/dashboard/range?start={start}")
        data = resp.json()
        self.assertEqual(data["opportunities"]["total"], 0)

    def test_range_filtered_by_chain(self):
        self._seed_data()
        start = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        resp = self.client.get(f"/dashboard/range?start={start}&chain=arbitrum")
        data = resp.json()
        self.assertEqual(data["chain"], "arbitrum")
        self.assertEqual(data["opportunities"]["total"], 1)

    def test_range_includes_profit_and_trades(self):
        self._seed_full_lifecycle()
        start = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        resp = self.client.get(f"/dashboard/range?start={start}")
        data = resp.json()
        self.assertIn("trades", data)
        self.assertIn("profit", data)
        self.assertGreaterEqual(data["trades"]["total_trades"], 1)

    def test_range_missing_start_returns_422(self):
        resp = self.client.get("/dashboard/range")
        self.assertEqual(resp.status_code, 422)


# ──────────────────────────────────────────────────────────────────────
# Opportunities with start/end params
# ──────────────────────────────────────────────────────────────────────

class OpportunitiesStartEndTests(_DashboardTestBase):
    def test_opportunities_with_start(self):
        self._seed_data()
        start = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        resp = self.client.get(f"/opportunities?start={start}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 4)

    def test_opportunities_with_start_and_end(self):
        self._seed_data()
        start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        resp = self.client.get(f"/opportunities?start={start}&end={end}")
        self.assertEqual(len(resp.json()), 4)

    def test_opportunities_start_excludes_old(self):
        self._seed_data()
        start = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        resp = self.client.get(f"/opportunities?start={start}")
        self.assertEqual(len(resp.json()), 0)

    def test_opportunities_start_takes_precedence_over_window(self):
        """When start is provided, window param should be ignored."""
        self._seed_data()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        # Window would return data, but start in future should override.
        resp = self.client.get(f"/opportunities?window=24h&start={future}")
        self.assertEqual(len(resp.json()), 0)

    def test_opportunities_with_chain_and_start(self):
        self._seed_data()
        start = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        resp = self.client.get(f"/opportunities?start={start}&chain=base")
        data = resp.json()
        self.assertTrue(all(o["chain"] == "base" for o in data))

    def test_opportunities_with_execution_data(self):
        """Ensure start/end still returns joined execution data."""
        opp_id = self._seed_full_lifecycle()
        start = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        resp = self.client.get(f"/opportunities?start={start}")
        data = resp.json()
        opp = next((o for o in data if o["opportunity_id"] == opp_id), None)
        self.assertIsNotNone(opp)
        self.assertEqual(opp["tx_hash"], "0xabcdef1234")
        self.assertEqual(opp["exec_included"], 1)
        self.assertIsNotNone(opp["actual_net_profit"])


# ──────────────────────────────────────────────────────────────────────
# Chain Summary Tests
# ──────────────────────────────────────────────────────────────────────

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
        chains = [c["chain"] for c in data]
        self.assertIn("ethereum", chains)
        self.assertIn("arbitrum", chains)

    def test_chains_sorted_by_total(self):
        self._seed_data()
        resp = self.client.get("/dashboard/chains")
        data = resp.json()
        totals = [c["total"] for c in data]
        self.assertEqual(totals, sorted(totals, reverse=True))

    def test_chains_custom_window(self):
        self._seed_data()
        resp = self.client.get("/dashboard/chains?window=1h")
        self.assertEqual(resp.status_code, 200)

    def test_chains_funnel_data(self):
        self._seed_data()
        resp = self.client.get("/dashboard/chains")
        data = resp.json()
        eth = next(c for c in data if c["chain"] == "ethereum")
        self.assertIn("funnel", eth)
        self.assertIn("approved", eth["funnel"])


# ──────────────────────────────────────────────────────────────────────
# Distinct Chains Tests
# ──────────────────────────────────────────────────────────────────────

class DistinctChainsTests(_DashboardTestBase):
    def test_no_chains_when_empty(self):
        resp = self.client.get("/dashboard/distinct-chains")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_returns_distinct_chains(self):
        self._seed_data()
        resp = self.client.get("/dashboard/distinct-chains")
        data = resp.json()
        self.assertIn("ethereum", data)
        self.assertIn("arbitrum", data)
        self.assertIn("base", data)
        # Should be sorted.
        self.assertEqual(data, sorted(data))


# ──────────────────────────────────────────────────────────────────────
# Hourly Bars Tests
# ──────────────────────────────────────────────────────────────────────

class HourlyBarsTests(_DashboardTestBase):
    def test_hourly_bars_empty(self):
        resp = self.client.get("/dashboard/hourly-bars")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_hourly_bars_with_data(self):
        self._seed_data()
        resp = self.client.get("/dashboard/hourly-bars")
        data = resp.json()
        self.assertGreater(len(data), 0)
        for row in data:
            self.assertIn("chain", row)
            self.assertIn("status", row)
            self.assertIn("hour", row)
            self.assertIn("cnt", row)


# ──────────────────────────────────────────────────────────────────────
# Profit Aggregation Tests
# ──────────────────────────────────────────────────────────────────────

class ProfitAggregationTests(_DashboardTestBase):
    def _seed_with_pricing(self):
        for i, (chain, profit) in enumerate([
            ("optimism", D("0.08")),
            ("optimism", D("0.05")),
            ("arbitrum", D("0.12")),
            ("ethereum", D("-0.002")),
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

    def test_profit_totals_correct(self):
        self._seed_with_pricing()
        resp = self.client.get("/dashboard/window/24h")
        p = resp.json()["profit"]
        self.assertAlmostEqual(p["total_expected_profit"], 0.25, places=4)
        self.assertEqual(p["priced_count"], 3)

    def test_profit_max_correct(self):
        self._seed_with_pricing()
        resp = self.client.get("/dashboard/window/24h")
        p = resp.json()["profit"]
        self.assertAlmostEqual(p["max_expected_profit"], 0.12, places=4)

    def test_profit_avg_correct(self):
        self._seed_with_pricing()
        resp = self.client.get("/dashboard/window/24h")
        p = resp.json()["profit"]
        self.assertAlmostEqual(p["avg_expected_profit"], 0.0833, places=3)

    def test_profit_filtered_by_chain(self):
        self._seed_with_pricing()
        resp = self.client.get("/dashboard/window/24h?chain=optimism")
        p = resp.json()["profit"]
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
        self.assertIn("Execution PnL", resp.text)
        self.assertIn("Expected Profit", resp.text)
        self.assertIn("Avg Profit", resp.text)
        self.assertIn("Best Single Opp", resp.text)

    def test_profit_in_custom_range(self):
        self._seed_with_pricing()
        start = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        resp = self.client.get(f"/dashboard/range?start={start}")
        data = resp.json()
        self.assertIn("profit", data)
        self.assertAlmostEqual(data["profit"]["total_expected_profit"], 0.25, places=4)


# ──────────────────────────────────────────────────────────────────────
# Execution Stats in Windowed Data
# ──────────────────────────────────────────────────────────────────────

class WindowedExecutionTests(_DashboardTestBase):
    def test_window_includes_trade_data(self):
        self._seed_full_lifecycle()
        resp = self.client.get("/dashboard/window/24h")
        data = resp.json()
        t = data["trades"]
        self.assertEqual(t["total_trades"], 1)
        self.assertEqual(t["successful"], 1)
        self.assertAlmostEqual(t["total_profit"], 0.006, places=4)

    def test_custom_range_includes_trade_data(self):
        self._seed_full_lifecycle()
        start = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        resp = self.client.get(f"/dashboard/range?start={start}")
        data = resp.json()
        self.assertEqual(data["trades"]["total_trades"], 1)


# ──────────────────────────────────────────────────────────────────────
# UTC Storage Verification
# ──────────────────────────────────────────────────────────────────────

class UTCStorageTests(_DashboardTestBase):
    def test_timestamps_are_utc_iso(self):
        """Verify that all stored timestamps are in UTC ISO format."""
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("10"),
        )
        opp = self.repo.get_opportunity(opp_id)
        # Should be ISO format with timezone info or parseable as UTC.
        detected = opp["detected_at"]
        self.assertIn("T", detected)
        # Parse it — should not raise.
        dt = datetime.fromisoformat(detected)
        # Should be recent (within last minute).
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        self.assertLess(abs((now - dt).total_seconds()), 60)


# ──────────────────────────────────────────────────────────────────────
# Spread BPS Display (bps → percent conversion)
# ──────────────────────────────────────────────────────────────────────

class SpreadDisplayTests(_DashboardTestBase):
    def test_dashboard_divides_bps_by_100(self):
        """spread_bps must be divided by 100 for display as percent."""
        resp = self.client.get("/dashboard")
        # The JS expression: (Number(o.spread_bps)/100).toFixed(2)
        self.assertIn("spread_bps)/100)", resp.text)

    def test_detail_page_divides_bps_by_100(self):
        resp = self.client.get("/opportunity/opp_test123")
        self.assertIn("spread_bps / 100", resp.text)

    def test_spread_in_api_is_raw_bps(self):
        """API returns raw bps; conversion is client-side only."""
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="arbitrum",
            buy_dex="A", sell_dex="B", spread_bps=D("42"),
        )
        resp = self.client.get(f"/opportunities/{opp_id}")
        self.assertEqual(resp.json()["spread_bps"], "42")


# ──────────────────────────────────────────────────────────────────────
# Repository: Execution Stats
# ──────────────────────────────────────────────────────────────────────

class ExecutionStatsTests(_DashboardTestBase):
    def test_execution_stats_empty(self):
        stats = self.repo.get_execution_stats()
        self.assertEqual(stats["total_trades"], 0)
        self.assertEqual(stats["successful"], 0)
        self.assertEqual(stats["reverted"], 0)
        self.assertEqual(stats["not_included"], 0)
        self.assertEqual(stats["total_profit"], 0)

    def test_execution_stats_with_data(self):
        opp_id = self._seed_full_lifecycle()
        stats = self.repo.get_execution_stats()
        self.assertEqual(stats["total_trades"], 1)
        self.assertEqual(stats["successful"], 1)
        self.assertEqual(stats["reverted"], 0)
        self.assertAlmostEqual(stats["total_profit"], 0.006, places=4)

    def test_execution_stats_since_filter(self):
        self._seed_full_lifecycle()
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        stats = self.repo.get_execution_stats(since)
        self.assertEqual(stats["total_trades"], 1)

    def test_execution_stats_since_future_returns_zero(self):
        self._seed_full_lifecycle()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        stats = self.repo.get_execution_stats(future)
        self.assertEqual(stats["total_trades"], 0)

    def test_execution_stats_reverted(self):
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="arbitrum",
            buy_dex="A", sell_dex="B", spread_bps=D("20"),
        )
        exec_id = self.repo.save_execution_attempt(opp_id, tx_hash="0xfail")
        self.repo.save_trade_result(
            exec_id, included=True, reverted=True, gas_used=150000,
            gas_cost_base=D("0.001"), actual_net_profit=D("-0.001"),
        )
        stats = self.repo.get_execution_stats()
        self.assertEqual(stats["successful"], 0)
        self.assertEqual(stats["reverted"], 1)

    def test_execution_stats_not_included(self):
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="base",
            buy_dex="A", sell_dex="B", spread_bps=D("15"),
        )
        exec_id = self.repo.save_execution_attempt(opp_id, tx_hash="0xmiss")
        self.repo.save_trade_result(exec_id, included=False)
        stats = self.repo.get_execution_stats()
        self.assertEqual(stats["not_included"], 1)
        self.assertEqual(stats["successful"], 0)


# ──────────────────────────────────────────────────────────────────────
# Repository: Chain Opportunity Stats
# ──────────────────────────────────────────────────────────────────────

class ChainOpportunityStatsTests(_DashboardTestBase):
    def test_empty(self):
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        stats = self.repo.get_chain_opportunity_stats(since)
        self.assertEqual(stats, {})

    def test_per_chain_counts(self):
        self._seed_data()  # 2 ethereum, 1 arbitrum, 1 base
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        stats = self.repo.get_chain_opportunity_stats(since)
        self.assertIn("ethereum", stats)
        self.assertIn("arbitrum", stats)
        self.assertIn("base", stats)
        self.assertEqual(stats["ethereum"]["total"], 2)
        self.assertEqual(stats["arbitrum"]["total"], 1)

    def test_per_chain_status_breakdown(self):
        self._seed_data()
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        stats = self.repo.get_chain_opportunity_stats(since)
        eth = stats["ethereum"]
        self.assertIn("approved", eth)
        self.assertIn("rejected", eth)


# ──────────────────────────────────────────────────────────────────────
# PnL Summary: COALESCE returns 0 not None
# ──────────────────────────────────────────────────────────────────────

class PnlCoalesceTests(_DashboardTestBase):
    def test_pnl_returns_zeros_not_none_when_empty(self):
        resp = self.client.get("/pnl")
        data = resp.json()
        self.assertEqual(data["successful"], 0)
        self.assertEqual(data["reverted"], 0)
        self.assertEqual(data["not_included"], 0)
        self.assertEqual(data["total_profit"], 0)

    def test_windowed_trades_return_zeros_not_none(self):
        resp = self.client.get("/dashboard/window/24h")
        t = resp.json()["trades"]
        self.assertEqual(t["successful"], 0)
        self.assertEqual(t["reverted"], 0)


# ──────────────────────────────────────────────────────────────────────
# Ops Dashboard Tests
# ──────────────────────────────────────────────────────────────────────

class OpsDashboardHTMLTests(_DashboardTestBase):
    def test_ops_returns_html(self):
        resp = self.client.get("/ops")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])

    def test_ops_contains_dex_health_table(self):
        resp = self.client.get("/ops")
        self.assertIn('id="dex-table"', resp.text)

    def test_ops_contains_diagnostics_fetch(self):
        resp = self.client.get("/ops")
        self.assertIn("fetchJSON('/diagnostics/quotes')", resp.text)

    def test_ops_contains_risk_policy_section(self):
        resp = self.client.get("/ops")
        self.assertIn("Risk Policy", resp.text)
        self.assertIn("Min Net Profit", resp.text)
        self.assertIn("Min Spread", resp.text)
        self.assertIn("Max Slippage", resp.text)

    def test_ops_contains_scan_metrics(self):
        resp = self.client.get("/ops")
        self.assertIn("Scan Metrics", resp.text)
        self.assertIn("Uptime", resp.text)
        self.assertIn("Opportunities / min", resp.text)

    def test_ops_contains_rpc_section(self):
        resp = self.client.get("/ops")
        self.assertIn("RPC Endpoints", resp.text)
        self.assertIn('id="rpc-grid"', resp.text)

    def test_ops_contains_back_link(self):
        resp = self.client.get("/ops")
        self.assertIn('href="dashboard"', resp.text)

    def test_ops_no_cache_headers(self):
        resp = self.client.get("/ops")
        self.assertIn("no-cache", resp.headers.get("cache-control", ""))


class OpsAPIEndpointTests(_DashboardTestBase):
    def test_operations_default(self):
        resp = self.client.get("/operations")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("db_backend", data)
        self.assertIn("discovered_pairs_count", data)
        self.assertIn("enabled_pools_total", data)
        self.assertIn("live_stack_ready", data)
        self.assertIn("launch_ready", data)

    def test_operations_with_checkpoints(self):
        self.repo.set_checkpoint("live_stack_ready", "1")
        self.repo.set_checkpoint("live_rollout_target", "arbitrum")
        self.repo.set_checkpoint("live_executable_chains", "arbitrum,base")
        self.repo.set_checkpoint("live_executable_dexes", "Uniswap-Arb,Sushi-Arb")
        self.repo.set_checkpoint("launch_chain", "arbitrum")
        self.repo.set_checkpoint("launch_ready", "1")
        self.repo.set_checkpoint("launch_blockers", "[]")
        self.repo.set_checkpoint("executor_key_configured", "1")
        self.repo.set_checkpoint("executor_contract_configured", "1")
        self.repo.set_checkpoint("rpc_configured", "1")

        resp = self.client.get("/operations")
        data = resp.json()
        self.assertTrue(data["live_stack_ready"])
        self.assertEqual(data["live_rollout_target"], "arbitrum")
        self.assertEqual(data["live_executable_chains"], ["arbitrum", "base"])
        self.assertTrue(data["launch_ready"])

    def test_diagnostics_empty(self):
        resp = self.client.get("/diagnostics/quotes")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["dexes"], {})

    def test_diagnostics_with_data(self):
        from observability.quote_diagnostics import QuoteDiagnostics, QuoteOutcome
        import api.app as app_mod
        diag = QuoteDiagnostics()
        diag.record("Uniswap", "ethereum", "WETH/USDC", QuoteOutcome.SUCCESS, latency_ms=50.0)
        diag.record("Uniswap", "ethereum", "WETH/USDC", QuoteOutcome.ERROR, error_msg="timeout")
        old = app_mod._diagnostics
        app_mod._diagnostics = diag
        try:
            resp = self.client.get("/diagnostics/quotes")
            data = resp.json()
            self.assertIn("Uniswap", data["dexes"])
            self.assertEqual(data["dexes"]["Uniswap"][0]["total_quotes"], 2)
            self.assertEqual(data["dexes"]["Uniswap"][0]["success_count"], 1)
        finally:
            app_mod._diagnostics = old

    def test_metrics_endpoint(self):
        resp = self.client.get("/metrics")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("opportunities_detected", data)
        self.assertIn("avg_latency_ms", data)
        self.assertIn("inclusion_rate_pct", data)

    def test_risk_policy_endpoint(self):
        resp = self.client.get("/risk/policy")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("min_net_profit_default", data)
        self.assertIn("min_spread_pct_default", data)
        self.assertIn("max_slippage_bps", data)
        self.assertIn("execution_enabled", data)

    def test_ops_dashboard_uses_current_risk_policy_field_names(self):
        resp = self.client.get("/ops")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("min_net_profit_default", resp.text)
        self.assertIn("min_spread_pct_default", resp.text)


# ──────────────────────────────────────────────────────────────────────
# Scanner & Execution Control Tests
# ──────────────────────────────────────────────────────────────────────

class ScannerControlTests(_DashboardTestBase):
    def test_scanner_status_not_configured(self):
        resp = self.client.get("/scanner")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "not_configured")
        self.assertFalse(data["running"])

    def test_scanner_start_fails_without_ref(self):
        resp = self.client.post("/scanner/start")
        self.assertEqual(resp.status_code, 400)

    def test_scanner_stop_fails_without_ref(self):
        resp = self.client.post("/scanner/stop")
        self.assertEqual(resp.status_code, 400)


class ExecutionControlTests(_DashboardTestBase):
    def test_execution_disabled_by_default(self):
        resp = self.client.get("/execution")
        self.assertFalse(resp.json()["execution_enabled"])

    def test_enable_execution_blocked_without_launch_ready(self):
        resp = self.client.post("/execution", json={"enabled": True})
        self.assertEqual(resp.status_code, 409)

    def test_disable_execution(self):
        self.policy.execution_enabled = True
        resp = self.client.post("/execution", json={"enabled": False})
        self.assertFalse(resp.json()["execution_enabled"])


class PauseTests(_DashboardTestBase):
    def test_pause_default_false(self):
        resp = self.client.get("/pause")
        self.assertFalse(resp.json()["paused"])

    def test_pause_toggle(self):
        resp = self.client.post("/pause", json={"paused": True})
        self.assertTrue(resp.json()["paused"])
        resp = self.client.post("/pause", json={"paused": False})
        self.assertFalse(resp.json()["paused"])


# ──────────────────────────────────────────────────────────────────────
# Replay Endpoint Tests
# ──────────────────────────────────────────────────────────────────────

class ReplayTests(_DashboardTestBase):
    def test_replay_existing(self):
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("42"),
        )
        self.repo.save_pricing(opp_id, D("2200"), D("2210"), D("2"), D("1"), D("0.002"), D("0.005"))
        self.repo.save_risk_decision(opp_id, approved=True, reason_code="passed")

        resp = self.client.post(f"/opportunities/{opp_id}/replay")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("replay_risk_verdict", data)
        self.assertIn("current_policy", data)

    def test_replay_nonexistent(self):
        resp = self.client.post("/opportunities/opp_fake/replay")
        self.assertEqual(resp.status_code, 404)


# ──────────────────────────────────────────────────────────────────────
# Full Opportunity Detail API Tests
# ──────────────────────────────────────────────────────────────────────

class OpportunityFullTests(_DashboardTestBase):
    def test_full_lifecycle_data(self):
        opp_id = self._seed_full_lifecycle()
        resp = self.client.get(f"/opportunities/{opp_id}/full")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNotNone(data["opportunity"])
        self.assertIsNotNone(data["pricing"])
        self.assertIsNotNone(data["risk_decision"])
        self.assertIsNotNone(data["simulation"])
        self.assertIsNotNone(data["execution_attempt"])
        self.assertIsNotNone(data["trade_result"])
        self.assertEqual(data["trade_result"]["gas_used"], 250000)
        self.assertEqual(data["execution_attempt"]["tx_hash"], "0xabcdef1234")

    def test_partial_lifecycle(self):
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("10"),
        )
        resp = self.client.get(f"/opportunities/{opp_id}/full")
        data = resp.json()
        self.assertIsNotNone(data["opportunity"])
        self.assertIsNone(data["pricing"])
        self.assertIsNone(data["execution_attempt"])
        self.assertIsNone(data["trade_result"])

    def test_nonexistent_returns_404(self):
        resp = self.client.get("/opportunities/opp_nope/full")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
