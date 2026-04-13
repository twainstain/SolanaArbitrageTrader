"""FastAPI control plane for the arbitrage trading system.

Per the architecture doc, provides:
  - system health endpoints
  - current config inspection
  - enable/disable execution (kill switch)
  - recent opportunities / trades / failures
  - PnL summary and opportunity funnel

Usage::

    PYTHONPATH=src uvicorn api.app:app --port 8000

Or programmatically::

    from api.app import create_app
    app = create_app()
"""

from __future__ import annotations

import os
import secrets
from typing import Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from observability.metrics import MetricsCollector
from persistence.db import get_db, init_db
from persistence.repository import Repository
from risk.policy import RiskPolicy

# Module-level state — shared across the app lifetime.
_risk_policy = RiskPolicy()
_repo: Repository | None = None
_metrics = MetricsCollector()
_paused = False  # soft pause: stops new scans but lets in-flight trades complete

# Basic auth credentials — from env or defaults for testing.
DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "adminTest")

_security = HTTPBasic()


def _verify_credentials(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    """Verify HTTP Basic credentials. Returns username if valid."""
    correct_user = secrets.compare_digest(credentials.username, DASHBOARD_USER)
    correct_pass = secrets.compare_digest(credentials.password, DASHBOARD_PASS)
    if not (correct_user and correct_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _get_repo() -> Repository:
    global _repo
    if _repo is None:
        conn = init_db()
        _repo = Repository(conn)
    return _repo


def create_app(
    risk_policy: RiskPolicy | None = None,
    repo: Repository | None = None,
    metrics: MetricsCollector | None = None,
    require_auth: bool = True,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        require_auth: If True, all endpoints require HTTP Basic auth.
            Set False for tests.
    """
    global _risk_policy, _repo, _metrics

    if risk_policy is not None:
        _risk_policy = risk_policy
    if repo is not None:
        _repo = repo
    if metrics is not None:
        _metrics = metrics

    # Apply auth as a global dependency when enabled.
    deps = [Depends(_verify_credentials)] if require_auth else []

    app = FastAPI(
        title="Arbitrage Trader Control Plane",
        description="Operate and inspect the DEX arbitrage trading system.",
        version="0.1.0",
        dependencies=deps,
    )

    # --- Health ---

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "execution_enabled": _risk_policy.execution_enabled,
        }

    # --- Kill Switch ---

    @app.post("/execution")
    def toggle_execution(body: Dict):
        _risk_policy.execution_enabled = bool(body.get("enabled", False))
        return {
            "execution_enabled": _risk_policy.execution_enabled,
            "message": "enabled" if _risk_policy.execution_enabled else "disabled",
        }

    @app.get("/execution")
    def get_execution_status():
        return {"execution_enabled": _risk_policy.execution_enabled}

    # --- Pause (soft pause — separate from kill switch) ---

    @app.get("/pause")
    def get_pause_status():
        return {"paused": _paused}

    @app.post("/pause")
    def toggle_pause(body: Dict):
        global _paused
        _paused = bool(body.get("paused", False))
        return {"paused": _paused}

    # --- Risk Policy ---

    @app.get("/risk/policy")
    def get_risk_policy():
        return _risk_policy.to_dict()

    # --- Opportunities ---

    @app.get("/opportunities")
    def get_opportunities(limit: int = 50, window: Optional[str] = None,
                          chain: Optional[str] = None):
        """Get recent opportunities, optionally filtered by time window and chain."""
        repo = _get_repo()
        if window:
            from observability.time_windows import WINDOWS
            from datetime import datetime, timedelta, timezone
            td = WINDOWS.get(window)
            if td:
                since = (datetime.now(timezone.utc) - td).isoformat()
                query = "SELECT * FROM opportunities WHERE detected_at >= ?"
                params = [since]
                if chain:
                    query += " AND chain = ?"
                    params.append(chain)
                query += " ORDER BY detected_at DESC LIMIT ?"
                params.append(limit)
                rows = repo.conn.execute(query, tuple(params)).fetchall()
                return [dict(r) for r in rows]
        return repo.get_recent_opportunities(limit=limit)

    @app.get("/opportunities/{opp_id}")
    def get_opportunity(opp_id: str):
        repo = _get_repo()
        opp = repo.get_opportunity(opp_id)
        if opp is None:
            raise HTTPException(status_code=404, detail="Opportunity not found")
        return opp

    @app.get("/opportunities/{opp_id}/pricing")
    def get_pricing(opp_id: str):
        repo = _get_repo()
        pricing = repo.get_pricing(opp_id)
        if pricing is None:
            raise HTTPException(status_code=404, detail="No pricing for this opportunity")
        return pricing

    @app.get("/opportunities/{opp_id}/risk")
    def get_risk_decision(opp_id: str):
        repo = _get_repo()
        dec = repo.get_risk_decision(opp_id)
        if dec is None:
            raise HTTPException(status_code=404, detail="No risk decision for this opportunity")
        return dec

    @app.get("/opportunities/{opp_id}/simulation")
    def get_simulation(opp_id: str):
        repo = _get_repo()
        sim = repo.get_simulation(opp_id)
        if sim is None:
            raise HTTPException(status_code=404, detail="No simulation for this opportunity")
        return sim

    # --- Opportunity Detail (all data in one call) ---

    @app.get("/opportunities/{opp_id}/full")
    def get_opportunity_full(opp_id: str):
        """Return all data for an opportunity: pricing, risk, simulation, execution."""
        repo = _get_repo()
        opp = repo.get_opportunity(opp_id)
        if opp is None:
            raise HTTPException(status_code=404, detail="Opportunity not found")
        return {
            "opportunity": opp,
            "pricing": repo.get_pricing(opp_id),
            "risk_decision": repo.get_risk_decision(opp_id),
            "simulation": repo.get_simulation(opp_id),
        }

    @app.get("/opportunity/{opp_id}", response_class=HTMLResponse)
    def opportunity_detail_page(opp_id: str):
        """HTML detail page for a single opportunity."""
        from api.dashboard import OPPORTUNITY_DETAIL_HTML
        return HTMLResponse(content=OPPORTUNITY_DETAIL_HTML)

    # --- Aggregations ---

    @app.get("/pnl")
    def get_pnl():
        repo = _get_repo()
        return repo.get_pnl_summary()

    @app.get("/funnel")
    def get_funnel():
        repo = _get_repo()
        return repo.get_opportunity_funnel()

    # --- Metrics ---

    @app.get("/metrics")
    def get_metrics():
        return _metrics.snapshot()

    # --- Dashboard ---

    @app.get("/dashboard/distinct-chains")
    def distinct_chains():
        repo = _get_repo()
        rows = repo.conn.execute(
            "SELECT DISTINCT chain FROM opportunities WHERE chain != '' ORDER BY chain"
        ).fetchall()
        return [r["chain"] for r in rows]

    @app.get("/dashboard/hourly-bars")
    def hourly_bars():
        """Return per-chain win/loss counts for the last 24h, grouped by hour."""
        from datetime import datetime, timedelta, timezone
        repo = _get_repo()
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        rows = repo.conn.execute(
            "SELECT chain, status, "
            "  substr(detected_at, 1, 13) as hour, "
            "  COUNT(*) as cnt "
            "FROM opportunities WHERE detected_at >= ? AND chain != '' "
            "GROUP BY chain, status, hour ORDER BY hour",
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard():
        from api.dashboard import DASHBOARD_HTML
        return HTMLResponse(
            content=DASHBOARD_HTML,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    @app.get("/dashboard/window/{window_key}")
    def dashboard_window(window_key: str, chain: Optional[str] = None):
        from observability.time_windows import get_windowed_stats
        repo = _get_repo()
        return get_windowed_stats(repo.conn, window_key, chain)

    @app.get("/dashboard/windows")
    def dashboard_all_windows(chain: Optional[str] = None):
        from observability.time_windows import get_all_windows
        repo = _get_repo()
        return get_all_windows(repo.conn, chain)

    @app.get("/dashboard/chains")
    def dashboard_chains(window: str = "24h"):
        from observability.time_windows import get_chain_summary
        repo = _get_repo()
        return get_chain_summary(repo.conn, window)

    # --- Replay ---

    @app.post("/opportunities/{opp_id}/replay")
    def replay_opportunity(opp_id: str):
        """Re-run a historical candidate through pricing + risk evaluation.

        Does NOT re-execute — only re-evaluates with current market state
        and risk policy. Useful for debugging rejected candidates.
        """
        repo = _get_repo()
        opp = repo.get_opportunity(opp_id)
        if opp is None:
            raise HTTPException(status_code=404, detail="Opportunity not found")

        pricing = repo.get_pricing(opp_id)
        risk_dec = repo.get_risk_decision(opp_id)
        sim = repo.get_simulation(opp_id)

        # Re-evaluate risk with CURRENT policy.
        from models import Opportunity, ZERO
        from decimal import Decimal as D
        replay_opp = Opportunity(
            pair=opp["pair"],
            buy_dex=opp["buy_dex"],
            sell_dex=opp["sell_dex"],
            trade_size=D(pricing["input_amount"]) if pricing else D("0"),
            cost_to_buy_quote=D(pricing["input_amount"]) if pricing else D("0"),
            proceeds_from_sell_quote=D(pricing["estimated_output"]) if pricing else D("0"),
            gross_profit_quote=D("0"),
            net_profit_quote=D("0"),
            net_profit_base=D(pricing["expected_net_profit"]) if pricing else D("0"),
            gas_cost_base=D(pricing["gas_estimate"]) if pricing else D("0"),
        )
        new_verdict = _risk_policy.evaluate(replay_opp)

        return {
            "opportunity": opp,
            "original_pricing": pricing,
            "original_risk": risk_dec,
            "original_simulation": sim,
            "replay_risk_verdict": {
                "approved": new_verdict.approved,
                "reason": new_verdict.reason,
                "details": new_verdict.details,
            },
            "current_policy": _risk_policy.to_dict(),
        }

    return app


# Default app instance for `uvicorn api.app:app` — auth enabled by default.
app = create_app(require_auth=True)
