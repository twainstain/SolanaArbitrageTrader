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

    def test_enable_execution_requires_launch_ready(self):
        resp = self.client.post("/execution", json={"enabled": True})
        self.assertEqual(resp.status_code, 409)
        data = resp.json()["detail"]
        self.assertEqual(data["message"], "launch_not_ready")
        self.assertFalse(data["launch_ready"])

    def test_enable_execution_when_launch_ready(self):
        self.repo.set_checkpoint("launch_chain", "arbitrum")
        self.repo.set_checkpoint("launch_ready", "1")
        self.repo.set_checkpoint("launch_blockers", "[]")
        self.repo.set_checkpoint("executor_key_configured", "1")
        self.repo.set_checkpoint("executor_contract_configured", "1")
        self.repo.set_checkpoint("rpc_configured", "1")

        resp = self.client.post("/execution", json={"enabled": True})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["execution_enabled"])
        resp2 = self.client.get("/execution")
        self.assertTrue(resp2.json()["execution_enabled"])

    def test_disable_execution(self):
        self.policy.execution_enabled = True
        resp = self.client.post("/execution", json={"enabled": False})
        self.assertFalse(resp.json()["execution_enabled"])

    def test_launch_readiness_endpoint(self):
        self.repo.set_checkpoint("launch_chain", "arbitrum")
        self.repo.set_checkpoint("launch_ready", "0")
        self.repo.set_checkpoint("launch_blockers", '["missing_rpc_arbitrum"]')
        self.repo.set_checkpoint("executor_key_configured", "1")
        self.repo.set_checkpoint("executor_contract_configured", "1")
        self.repo.set_checkpoint("rpc_configured", "0")

        resp = self.client.get("/launch-readiness")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["launch_chain"], "arbitrum")
        self.assertFalse(data["launch_ready"])
        self.assertEqual(data["launch_blockers"], ["missing_rpc_arbitrum"])


