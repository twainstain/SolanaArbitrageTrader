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

import json
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
_scanner_ref = None  # reference to EventDrivenScanner for start/stop via API
_diagnostics = None  # reference to QuoteDiagnostics for /diagnostics/quotes

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


def _load_launch_readiness(repo: Repository) -> dict:
    launch_blockers_raw = repo.get_checkpoint("launch_blockers") or "[]"
    try:
        launch_blockers = json.loads(launch_blockers_raw)
    except json.JSONDecodeError:
        launch_blockers = []
    return {
        "launch_chain": repo.get_checkpoint("launch_chain") or "",
        "launch_ready": (repo.get_checkpoint("launch_ready") or "0") == "1",
        "launch_blockers": launch_blockers,
        "executor_key_configured": (repo.get_checkpoint("executor_key_configured") or "0") == "1",
        "executor_contract_configured": (repo.get_checkpoint("executor_contract_configured") or "0") == "1",
        "rpc_configured": (repo.get_checkpoint("rpc_configured") or "0") == "1",
    }


def set_scanner_ref(scanner: object) -> None:
    """Set a reference to the EventDrivenScanner for API control."""
    global _scanner_ref
    _scanner_ref = scanner


def set_diagnostics_ref(diagnostics: object) -> None:
    """Set a reference to QuoteDiagnostics for /diagnostics/quotes."""
    global _diagnostics
    _diagnostics = diagnostics


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
        chain = body.get("chain")  # Optional: per-chain toggle
        requested = body.get("enabled")
        mode = body.get("mode")  # "live", "simulated", "disabled"

        if chain:
            # Per-chain mode change
            if mode:
                _risk_policy.set_chain_mode(chain, mode)
            elif requested is not None:
                _risk_policy.set_chain_mode(chain, "live" if requested else "simulated")
            return {
                "chain": chain,
                "mode": _risk_policy.get_chain_mode(chain),
                "chain_execution_mode": dict(_risk_policy.chain_execution_mode),
            }

        # Global toggle (backward compatible)
        if mode:
            _risk_policy.execution_enabled = (mode == "live")
        elif requested is not None:
            requested = bool(requested)
            if requested:
                repo = _get_repo()
                readiness = _load_launch_readiness(repo)
                if not readiness["launch_ready"]:
                    _risk_policy.execution_enabled = False
                    raise HTTPException(
                        status_code=409,
                        detail={"message": "launch_not_ready", **readiness},
                    )
            _risk_policy.execution_enabled = requested

        return {
            "execution_enabled": _risk_policy.execution_enabled,
            "chain_execution_mode": dict(_risk_policy.chain_execution_mode),
            "message": "updated",
        }

    @app.get("/execution")
    def get_execution_status():
        from chain_executor import SWAP_ROUTERS, AAVE_V3_POOL
        known_chains = ["arbitrum", "optimism", "ethereum", "base"]
        chain_status = {}
        for ch in known_chains:
            mode = _risk_policy.get_chain_mode(ch)
            has_routers = ch in SWAP_ROUTERS
            has_aave = ch in AAVE_V3_POOL
            chain_status[ch] = {
                "mode": mode,
                "has_routers": has_routers,
                "has_aave": has_aave,
                "executable": has_routers and has_aave,
            }
        return {
            "execution_enabled": _risk_policy.execution_enabled,
            "chain_execution_mode": dict(_risk_policy.chain_execution_mode),
            "chains": chain_status,
        }

    @app.get("/launch-readiness")
    def get_launch_readiness():
        repo = _get_repo()
        return _load_launch_readiness(repo)

    # --- Pause (soft pause — separate from kill switch) ---

    @app.get("/pause")
    def get_pause_status():
        return {"paused": _paused}

    @app.post("/pause")
    def toggle_pause(body: Dict):
        global _paused
        _paused = bool(body.get("paused", False))
        return {"paused": _paused}

    # --- Scanner Control (start/stop/status) ---

    @app.get("/scanner")
    def get_scanner_status():
        """Get the current scanner status."""
        if _scanner_ref is None:
            return {"status": "not_configured", "running": False}
        running = getattr(_scanner_ref, '_running', False)
        return {
            "status": "running" if running else "stopped",
            "running": running,
            "paused": _paused,
            "execution_enabled": _risk_policy.execution_enabled,
        }

    @app.post("/scanner/start")
    def start_scanner():
        """Start the scanner in a background thread."""
        if _scanner_ref is None:
            raise HTTPException(status_code=400, detail="Scanner not configured")
        if getattr(_scanner_ref, '_running', False):
            return {"status": "already_running"}
        import threading
        t = threading.Thread(target=_scanner_ref.run, daemon=True, name="scanner-api")
        t.start()
        return {"status": "started"}

    @app.post("/scanner/stop")
    def stop_scanner():
        """Stop the scanner gracefully."""
        if _scanner_ref is None:
            raise HTTPException(status_code=400, detail="Scanner not configured")
        _scanner_ref.stop()
        return {"status": "stopping"}

    # --- Risk Policy ---

    @app.get("/risk/policy")
    def get_risk_policy():
        return _risk_policy.to_dict()

    # --- Opportunities ---

    @app.get("/opportunities")
    def get_opportunities(limit: int = 50, window: Optional[str] = None,
                          chain: Optional[str] = None,
                          start: Optional[str] = None,
                          end: Optional[str] = None,
                          status: Optional[str] = None,
                          pair: Optional[str] = None):
        """Get recent opportunities with net_profit and execution data.

        Filters (all optional):
          - window: predefined window key (5m, 15m, 1h, 4h, 8h, 24h, 3d, 1w, 1m)
          - start/end: ISO timestamp (UTC)
          - chain: filter by chain name
          - status: filter by opportunity status
          - pair: filter by trading pair
          - limit: max rows (default 50)
        """
        repo = _get_repo()

        query = """
            SELECT o.*, p.expected_net_profit, p.fee_cost, p.slippage_cost, p.gas_estimate,
                   e.tx_hash, e.submission_type,
                   tr.included as exec_included, tr.reverted as exec_reverted,
                   tr.gas_used as exec_gas_used,
                   tr.realized_profit_quote, tr.gas_cost_base as exec_gas_cost_base,
                   tr.actual_net_profit, tr.profit_currency
            FROM opportunities o
            LEFT JOIN pricing_results p ON o.opportunity_id = p.opportunity_id
            LEFT JOIN execution_attempts e ON o.opportunity_id = e.opportunity_id
            LEFT JOIN trade_results tr ON e.execution_id = tr.execution_id
        """
        params: list = []
        conditions: list[str] = []

        # start/end take precedence over window.
        if start:
            conditions.append("o.detected_at >= ?")
            params.append(start)
        if end:
            conditions.append("o.detected_at <= ?")
            params.append(end)

        if not start and not end and window:
            from observability.time_windows import WINDOWS
            from datetime import datetime, timedelta, timezone
            td = WINDOWS.get(window)
            if td:
                since = (datetime.now(timezone.utc) - td).isoformat()
                conditions.append("o.detected_at >= ?")
                params.append(since)

        if chain:
            conditions.append("o.chain = ?")
            params.append(chain)
        if status:
            conditions.append("o.status = ?")
            params.append(status)
        if pair:
            conditions.append("o.pair = ?")
            params.append(pair)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY o.detected_at DESC LIMIT ?"
        params.append(limit)

        rows = repo.conn.execute(query, tuple(params)).fetchall()
        return [dict(r) for r in rows]

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
        execution = repo.get_latest_execution_attempt(opp_id)
        trade_result = None
        if execution is not None:
            trade_result = repo.get_trade_result(int(execution["execution_id"]))
        return {
            "opportunity": opp,
            "pricing": repo.get_pricing(opp_id),
            "risk_decision": repo.get_risk_decision(opp_id),
            "simulation": repo.get_simulation(opp_id),
            "execution_attempt": execution,
            "trade_result": trade_result,
        }

    @app.get("/opportunity/{opp_id}", response_class=HTMLResponse)
    def opportunity_detail_page(opp_id: str):
        """HTML detail page for a single opportunity."""
        from api.dashboard import OPPORTUNITY_DETAIL_HTML
        return HTMLResponse(
            content=OPPORTUNITY_DETAIL_HTML,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    # --- Aggregations ---

    @app.get("/pnl")
    def get_pnl():
        repo = _get_repo()
        return repo.get_pnl_summary()

    @app.get("/pnl/analytics")
    def get_pnl_analytics(
        chain: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        window: Optional[str] = None,
    ):
        """Comprehensive PnL analytics with filters."""
        # Resolve time range from window shortcut or explicit dates.
        if window and not since:
            from observability.time_windows import WINDOWS
            from datetime import datetime, timezone
            td = WINDOWS.get(window)
            if td:
                since = (datetime.now(timezone.utc) - td).isoformat()

        repo = _get_repo()
        return repo.get_pnl_analytics(chain=chain, since=since, until=until)

    @app.get("/scan-history")
    def get_scan_history(
        chain: Optional[str] = None,
        pair: Optional[str] = None,
        reason: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        window: Optional[str] = None,
        limit: int = 500,
    ):
        """Query scan history — every evaluated pair per scan cycle."""
        if window and not since:
            from observability.time_windows import WINDOWS
            from datetime import datetime, timezone
            td = WINDOWS.get(window)
            if td:
                since = (datetime.now(timezone.utc) - td).isoformat()
        repo = _get_repo()
        return repo.get_scan_history(chain=chain, pair=pair, reason=reason,
                                      since=since, until=until, limit=limit)

    @app.get("/scan-history/summary")
    def get_scan_summary(
        chain: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        window: Optional[str] = None,
    ):
        """Aggregate scan history: filter breakdown, near-misses, spread stats."""
        if window and not since:
            from observability.time_windows import WINDOWS
            from datetime import datetime, timezone
            td = WINDOWS.get(window)
            if td:
                since = (datetime.now(timezone.utc) - td).isoformat()
        repo = _get_repo()
        return repo.get_scan_summary(chain=chain, since=since, until=until)

    @app.get("/funnel")
    def get_funnel():
        repo = _get_repo()
        return repo.get_opportunity_funnel()

    # --- Metrics ---

    @app.get("/metrics")
    def get_metrics():
        return _metrics.snapshot()

    @app.get("/operations")
    def get_operations():
        repo = _get_repo()
        live_executable_chains = repo.get_checkpoint("live_executable_chains") or ""
        live_executable_dexes = repo.get_checkpoint("live_executable_dexes") or ""
        readiness = _load_launch_readiness(repo)
        return {
            "db_backend": repo.conn.backend,
            "discovered_pairs_count": repo.count_discovered_pairs(),
            "enabled_pools_total": repo.count_enabled_pools(),
            "discovery_snapshot_source": repo.get_checkpoint("discovery_snapshot_source") or "unknown",
            "last_discovery_pair_count": int(repo.get_checkpoint("discovery_pair_count") or 0),
            "last_monitored_pools_synced": int(repo.get_checkpoint("monitored_pools_synced") or 0),
            "live_stack_ready": (repo.get_checkpoint("live_stack_ready") or "0") == "1",
            "live_rollout_target": repo.get_checkpoint("live_rollout_target") or "",
            "live_executable_chains": [c for c in live_executable_chains.split(",") if c],
            "live_executable_dexes": [d for d in live_executable_dexes.split(",") if d],
            **readiness,
        }

    # --- Diagnostics ---

    @app.get("/diagnostics/quotes")
    def get_quote_diagnostics():
        """Per-DEX quote health: success rate, latency, last error."""
        if _diagnostics is None:
            return {"dexes": {}}
        snapshot = _diagnostics.snapshot()
        by_dex: dict[str, list] = {}
        for key, data in snapshot.items():
            dex = key.split(":", 1)[0]
            by_dex.setdefault(dex, []).append({"key": key, **data})
        return {"dexes": by_dex}

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

    @app.get("/ops", response_class=HTMLResponse)
    def ops_dashboard():
        from api.dashboard import OPS_DASHBOARD_HTML
        return HTMLResponse(
            content=OPS_DASHBOARD_HTML,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    @app.get("/analytics", response_class=HTMLResponse)
    def analytics_dashboard():
        from api.dashboard import ANALYTICS_HTML
        return HTMLResponse(
            content=ANALYTICS_HTML,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    @app.get("/dashboard/window/{window_key}")
    def dashboard_window(window_key: str, chain: Optional[str] = None):
        from observability.time_windows import get_windowed_stats
        repo = _get_repo()
        return get_windowed_stats(repo.conn, window_key, chain)

    @app.get("/dashboard/range")
    def dashboard_range(start: str, end: Optional[str] = None,
                        chain: Optional[str] = None):
        """Get stats for a custom time range (ISO timestamps in UTC).

        Args:
            start: ISO timestamp (UTC) for range start.
            end: ISO timestamp (UTC) for range end. Defaults to now.
            chain: Optional chain filter.
        """
        from observability.time_windows import get_range_stats
        repo = _get_repo()
        return get_range_stats(repo.conn, start, end, chain)

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

    # --- Wallet Balance ---

    @app.get("/wallet/balance")
    def wallet_balance():
        """Fetch wallet balance from on-chain RPC (non-blocking cache)."""
        import os
        from web3 import Web3
        from contracts import PUBLIC_RPC_URLS
        from env import get_rpc_overrides

        private_key = os.environ.get("EXECUTOR_PRIVATE_KEY", "")
        if not private_key:
            return {"address": "", "balances": {}, "error": "EXECUTOR_PRIVATE_KEY not set"}

        try:
            account = Web3().eth.account.from_key(private_key)
            address = account.address
        except Exception:
            return {"address": "", "balances": {}, "error": "invalid key"}

        rpc_overrides = get_rpc_overrides()
        balances = {}
        for chain in ["arbitrum", "ethereum", "base", "optimism"]:
            rpc_url = rpc_overrides.get(chain, PUBLIC_RPC_URLS.get(chain, ""))
            if not rpc_url:
                continue
            try:
                w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 3}))
                bal_wei = w3.eth.get_balance(address)
                balances[chain] = float(bal_wei) / 1e18
            except Exception:
                balances[chain] = None
        return {"address": address, "balances": balances}

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
