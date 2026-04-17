"""SolanaTrader ops dashboard — readiness, RPC + Jupiter + venue health.

Mirrors the production /ops layout (arb-trader.yeda-ai.com) adapted for
Solana: instead of per-EVM-chain RPC cards, we show per-Solana-RPC-URL
cards and per-venue (Jupiter / Raydium / Orca) quote health rows.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path

import requests

from control_state import get_control
from core.env import get_jupiter_api_url, get_solana_rpc_urls
from dashboards._shared import (
    card, filter_bar, fmt_num, fmt_pct, fmt_sol,
    page, resolve_filters, tag,
)
from observability.wallet import get_wallet_balances
from persistence.repository import Repository
from risk.policy import RiskPolicy

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LATENCY_FILE = _PROJECT_ROOT / "logs" / "latency.jsonl"
_KILL_SWITCH = _PROJECT_ROOT / "data" / ".execution_kill_switch"


def render(
    repo: Repository,
    metrics=None,
    risk_policy: RiskPolicy | None = None,
    filters: dict | None = None,
) -> str:
    f = resolve_filters(filters)
    # --- execution state ---
    execution_on = os.environ.get("SOLANA_EXECUTION_ENABLED", "").lower() in ("true", "1", "yes")
    kill = _KILL_SWITCH.exists()
    wallet_configured = bool(os.environ.get("SOLANA_WALLET_KEYPAIR_PATH"))

    if kill:
        exec_state, exec_cls = tag("KILL-SWITCH ACTIVE", "err"), "status-bad"
    elif execution_on and wallet_configured:
        exec_state, exec_cls = tag("LIVE READY", "ok"), "status-ok"
    elif execution_on and not wallet_configured:
        exec_state, exec_cls = tag("NO WALLET", "warn"), "status-warn"
    else:
        exec_state, exec_cls = tag("PAPER", "muted"), ""

    # --- wallet balance ---
    wb = get_wallet_balances()
    sol_bal = wb.get("balances", {}).get("SOL")

    # --- infrastructure cards ---
    infra = "".join([
        card("Execution state", exec_state, value_class=exec_cls),
        card("Kill switch",
             tag("ACTIVE", "err") if kill else tag("CLEAR", "ok"),
             value_class="status-bad" if kill else "status-ok"),
        card("Wallet SOL",
             fmt_sol(sol_bal, 4) if sol_bal is not None else "—",
             value_class="status-bad" if (sol_bal is not None and float(sol_bal) < 0.005) else ""),
        card("DB backend", repo.conn.backend),
        card("Python",
             f"{os.sys.version_info.major}.{os.sys.version_info.minor}"),
        card("Pair count",
             fmt_num(len(repo.get_enabled_pairs()))),
    ])

    # --- RPC endpoint cards ---
    rpc_urls = get_solana_rpc_urls() or ["https://api.mainnet-beta.solana.com"]
    rpc_cards = []
    for i, url in enumerate(rpc_urls):
        label = "PRIMARY" if i == 0 else f"FALLBACK {i}"
        status, latency_ms, slot = _check_rpc(url)
        # Show URL host without leaking API key in path
        host = url.split("//", 1)[1].split("/", 1)[0]
        rpc_cards.append(card(
            label,
            tag(status.upper(), "ok" if status == "ok" else "err"),
            sub=f"{host} · {latency_ms:.0f}ms · slot {slot}" if status == "ok" else host,
            value_class="status-ok" if status == "ok" else "status-bad",
        ))
    # Jupiter health
    jup_status, jup_latency = _check_jupiter()
    jup_host = get_jupiter_api_url().split("//", 1)[1].split("/", 1)[0]
    rpc_cards.append(card(
        "JUPITER API",
        tag(jup_status.upper(), "ok" if jup_status == "ok" else "err"),
        sub=f"{jup_host} · {jup_latency:.0f}ms" if jup_status == "ok" else jup_host,
        value_class="status-ok" if jup_status == "ok" else "status-bad",
    ))

    rpc_grid = "".join(rpc_cards)

    # --- venue health (per pair) ---
    venue_rows = _venue_health_rows(repo)
    venue_tbl = f"""
      <table>
        <thead><tr>
          <th>Venue</th><th>Pair</th>
          <th class='num'>Success rate</th><th class='num'>Quotes</th>
          <th class='num'>Avg latency</th><th>Last outcome</th><th>Last error</th>
        </tr></thead>
        <tbody>{venue_rows or "<tr><td colspan='7'><span class='muted'>no diagnostics recorded yet — run for a few minutes</span></td></tr>"}</tbody>
      </table>
    """

    # --- scan metrics cards ---
    snap = metrics.snapshot() if metrics else {}
    scan_cards = "".join([
        card("Uptime (s)", fmt_num(snap.get("uptime_seconds", 0))),
        card("Opps / min", f"{snap.get('opportunities_per_minute', 0):.2f}"),
        card("Detected",   fmt_num(snap.get("opportunities_detected", 0))),
        card("Rejected",   fmt_num(snap.get("opportunities_rejected", 0))),
        card("Sim pass rate", fmt_pct(snap.get("simulation_success_rate_pct", 0), 1)),
        card("Inclusion rate", fmt_pct(snap.get("inclusion_rate_pct", 0), 1)),
        card("Revert rate", fmt_pct(snap.get("revert_rate_pct", 0), 1)),
        card("Avg latency", f"{snap.get('avg_latency_ms', 0):.0f} ms"),
    ])

    # --- pipeline latency percentiles ---
    lat = _latency_summary()
    lat_rows = "".join(
        f"<tr><td>{stage}</td>"
        f"<td class='num'>{v['p50']:.0f}</td>"
        f"<td class='num'>{v['p95']:.0f}</td>"
        f"<td class='num'>{v['max']:.0f}</td>"
        f"<td class='num'>{v['n']}</td></tr>"
        for stage, v in sorted(lat.items())
    ) or "<tr><td colspan='5'><span class='muted'>no latency data yet</span></td></tr>"
    lat_tbl = f"""
      <table>
        <thead><tr><th>Stage</th><th class='num'>p50 (ms)</th>
          <th class='num'>p95 (ms)</th><th class='num'>max (ms)</th>
          <th class='num'>n</th></tr></thead>
        <tbody>{lat_rows}</tbody>
      </table>
    """

    # --- controls ---
    control = get_control()
    scanner_btn = (
        f"<form method='post' action='/scanner/resume' class='inline'>"
        f"<button class='btn btn-green' type='submit'>Resume scanner</button></form>"
        if control.paused
        else
        f"<form method='post' action='/scanner/pause' class='inline'>"
        f"<button class='btn btn-red' type='submit'>Pause scanner</button></form>"
    )
    scanner_state = tag("PAUSED", "warn") if control.paused else tag("RUNNING", "ok")
    controls = f"""
      <p>
        Scanner: <strong>{scanner_state}</strong> &nbsp; {scanner_btn}
      </p>
      <p>Kill switch file: <code>{_KILL_SWITCH}</code> — re-checked before every tx build.</p>
      <form method='post' action='/execution/kill' class='inline'>
        <button class='btn btn-red' type='submit'>Activate kill switch</button>
      </form>
      &nbsp;
      <form method='post' action='/execution/resume' class='inline'>
        <button class='btn btn-green' type='submit'>Clear kill switch</button>
      </form>
    """

    # --- pair + venue toggles ---
    pairs_all = sorted({
        r["pair"] for r in repo.get_recent_opportunities(limit=500) if r.get("pair")
    })
    venues_all = sorted({
        v
        for r in repo.get_recent_opportunities(limit=500)
        for v in (r.get("buy_venue"), r.get("sell_venue"))
        if v
    })
    toggle_rows = []
    for p in pairs_all:
        disabled = p in control.disabled_pairs
        btn_action = "enable" if disabled else "disable"
        btn_class = "btn-green" if disabled else "btn-red"
        btn_label = "Enable" if disabled else "Disable"
        toggle_rows.append(
            f"<tr><td>{p}</td><td>{tag('DISABLED', 'err') if disabled else tag('enabled', 'ok')}</td>"
            f"<td><form method='post' action='/pairs/{p}/{btn_action}' class='inline'>"
            f"<button class='btn {btn_class}' type='submit'>{btn_label}</button></form></td></tr>"
        )
    pair_toggle_tbl = f"""
      <table>
        <thead><tr><th>Pair</th><th>State</th><th>Action</th></tr></thead>
        <tbody>{"".join(toggle_rows) or "<tr><td colspan='3'><span class='muted'>no pairs observed yet</span></td></tr>"}</tbody>
      </table>
    """

    venue_toggle_rows = []
    for v in venues_all:
        disabled = v in control.disabled_venues
        btn_action = "enable" if disabled else "disable"
        btn_class = "btn-green" if disabled else "btn-red"
        btn_label = "Enable" if disabled else "Disable"
        venue_toggle_rows.append(
            f"<tr><td>{v}</td><td>{tag('DISABLED', 'err') if disabled else tag('enabled', 'ok')}</td>"
            f"<td><form method='post' action='/venues/{v}/{btn_action}' class='inline'>"
            f"<button class='btn {btn_class}' type='submit'>{btn_label}</button></form></td></tr>"
        )
    venue_toggle_tbl = f"""
      <table>
        <thead><tr><th>Venue</th><th>State</th><th>Action</th></tr></thead>
        <tbody>{"".join(venue_toggle_rows) or "<tr><td colspan='3'><span class='muted'>no venues observed yet</span></td></tr>"}</tbody>
      </table>
    """

    # --- launch gates ---
    gates = [
        ("SOLANA_RPC_URL",              bool(get_solana_rpc_urls()),                      "RPC configured"),
        ("JUPITER_API_URL",             bool(get_jupiter_api_url()),                      "Jupiter URL set"),
        ("SOLANA_EXECUTION_ENABLED",    execution_on,                                      "execution opt-in"),
        ("SOLANA_WALLET_KEYPAIR_PATH",  wallet_configured,                                 "wallet path set"),
        ("Kill switch absent",          not kill,                                          "no kill-switch file"),
        ("Wallet funded >= 0.005 SOL",  bool(sol_bal and float(sol_bal) >= 0.005),         "wallet balance"),
    ]
    gate_rows = "".join(
        f"<tr><td>{name}</td>"
        f"<td>{tag('OK', 'ok') if ok else tag('MISSING', 'warn')}</td>"
        f"<td>{desc}</td></tr>"
        for name, ok, desc in gates
    )
    gate_tbl = f"""
      <p>For live execution <em>every</em> row must be <code>OK</code>.  Scanner-only mode only needs the first two.</p>
      <table>
        <thead><tr><th>Gate</th><th>State</th><th>Notes</th></tr></thead>
        <tbody>{gate_rows}</tbody>
      </table>
    """

    # --- risk policy cards ---
    policy = risk_policy or RiskPolicy()
    pol = policy.to_dict()
    risk_cards = "".join([
        card("Min net profit", fmt_sol(pol.get("min_net_profit")), value_class="card-value small"),
        card("Min spread %",   fmt_pct(pol.get("min_spread_pct"))),
        card("Max slippage",   f"{pol.get('max_slippage_bps')} bps"),
        card("Min liq (USD)",  f"${float(pol.get('min_liquidity_usd', 0)):,.0f}"),
        card("Max quote age",  f"{pol.get('max_quote_age_seconds')} s"),
        card("Max fee/profit", fmt_pct(float(pol.get('max_fee_profit_ratio', 0)) * 100, 0)),
        card("Max flags",      fmt_num(pol.get("max_warning_flags"))),
        card("Rate limit/h",   fmt_num(pol.get("max_trades_per_hour"))),
        card("Max exposure",   fmt_sol(pol.get("max_exposure_per_pair"))),
    ])

    body = f"""
      <h2>Infrastructure</h2>
      <div class='grid'>{infra}</div>

      <h2>RPC Endpoints</h2>
      <div class='grid'>{rpc_grid}</div>

      <h2>Venue Health (per pair)</h2>
      {venue_tbl}

      <h2>Scan Metrics</h2>
      <div class='grid tight'>{scan_cards}</div>

      <h2>Scanner &amp; Kill-switch Controls</h2>
      {controls}

      <h2>Pair Toggles</h2>
      {pair_toggle_tbl}

      <h2>Venue Toggles</h2>
      {venue_toggle_tbl}

      <h2>Launch Gates</h2>
      {gate_tbl}

      <h2>Pipeline Latency</h2>
      {lat_tbl}

      <h2>Risk Policy</h2>
      <div class='grid tight'>{risk_cards}</div>
    """
    return page("Operations & Diagnostics", body, active="ops", refresh_seconds=15)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_rpc(url: str) -> tuple[str, float, int]:
    t0 = time.monotonic()
    try:
        resp = requests.post(
            url, json={"jsonrpc": "2.0", "id": 1, "method": "getSlot"}, timeout=2.0,
        )
        resp.raise_for_status()
        slot = int(resp.json().get("result", 0))
        return "ok", (time.monotonic() - t0) * 1000, slot
    except Exception:
        return "unreachable", 0, 0


def _check_jupiter() -> tuple[str, float]:
    url = get_jupiter_api_url().rstrip("/") + "/quote"
    params = {
        "inputMint": "So11111111111111111111111111111111111111112",
        "outputMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "amount": "10000000", "slippageBps": "50",
    }
    t0 = time.monotonic()
    try:
        resp = requests.get(url, params=params, timeout=2.0)
        resp.raise_for_status()
        return ("ok" if "outAmount" in resp.json() else "no-route"), (time.monotonic() - t0) * 1000
    except Exception:
        return "unreachable", 0


def _venue_health_rows(repo: Repository) -> str:
    """Pull quote_diagnostics + scan_history to show per-venue quote health.

    Priority:
      1. If quote_diagnostics has rows (periodic flush from QuoteDiagnostics),
         use those numbers.
      2. Otherwise derive from scan_history (each row is a successful quote).
    """
    diag = repo.get_diagnostics_snapshot(limit=100)
    if diag:
        rows = []
        # Deduplicate by (venue, pair) keeping the most recent snapshot.
        seen = set()
        for d in diag:
            key = (d["venue"], d["pair"])
            if key in seen:
                continue
            seen.add(key)
            success = d["success_count"]
            total = d["total_count"] or 0
            rate = (success / total * 100) if total else 0
            rows.append(
                f"<tr><td>{d['venue']}</td><td>{d['pair']}</td>"
                f"<td class='num'>{rate:.1f}%</td>"
                f"<td class='num'>{total}</td>"
                f"<td class='num'>{d['avg_latency_ms']:.0f} ms</td>"
                f"<td>{_outcome_tag(d.get('last_outcome'))}</td>"
                f"<td><span class='muted'>{d.get('last_error', '') or '—'}</span></td></tr>"
            )
        return "".join(rows)

    # Fallback: derive from scan_history
    rows_dict = defaultdict(lambda: {"n": 0})
    try:
        for r in repo.get_scan_history(limit=1000):
            for venue_col in ("buy_venue", "sell_venue"):
                v = r.get(venue_col)
                if not v:
                    continue
                key = (v, r["pair"])
                rows_dict[key]["n"] += 1
    except Exception:
        return ""
    rows = []
    for (venue, pair), info in sorted(rows_dict.items(), key=lambda kv: -kv[1]["n"])[:30]:
        rows.append(
            f"<tr><td>{venue}</td><td>{pair}</td>"
            f"<td class='num'>—</td>"
            f"<td class='num'>{info['n']}</td>"
            f"<td class='num muted'>—</td>"
            f"<td>{tag('scan_ok', 'ok')}</td>"
            f"<td><span class='muted'>—</span></td></tr>"
        )
    return "".join(rows)


def _outcome_tag(outcome: str | None) -> str:
    if not outcome:
        return tag("—", "muted")
    if outcome == "success":
        return tag("success", "ok")
    if outcome in ("timeout", "error"):
        return tag(outcome, "err")
    return tag(outcome, "warn")


def _latency_summary(tail_bytes: int = 200_000) -> dict:
    if not _LATENCY_FILE.exists():
        return {}
    try:
        size = _LATENCY_FILE.stat().st_size
        with _LATENCY_FILE.open("rb") as f:
            if size > tail_bytes:
                f.seek(size - tail_bytes)
                f.readline()
            lines = f.read().decode("utf-8", errors="ignore").splitlines()
    except Exception:
        return {}

    stages: dict[str, list[float]] = defaultdict(list)
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        for stage, v in (rec.get("pipeline_ms") or {}).items():
            try:
                stages[stage].append(float(v))
            except (ValueError, TypeError):
                continue
        for stage, v in (rec.get("scan_marks_ms") or {}).items():
            try:
                stages[f"scan:{stage}"].append(float(v))
            except (ValueError, TypeError):
                continue

    out: dict[str, dict] = {}
    for stage, vals in stages.items():
        vals.sort()
        n = len(vals)
        if n == 0:
            continue
        out[stage] = {
            "p50": vals[n // 2],
            "p95": vals[int(n * 0.95)] if n > 1 else vals[0],
            "max": vals[-1],
            "n": n,
        }
    return out