class PerChainExecutionTests(_APITestBase):
    """Tests for per-chain execution mode."""

    def test_get_execution_returns_chain_status(self):
        resp = self.client.get("/execution")
        data = resp.json()
        self.assertIn("chains", data)
        self.assertIn("arbitrum", data["chains"])
        self.assertIn("optimism", data["chains"])
        self.assertIn("mode", data["chains"]["arbitrum"])

    def test_default_chain_mode_is_simulated(self):
        resp = self.client.get("/execution")
        data = resp.json()
        # Global execution_enabled is False, so all chains default to simulated
        self.assertEqual(data["chains"]["arbitrum"]["mode"], "simulated")
        self.assertEqual(data["chains"]["optimism"]["mode"], "simulated")

    def test_set_chain_mode_live(self):
        resp = self.client.post("/execution", json={"chain": "arbitrum", "mode": "live"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["mode"], "live")
        # Verify via GET
        resp2 = self.client.get("/execution")
        self.assertEqual(resp2.json()["chains"]["arbitrum"]["mode"], "live")
        # Other chains unchanged
        self.assertEqual(resp2.json()["chains"]["optimism"]["mode"], "simulated")

    def test_set_chain_mode_disabled(self):
        resp = self.client.post("/execution", json={"chain": "optimism", "mode": "disabled"})
        self.assertEqual(resp.json()["mode"], "disabled")

    def test_set_chain_mode_simulated(self):
        self.policy.set_chain_mode("arbitrum", "live")
        resp = self.client.post("/execution", json={"chain": "arbitrum", "mode": "simulated"})
        self.assertEqual(resp.json()["mode"], "simulated")

    def test_set_chain_via_enabled_flag(self):
        resp = self.client.post("/execution", json={"chain": "arbitrum", "enabled": True})
        self.assertEqual(resp.json()["mode"], "live")
        resp2 = self.client.post("/execution", json={"chain": "arbitrum", "enabled": False})
        self.assertEqual(resp2.json()["mode"], "simulated")

    def test_chain_executable_flags(self):
        resp = self.client.get("/execution")
        chains = resp.json()["chains"]
        # Arbitrum has routers + Aave
        self.assertTrue(chains["arbitrum"]["executable"])
        self.assertTrue(chains["arbitrum"]["has_routers"])
        self.assertTrue(chains["arbitrum"]["has_aave"])

    def test_global_toggle_still_works(self):
        self.repo.set_checkpoint("launch_ready", "1")
        self.repo.set_checkpoint("launch_blockers", "[]")
        self.repo.set_checkpoint("executor_key_configured", "1")
        self.repo.set_checkpoint("executor_contract_configured", "1")
        self.repo.set_checkpoint("rpc_configured", "1")

        resp = self.client.post("/execution", json={"enabled": True})
        self.assertTrue(resp.json()["execution_enabled"])

    def test_chain_mode_returned_in_policy(self):
        self.policy.set_chain_mode("arbitrum", "live")
        resp = self.client.get("/risk/policy")
        data = resp.json()
        self.assertIn("chain_execution_mode", data)
        self.assertEqual(data["chain_execution_mode"]["arbitrum"], "live")


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

    def test_get_opportunity_full_includes_execution_and_trade_result(self):
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain="arbitrum",
            buy_dex="Uniswap-Arbitrum", sell_dex="Sushi-Arbitrum", spread_bps=D("42"),
        )
        exec_id = self.repo.save_execution_attempt(
            opp_id,
            submission_type="flashbots",
            tx_hash="0xabc",
            target_block=12345,
        )
        self.repo.save_trade_result(
            execution_id=exec_id,
            included=True,
            gas_used=210000,
            realized_profit_quote=D("15"),
            gas_cost_base=D("0.0015"),
            profit_currency="USDC",
            actual_net_profit=D("0.005"),
            block_number=12346,
        )

        resp = self.client.get(f"/opportunities/{opp_id}/full")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["execution_attempt"]["tx_hash"], "0xabc")
        self.assertEqual(data["trade_result"]["realized_profit_quote"], "15")
        self.assertEqual(data["trade_result"]["gas_cost_base"], "0.0015")
        self.assertEqual(data["trade_result"]["profit_currency"], "USDC")

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
        self.repo.set_checkpoint("live_stack_ready", "1")
        self.repo.set_checkpoint("live_rollout_target", "arbitrum")
        self.repo.set_checkpoint("live_executable_chains", "arbitrum,base")
        self.repo.set_checkpoint("live_executable_dexes", "Uniswap-Arbitrum,Sushi-Arbitrum")
        self.repo.set_checkpoint("launch_chain", "arbitrum")
        self.repo.set_checkpoint("launch_ready", "0")
        self.repo.set_checkpoint("launch_blockers", '["missing_rpc_arbitrum"]')
        self.repo.set_checkpoint("executor_key_configured", "1")
        self.repo.set_checkpoint("executor_contract_configured", "1")
        self.repo.set_checkpoint("rpc_configured", "0")

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
        self.assertTrue(data["live_stack_ready"])
        self.assertEqual(data["live_rollout_target"], "arbitrum")
        self.assertEqual(data["live_executable_chains"], ["arbitrum", "base"])
        self.assertEqual(data["live_executable_dexes"], ["Uniswap-Arbitrum", "Sushi-Arbitrum"])
        self.assertEqual(data["launch_chain"], "arbitrum")
        self.assertFalse(data["launch_ready"])
        self.assertEqual(data["launch_blockers"], ["missing_rpc_arbitrum"])
        self.assertTrue(data["executor_key_configured"])
        self.assertTrue(data["executor_contract_configured"])
        self.assertFalse(data["rpc_configured"])

    def test_dashboard_html_links_to_ops(self):
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertIn('ops', resp.text)

    def test_ops_dashboard_loads(self):
        resp = self.client.get("/ops")
        self.assertEqual(resp.status_code, 200)
        self.assertIn('id="dex-table"', resp.text)
        self.assertIn("fetchJSON('/diagnostics/quotes')", resp.text)


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


