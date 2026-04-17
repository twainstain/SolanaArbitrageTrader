"""HTML dashboard rendering tests.

Covers:
  - resolve_filters() parses window + since/until + pair + status
  - filter_bar() renders a <form> with the right selected options
  - each dashboard renders without error on an empty repo
  - each dashboard renders with populated data
  - pair/status filter actually narrows the returned rows
  - ops dashboard surfaces scanner state + per-pair toggles
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from control_state import get_control
from dashboards._shared import filter_bar, resolve_filters
from dashboards import main_dashboard, ops_dashboard, analytics_dashboard
from persistence.db import init_db
from persistence.repository import Repository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def empty_repo(tmp_path):
    db = init_db(str(tmp_path / "empty.db"))
    return Repository(db)


@pytest.fixture
def populated_repo(tmp_path):
    """Repo with a handful of opportunities covering multiple pairs/statuses."""
    db = init_db(str(tmp_path / "pop.db"))
    repo = Repository(db)
    for i, (pair, buy, sell, status) in enumerate([
        ("SOL/USDC",  "Jupiter-Best",   "Raydium-SOL/USDC", "confirmed"),
        ("SOL/USDC",  "Orca-SOL/USDC",  "Jupiter-Best",      "rejected"),
        ("USDC/USDT", "Orca-USDC/USDT", "Jupiter-Best",      "dry_run"),
        ("SOL/USDC",  "Jupiter-Direct", "Raydium-SOL/USDC", "rejected"),
    ]):
        opp_id = repo.create_opportunity(pair=pair, buy_venue=buy, sell_venue=sell,
                                         spread_bps=Decimal("0.0" + str(i + 1)))
        repo.save_pricing(
            opp_id=opp_id,
            input_amount=Decimal("100"), estimated_output=Decimal("101"),
            fee_cost=Decimal("0"), slippage_cost=Decimal("0.1"),
            fee_estimate_base=Decimal("0.00001"),
            expected_net_profit=Decimal("0.001"),
        )
        repo.save_risk_decision(opp_id=opp_id, approved=(status != "rejected"),
                                reason_code="approved" if status != "rejected" else "below_min_profit")
        repo.update_opportunity_status(opp_id, status)
    # One scan_history row per pair
    repo.save_scan_history([
        {"pair": "SOL/USDC",  "buy_venue": "Jupiter-Best", "sell_venue": "Raydium-SOL/USDC",
         "buy_price": "90", "sell_price": "90.05", "spread_bps": "0.05",
         "gross_profit": "1", "net_profit": "0.5", "filter_reason": "passed", "passed": True},
        {"pair": "USDC/USDT", "buy_venue": "Orca-USDC/USDT", "sell_venue": "Jupiter-Best",
         "buy_price": "0.999", "sell_price": "1.000", "spread_bps": "0.01",
         "gross_profit": "0.01", "net_profit": "-0.001",
         "filter_reason": "unprofitable", "passed": False},
    ])
    return repo


# ---------------------------------------------------------------------------
# resolve_filters
# ---------------------------------------------------------------------------

class TestResolveFilters:
    def test_empty_input_has_empty_defaults(self):
        f = resolve_filters(None)
        assert f == {"window": "", "since": "", "until": "", "pair": "", "status": ""}

    def test_window_preset_sets_since(self):
        f = resolve_filters({"window": "1h"})
        assert f["window"] == "1h"
        parsed = datetime.fromisoformat(f["since"])
        # Should be ~1h ago (allow a few seconds of slop for test wall-clock).
        now = datetime.now(timezone.utc)
        assert timedelta(minutes=58) < (now - parsed) < timedelta(minutes=62)

    def test_explicit_since_wins_over_window(self):
        f = resolve_filters({"window": "24h", "since": "2026-01-01"})
        assert f["since"].startswith("2026-01-01")

    def test_unknown_window_is_ignored(self):
        f = resolve_filters({"window": "bogus"})
        assert f["since"] == ""

    def test_date_only_since_is_coerced(self):
        f = resolve_filters({"since": "2026-04-01"})
        assert f["since"].startswith("2026-04-01")
        # Should have timezone info
        assert "+" in f["since"] or f["since"].endswith("+00:00")

    def test_malformed_since_is_empty(self):
        f = resolve_filters({"since": "not-a-date"})
        assert f["since"] == ""

    def test_pair_and_status_pass_through(self):
        f = resolve_filters({"pair": "SOL/USDC", "status": "rejected"})
        assert f["pair"] == "SOL/USDC"
        assert f["status"] == "rejected"


# ---------------------------------------------------------------------------
# filter_bar
# ---------------------------------------------------------------------------

class TestFilterBar:
    def test_renders_form_with_expected_inputs(self):
        f = resolve_filters({})
        html = filter_bar(f, "analytics", pair_options=["SOL/USDC", "USDC/USDT"])
        assert 'action="/analytics"' in html
        assert 'name="window"' in html
        assert 'name="since"' in html
        assert 'name="until"' in html
        assert 'name="pair"' in html
        assert "SOL/USDC" in html
        assert "USDC/USDT" in html

    def test_selected_window_is_marked(self):
        f = resolve_filters({"window": "24h"})
        html = filter_bar(f, "dashboard", pair_options=[])
        assert '<option value="24h" selected>24h</option>' in html

    def test_selected_pair_is_marked(self):
        f = resolve_filters({"pair": "SOL/USDC"})
        html = filter_bar(f, "dashboard", pair_options=["SOL/USDC", "USDC/USDT"])
        assert '<option value="SOL/USDC" selected>SOL/USDC</option>' in html

    def test_status_hidden_when_disabled(self):
        f = resolve_filters({})
        html = filter_bar(f, "analytics", pair_options=[], show_status=False)
        assert 'name="status"' not in html

    def test_since_is_date_input_value(self):
        html = filter_bar(resolve_filters({"since": "2026-04-01"}),
                          "dashboard", pair_options=[])
        assert 'value="2026-04-01"' in html


# ---------------------------------------------------------------------------
# Dashboard renders — smoke tests (don't error) + content assertions
# ---------------------------------------------------------------------------

class TestMainDashboard:
    def test_renders_empty(self, empty_repo):
        html = main_dashboard.render(empty_repo)
        assert "<h1>Dashboard</h1>" in html
        assert "System Status" in html
        assert "Recent Opportunities" in html
        # Filter bar present
        assert 'action="/dashboard"' in html

    def test_renders_populated(self, populated_repo):
        html = main_dashboard.render(populated_repo)
        assert "SOL/USDC" in html
        assert "USDC/USDT" in html
        # Per-pair 24h table shows counts
        assert "<h2>Per Pair (24h)</h2>" in html

    def test_pair_filter_narrows_opps(self, populated_repo):
        html_all = main_dashboard.render(populated_repo)
        html_sol = main_dashboard.render(populated_repo, filters={"pair": "SOL/USDC"})
        # Both contain SOL/USDC; only the unfiltered one contains USDC/USDT in recent opps.
        assert "SOL/USDC" in html_sol
        # USDC/USDT might still appear in the pair dropdown — check the
        # recent-opps table only (within the last <table>).
        last_table_sol = html_sol.rsplit("<table>", 1)[-1]
        last_table_all = html_all.rsplit("<table>", 1)[-1]
        assert "USDC/USDT" not in last_table_sol
        assert "USDC/USDT" in last_table_all


class TestOpsDashboard:
    def test_renders_empty(self, empty_repo):
        # Reset control to known state so other tests don't leak in.
        c = get_control()
        c.paused = False
        c.disabled_pairs.clear()
        c.disabled_venues.clear()
        html = ops_dashboard.render(empty_repo)
        assert "Infrastructure" in html
        assert "Risk Policy" in html
        assert "Launch Gates" in html
        assert "Pair Toggles" in html
        assert "Venue Toggles" in html

    def test_surfaces_paused_state(self, populated_repo):
        c = get_control()
        c.paused = True
        try:
            html = ops_dashboard.render(populated_repo)
            assert "PAUSED" in html
            assert "Resume scanner" in html
        finally:
            c.paused = False

    def test_pair_toggle_button_shows_current_state(self, populated_repo):
        c = get_control()
        c.disabled_pairs.add("SOL/USDC")
        try:
            html = ops_dashboard.render(populated_repo)
            # Should show SOL/USDC as disabled with an Enable button pointing to /enable
            assert "/pairs/SOL/USDC/enable" in html
            # And USDC/USDT should still be enabled with a Disable button
            assert "/pairs/USDC/USDT/disable" in html
        finally:
            c.disabled_pairs.clear()


class TestAnalyticsDashboard:
    def test_renders_empty(self, empty_repo):
        html = analytics_dashboard.render(empty_repo)
        assert "Profit by Pair" in html
        assert "Expected vs Realized" in html
        assert "Scan History — Filter Breakdown" in html
        assert "Near Misses" in html

    def test_renders_populated(self, populated_repo):
        html = analytics_dashboard.render(populated_repo)
        # Filter breakdown should list the two filter_reason rows we inserted.
        assert "passed" in html
        assert "unprofitable" in html
