"""FastAPI endpoint tests — auth, routes, scanner controls, kill switch."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app, set_metrics_ref
from control_state import get_control
from observability.metrics import MetricsCollector
from persistence.db import init_db
from persistence.repository import Repository
from risk.policy import RiskPolicy

AUTH = ("admin", "adminTest")   # matches the default fallbacks


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Isolate the DB to tmp_path so tests don't collide with real data.
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.delenv("DASHBOARD_USER", raising=False)
    monkeypatch.delenv("DASHBOARD_PASS", raising=False)
    db = init_db(str(tmp_path / "api.db"))
    repo = Repository(db)
    set_metrics_ref(MetricsCollector())
    app = create_app(repo=repo, risk_policy=RiskPolicy())
    yield TestClient(app)
    # Reset control state between tests
    c = get_control()
    c.paused = False
    c.disabled_pairs.clear()
    c.disabled_venues.clear()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuth:
    def test_rejects_without_credentials(self, client):
        r = client.get("/dashboard")
        assert r.status_code == 401

    def test_rejects_with_wrong_credentials(self, client):
        r = client.get("/dashboard", auth=("admin", "wrong"))
        assert r.status_code == 401

    def test_health_is_public(self, client):
        # /health does not require auth.
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# HTML routes
# ---------------------------------------------------------------------------

class TestHtmlRoutes:
    @pytest.mark.parametrize("path", ["/dashboard", "/ops", "/analytics"])
    def test_each_page_returns_html(self, client, path):
        r = client.get(path, auth=AUTH)
        assert r.status_code == 200
        assert "<!doctype html>" in r.text.lower()

    def test_root_redirects_to_dashboard_content(self, client):
        r = client.get("/", auth=AUTH)
        assert r.status_code == 200
        assert "<h1>Dashboard</h1>" in r.text

    def test_overview_is_gone(self, client):
        # We removed /overview deliberately.  It should 404.
        r = client.get("/overview", auth=AUTH)
        assert r.status_code == 404

    def test_query_params_render(self, client):
        r = client.get("/analytics?window=24h&pair=SOL/USDC", auth=AUTH)
        assert r.status_code == 200
        # filter bar should reflect the selection
        assert '<option value="24h" selected>24h</option>' in r.text


# ---------------------------------------------------------------------------
# JSON routes
# ---------------------------------------------------------------------------

class TestJsonRoutes:
    def test_opportunities_empty(self, client):
        r = client.get("/opportunities", auth=AUTH)
        assert r.status_code == 200
        assert r.json() == []

    def test_pnl_empty(self, client):
        r = client.get("/pnl", auth=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["total_trades"] == 0

    def test_funnel_empty(self, client):
        r = client.get("/funnel", auth=AUTH)
        assert r.status_code == 200
        assert r.json() == {}

    def test_metrics_returns_snapshot(self, client):
        r = client.get("/metrics", auth=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert "opportunities_detected" in body


# ---------------------------------------------------------------------------
# Scanner controls
# ---------------------------------------------------------------------------

class TestScannerControls:
    def test_scanner_status_default_is_running(self, client):
        r = client.get("/scanner", auth=AUTH)
        assert r.status_code == 200
        snap = r.json()
        assert snap["paused"] is False
        assert snap["disabled_pairs"] == []
        assert snap["disabled_venues"] == []

    def test_pause_then_resume(self, client):
        r = client.post("/scanner/pause", auth=AUTH)
        assert r.status_code == 200
        assert r.json()["paused"] is True
        r = client.post("/scanner/resume", auth=AUTH)
        assert r.status_code == 200
        assert r.json()["paused"] is False

    def test_disable_pair_updates_state(self, client):
        r = client.post("/pairs/SOL/USDC/disable", auth=AUTH)
        assert r.status_code == 200
        assert "SOL/USDC" in r.json()["disabled_pairs"]

    def test_enable_pair_removes_from_state(self, client):
        client.post("/pairs/SOL/USDC/disable", auth=AUTH)
        r = client.post("/pairs/SOL/USDC/enable", auth=AUTH)
        assert r.status_code == 200
        assert "SOL/USDC" not in r.json()["disabled_pairs"]

    def test_disable_venue_updates_state(self, client):
        r = client.post("/venues/Jupiter-Best/disable", auth=AUTH)
        assert r.status_code == 200
        assert "Jupiter-Best" in r.json()["disabled_venues"]

    def test_control_state_drives_scan_gate(self, client):
        # Call scanner_pause through the API, then verify get_control
        # sees it — that's exactly the contract the run loop relies on.
        client.post("/scanner/pause", auth=AUTH)
        assert get_control().paused is True


# ---------------------------------------------------------------------------
# Execution kill switch
# ---------------------------------------------------------------------------

class TestKillSwitch:
    def test_kill_then_resume_toggles_file(self, client, tmp_path, monkeypatch):
        # Redirect the kill-switch path to a temp location so we don't
        # pollute the repo's data/.
        kill_path = tmp_path / ".kill"
        from api import app as app_mod
        monkeypatch.setattr(app_mod, "_KILL_SWITCH_PATH", kill_path)

        r = client.post("/execution/kill", auth=AUTH)
        assert r.status_code == 200
        assert r.json()["kill_switch_active"] is True
        assert kill_path.exists()

        r = client.post("/execution/resume", auth=AUTH)
        assert r.status_code == 200
        assert r.json()["kill_switch_active"] is False
        assert not kill_path.exists()


# ---------------------------------------------------------------------------
# /windows endpoints (multi-window activity feed for the dashboard).
# ---------------------------------------------------------------------------


class TestWindowsEndpoints:
    def test_windows_returns_every_predefined_key(self, client):
        r = client.get("/windows", auth=AUTH)
        assert r.status_code == 200
        body = r.json()
        # Every window key from observability.time_windows.WINDOWS is present.
        for key in ["5m", "15m", "1h", "4h", "8h", "24h", "3d", "1w", "1m"]:
            assert key in body
            # Each value has the expected top-level shape.
            entry = body[key]
            assert entry["window"] == key
            assert "opportunities" in entry
            assert "trades" in entry
            assert "profit" in entry

    def test_single_window_returns_stats(self, client):
        r = client.get("/windows/1h", auth=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["window"] == "1h"
        assert "opportunities" in body
        assert "trades" in body

    def test_unknown_window_returns_error_field(self, client):
        r = client.get("/windows/bogus", auth=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert "error" in body
        assert "bogus" in body["error"]

    def test_windows_requires_auth(self, client):
        r = client.get("/windows")
        assert r.status_code == 401