class AnalyticsEndpointTests(_APITestBase):
    """Tests for /pnl/analytics and /analytics dashboard."""

    def _seed_executed_trade(self, chain="arbitrum", pair="WETH/USDC",
                             buy_dex="Uniswap", sell_dex="Sushi",
                             expected_profit="0.005", actual_profit="0.004",
                             gas_cost="0.0001", reverted=False):
        opp_id = self.repo.create_opportunity(
            pair=pair, chain=chain,
            buy_dex=buy_dex, sell_dex=sell_dex, spread_bps=D("25"),
        )
        self.repo.save_pricing(
            opp_id=opp_id, input_amount=D("2300"), estimated_output=D("2310"),
            fee_cost=D("1"), slippage_cost=D("0.5"), gas_estimate=D("0.0002"),
            expected_net_profit=D(expected_profit),
        )
        self.repo.save_risk_decision(
            opp_id=opp_id, approved=True, reason_code="approved",
        )
        self.repo.update_opportunity_status(opp_id, "submitted")
        exec_id = self.repo.save_execution_attempt(
            opp_id=opp_id, submission_type="public",
            tx_hash="0x" + "aa" * 32, target_block=12345,
        )
        self.repo.save_trade_result(
            execution_id=exec_id,
            included=not reverted, reverted=reverted, gas_used=350000,
            realized_profit_quote=D("10") if not reverted else D("0"),
            gas_cost_base=D(gas_cost),
            actual_net_profit=D(actual_profit) if not reverted else D("0"),
            block_number=12345,
        )
        self.repo.update_opportunity_status(opp_id, "included" if not reverted else "reverted")
        return opp_id

    def _seed_rejected(self, chain="arbitrum", reason="spread_too_low"):
        opp_id = self.repo.create_opportunity(
            pair="WETH/USDC", chain=chain,
            buy_dex="Uniswap", sell_dex="Sushi", spread_bps=D("5"),
        )
        self.repo.save_pricing(
            opp_id=opp_id, input_amount=D("2300"), estimated_output=D("2301"),
            fee_cost=D("0.5"), slippage_cost=D("0.2"), gas_estimate=D("0.0001"),
            expected_net_profit=D("0.0001"),
        )
        self.repo.save_risk_decision(
            opp_id=opp_id, approved=False, reason_code=reason,
        )
        self.repo.update_opportunity_status(opp_id, "rejected")
        return opp_id

    def test_analytics_empty(self):
        resp = self.client.get("/pnl/analytics")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["per_pair"], [])
        self.assertEqual(data["per_venue"], [])
        self.assertEqual(data["spread_capture_rate_pct"], 0)

    def test_analytics_with_trades(self):
        self._seed_executed_trade(chain="arbitrum")
        self._seed_executed_trade(chain="optimism", buy_dex="Velodrome", sell_dex="Uniswap")
        self._seed_executed_trade(chain="arbitrum", reverted=True)

        resp = self.client.get("/pnl/analytics")
        data = resp.json()

        # Per-pair: should have entries
        self.assertTrue(len(data["per_pair"]) > 0)
        arb_pair = [p for p in data["per_pair"] if p["chain"] == "arbitrum"]
        self.assertTrue(len(arb_pair) > 0)
        self.assertEqual(arb_pair[0]["trades"], 2)
        self.assertEqual(arb_pair[0]["wins"], 1)
        self.assertEqual(arb_pair[0]["reverts"], 1)

        # Per-venue: should have entries
        self.assertTrue(len(data["per_venue"]) > 0)

        # Expected vs realized: only included trades
        self.assertTrue(len(data["expected_vs_realized"]) >= 2)

        # Hourly PnL: should have at least 1 hour
        self.assertTrue(len(data["hourly_pnl"]) > 0)

    def test_analytics_chain_filter(self):
        self._seed_executed_trade(chain="arbitrum")
        self._seed_executed_trade(chain="optimism", buy_dex="Velodrome", sell_dex="Uniswap")

        resp = self.client.get("/pnl/analytics?chain=optimism")
        data = resp.json()
        self.assertEqual(data["filters"]["chain"], "optimism")
        # Only optimism trades
        for p in data["per_pair"]:
            self.assertEqual(p["chain"], "optimism")

    def test_analytics_window_filter(self):
        self._seed_executed_trade()
        resp = self.client.get("/pnl/analytics?window=24h")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(len(data["per_pair"]) > 0)

    def test_analytics_rejection_analysis(self):
        self._seed_rejected(chain="arbitrum", reason="spread_too_low")
        self._seed_rejected(chain="arbitrum", reason="spread_too_low")
        self._seed_rejected(chain="optimism", reason="liquidity_too_low")

        resp = self.client.get("/pnl/analytics")
        data = resp.json()
        self.assertTrue(len(data["rejection_reasons"]) > 0)
        reasons = {r["reason_code"] for r in data["rejection_reasons"]}
        self.assertIn("spread_too_low", reasons)

    def test_analytics_gas_efficiency(self):
        self._seed_executed_trade(chain="arbitrum", gas_cost="0.0005")
        resp = self.client.get("/pnl/analytics")
        data = resp.json()
        self.assertTrue(len(data["gas_efficiency"]) > 0)
        arb_gas = [g for g in data["gas_efficiency"] if g["chain"] == "arbitrum"]
        self.assertTrue(len(arb_gas) > 0)
        self.assertGreater(arb_gas[0]["avg_gas_used"], 0)

    def test_analytics_dashboard_html(self):
        resp = self.client.get("/analytics")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("PnL Analytics", resp.text)
        self.assertIn("f-chain", resp.text)
        self.assertIn("f-window", resp.text)

    def test_analytics_date_range_filter(self):
        self._seed_executed_trade()
        resp = self.client.get(
            "/pnl/analytics?since=2020-01-01T00:00:00Z&until=2099-12-31T23:59:59Z"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["filters"]["since"], "2020-01-01T00:00:00Z")
        self.assertEqual(data["filters"]["until"], "2099-12-31T23:59:59Z")
        self.assertTrue(len(data["per_pair"]) > 0)

    def test_analytics_date_range_excludes_data(self):
        self._seed_executed_trade()
        resp = self.client.get(
            "/pnl/analytics?since=2099-01-01T00:00:00Z"
        )
        data = resp.json()
        self.assertEqual(data["per_pair"], [])

    def test_analytics_spread_capture_rate(self):
        self._seed_executed_trade(expected_profit="0.010", actual_profit="0.008")
        resp = self.client.get("/pnl/analytics")
        data = resp.json()
        self.assertGreater(data["spread_capture_rate_pct"], 0)
        self.assertLessEqual(data["spread_capture_rate_pct"], 100)

    def test_analytics_expected_vs_realized_has_tx_hash(self):
        self._seed_executed_trade()
        resp = self.client.get("/pnl/analytics")
        data = resp.json()
        for row in data["expected_vs_realized"]:
            self.assertIn("tx_hash", row)
            self.assertTrue(row["tx_hash"].startswith("0x"))


class AnalyticsDashboardHTMLTests(_APITestBase):
    """Tests for /analytics HTML content."""

    def test_analytics_contains_filter_controls(self):
        resp = self.client.get("/analytics")
        html = resp.text
        self.assertIn('id="f-chain"', html)
        self.assertIn('id="f-window"', html)
        self.assertIn('id="f-since"', html)
        self.assertIn('id="f-until"', html)
        self.assertIn('onclick="loadAll()"', html)

    def test_analytics_contains_sections(self):
        resp = self.client.get("/analytics")
        html = resp.text
        for section in ["summary-grid", "hourly-chart", "pair-table",
                        "venue-table", "evr-table", "gas-table", "reject-table"]:
            self.assertIn(f'id="{section}"', html, f"Missing section: {section}")

    def test_analytics_contains_js_render_functions(self):
        resp = self.client.get("/analytics")
        html = resp.text
        for func in ["renderSummary", "renderHourly", "renderPairs",
                      "renderVenues", "renderEVR", "renderGas", "renderRejects"]:
            self.assertIn(f"function {func}", html, f"Missing JS function: {func}")

    def test_analytics_contains_back_link(self):
        resp = self.client.get("/analytics")
        self.assertIn("Back to Dashboard", resp.text)

    def test_analytics_no_cache(self):
        resp = self.client.get("/analytics")
        self.assertIn("no-cache", resp.headers.get("cache-control", ""))


class DashboardNewFeaturesHTMLTests(_APITestBase):
    """Tests for dashboard features added in this session."""

    def test_dashboard_has_sortable_columns(self):
        resp = self.client.get("/dashboard")
        html = resp.text
        self.assertIn("sortOpps(", html)
        self.assertIn("sort-arrow", html)

    def test_dashboard_has_exec_detail_toggle(self):
        resp = self.client.get("/dashboard")
        html = resp.text
        self.assertIn("toggleExecDetail", html)
        self.assertIn("exec-detail", html)

    def test_dashboard_has_wallet_balance_loader(self):
        resp = self.client.get("/dashboard")
        html = resp.text
        self.assertIn("loadWalletBalance", html)
        self.assertIn("wallet-grid", html)

    def test_dashboard_has_analytics_link(self):
        resp = self.client.get("/dashboard")
        self.assertIn("analytics", resp.text)
        self.assertIn("PnL Analytics", resp.text)

    def test_dashboard_has_chain_exec_table(self):
        resp = self.client.get("/dashboard")
        html = resp.text
        self.assertIn("chain-exec-table", html)
        self.assertIn("chain-exec-body", html)
        self.assertIn("loadChainExecStatus", html)
        self.assertIn("setChainMode", html)

    def test_dashboard_has_realized_pnl_column(self):
        resp = self.client.get("/dashboard")
        self.assertIn("Realized PnL", resp.text)

    def test_dashboard_sorts_by_profit_default(self):
        resp = self.client.get("/dashboard")
        self.assertIn("oppSortField = 'profit'", resp.text)


class WalletBalanceEndpointTests(_APITestBase):
    """Tests for /wallet/balance endpoint."""

    def test_wallet_balance_no_key(self):
        """Without EXECUTOR_PRIVATE_KEY, returns error."""
        import os
        old = os.environ.pop("EXECUTOR_PRIVATE_KEY", None)
        try:
            resp = self.client.get("/wallet/balance")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("error", data)
        finally:
            if old:
                os.environ["EXECUTOR_PRIVATE_KEY"] = old

    def test_wallet_balance_returns_address(self):
        """With a valid key, returns the derived address."""
        import os
        os.environ["EXECUTOR_PRIVATE_KEY"] = "0x" + "ab" * 32
        try:
            resp = self.client.get("/wallet/balance")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["address"].startswith("0x"))
            self.assertIn("balances", data)
        finally:
            os.environ.pop("EXECUTOR_PRIVATE_KEY", None)


if __name__ == "__main__":
    unittest.main()
