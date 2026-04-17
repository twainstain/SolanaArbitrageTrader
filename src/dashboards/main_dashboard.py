"""SolanaTrader main dashboard.

Sections (mirrors the production /dashboard at arb-trader.yeda-ai.com,
Solana-adapted):

  1. System status cards — uptime, execution mode, wallet, kill switch
  2. Headline counters — scans, opportunities, wins, reverts, realized PnL
  3. Hourly Win/Loss (24h)
  4. Per-pair 24h funnel
  5. Recent opportunities with Solscan links
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboards._shared import (
    card, filter_bar, fmt_num, fmt_signed_sol, fmt_sol,
    page, resolve_filters, tag, wallet_link,
)
from observability.wallet import get_wallet_balances
from persistence.repository import Repository

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_KILL_SWITCH = _PROJECT_ROOT / "data" / ".execution_kill_switch"


def _status_tag(status: str) -> str:
    kind = {
        "confirmed": "ok", "dry_run": "ok", "approved": "ok",
        "simulation_approved": "ok",
        "rejected": "muted", "simulation_failed": "warn",
        "reverted": "err", "dropped": "err", "not_included": "err",
        "detected": "accent", "priced": "accent", "submitted": "warn",
    }.get(status, "muted")
    return tag(status, kind)


def render(repo: Repository, metrics=None, filters: dict | None = None) -> str:
    f = resolve_filters(filters)
    funnel = repo.get_opportunity_funnel()
    pnl = repo.get_pnl_summary()

    # Apply pair/status filters to the recent-opps list.  The repo call is
    # cheap; filtering in Python is fine for the dashboard's 50-row limit.
    opps = repo.get_recent_opportunities(limit=100)
    if f["pair"]:
        opps = [o for o in opps if o["pair"] == f["pair"]]
    if f["status"]:
        opps = [o for o in opps if o["status"] == f["status"]]
    if f["since"]:
        opps = [o for o in opps if (o.get("detected_at") or "") >= f["since"]]
    if f["until"]:
        opps = [o for o in opps if (o.get("detected_at") or "") <= f["until"]]
    opps = opps[:20]

    metrics_snap = metrics.snapshot() if metrics else {}

    # Pair dropdown options: discovered from the recent_opps set so it
    # reflects what's actually being scanned.
    pair_options = sorted({o["pair"] for o in repo.get_recent_opportunities(limit=200) if o.get("pair")})

    # --- system status cards -------------------------------------------
    execution_on = os.environ.get("SOLANA_EXECUTION_ENABLED", "").lower() in ("true", "1", "yes")
    kill = _KILL_SWITCH.exists()
    wb = get_wallet_balances()
    sol_bal = wb.get("balances", {}).get("SOL")
    pubkey = wb.get("address", "")

    if kill:
        exec_state, exec_cls = tag("KILL-SWITCH ACTIVE", "err"), "status-bad"
    elif execution_on and pubkey:
        exec_state, exec_cls = tag("LIVE READY", "ok"), "status-ok"
    elif execution_on:
        exec_state, exec_cls = tag("NO WALLET", "warn"), "status-warn"
    else:
        exec_state, exec_cls = tag("PAPER", "muted"), ""

    uptime = metrics_snap.get("uptime_seconds", 0)
    uptime_str = f"{int(uptime // 3600)}h {int((uptime % 3600) // 60)}m" if uptime > 60 else f"{int(uptime)}s"

    status_cards = "".join([
        card("Uptime", uptime_str),
        card("Execution", exec_state, value_class=exec_cls),
        card("Wallet SOL",
             fmt_sol(sol_bal, 4) if sol_bal is not None else "—",
             sub=wallet_link(pubkey) if pubkey else "no wallet configured",
             value_class="status-bad" if (sol_bal is not None and float(sol_bal) < 0.005) else ""),
        card("Opps / min",
             f"{metrics_snap.get('opportunities_per_minute', 0):.2f}",
             sub=f"{metrics_snap.get('opportunities_detected', 0)} detected total"),
    ])

    # --- headline counters ---------------------------------------------
    approved = funnel.get("approved", 0) + funnel.get("dry_run", 0) + funnel.get("simulation_approved", 0)
    headline = "".join([
        card("Total opps", fmt_num(sum(funnel.values()))),
        card("Approved",   fmt_num(approved), value_class="status-ok"),
        card("Rejected",   fmt_num(funnel.get("rejected", 0)), value_class="status-warn"),
        card("Confirmed",  fmt_num(pnl.get("successful", 0)), value_class="status-ok"),
        card("Reverts",    fmt_num(pnl.get("reverted", 0)),
             value_class="status-bad" if pnl.get("reverted", 0) else ""),
        card("Realized PnL", fmt_signed_sol(pnl.get("total_profit", 0))),
    ])

    # --- hourly win/loss -------------------------------------------
    # Honour the filter's since/until when set, otherwise default to 24h.
    since = f["since"] or (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    analytics = repo.get_pnl_analytics(since=since, until=f["until"] or None)
    hourly = analytics.get("hourly_pnl", [])
    hourly_rows = "".join(
        f"<tr><td>{r['hour']}:00 UTC</td>"
        f"<td class='num'>{r['trades']}</td>"
        f"<td class='num positive'>{r['wins']}</td>"
        f"<td class='num'>{r['trades'] - r['wins']}</td>"
        f"<td class='num'>{fmt_signed_sol(r['net_profit'])}</td></tr>"
        for r in hourly[:24]
    ) or "<tr><td colspan='5'><span class='muted'>no trades in the last 24h</span></td></tr>"
    hourly_tbl = f"""
      <table>
        <thead><tr><th>Hour</th><th class='num'>Trades</th><th class='num'>Wins</th>
          <th class='num'>Losses</th><th class='num'>Net</th></tr></thead>
        <tbody>{hourly_rows}</tbody>
      </table>
    """

    # --- windowed activity (5m / 15m / 1h / 4h / 24h / 3d / 1w) --------
    # Server-rendered so the page works without JS. One row per window
    # key; columns mirror what the EVM dashboard showed: total / approved
    # / rejected / executed / best-expected / realized.
    from observability.time_windows import get_windowed_stats
    windows_tbl = _render_windows_section(repo)

    # --- per-pair 24h funnel -------------------------------------------
    from observability.time_windows import get_pair_summary
    pair_rows_raw = get_pair_summary(repo.conn, window_key="24h")
    pair_rows = "".join(
        f"<tr><td>{p['pair']}</td>"
        f"<td class='num'>{p['total']}</td>"
        f"<td class='num positive'>{p['funnel'].get('approved', 0) + p['funnel'].get('dry_run', 0) + p['funnel'].get('simulation_approved', 0)}</td>"
        f"<td class='num'>{p['funnel'].get('rejected', 0)}</td>"
        f"<td class='num'>{p['funnel'].get('confirmed', 0) + p['funnel'].get('reverted', 0) + p['funnel'].get('dropped', 0)}</td></tr>"
        for p in pair_rows_raw
    ) or "<tr><td colspan='5'><span class='muted'>no pairs scanned in the last 24h</span></td></tr>"
    pair_tbl = f"""
      <table>
        <thead><tr><th>Pair</th><th class='num'>Total</th><th class='num'>Approved</th>
          <th class='num'>Rejected</th><th class='num'>Executed</th></tr></thead>
        <tbody>{pair_rows}</tbody>
      </table>
    """

    # --- recent opportunities ------------------------------------------
    opp_rows = []
    for o in opps:
        detected = (o["detected_at"] or "")[11:19]
        opp_rows.append(
            f"<tr>"
            f"<td>{detected}</td>"
            f"<td><a href='/opportunity/{o['opportunity_id']}'>{o['opportunity_id'][-8:]}</a></td>"
            f"<td>{o['pair']}</td>"
            f"<td>{o['buy_venue']}</td>"
            f"<td>{o['sell_venue']}</td>"
            f"<td class='num'>{o['spread_bps']}%</td>"
            f"<td>{_status_tag(o['status'])}</td>"
            f"</tr>"
        )
    opp_tbl = f"""
      <table>
        <thead><tr><th>Time</th><th>ID</th><th>Pair</th>
          <th>Buy</th><th>Sell</th><th class='num'>Spread</th><th>Status</th></tr></thead>
        <tbody>{"".join(opp_rows) or "<tr><td colspan='7'><span class='muted'>no opportunities yet</span></td></tr>"}</tbody>
      </table>
    """

    body = f"""
      {filter_bar(f, "dashboard", pair_options=pair_options, show_status=True)}

      <h2>System Status</h2>
      <div class='grid'>{status_cards}</div>

      <h2>Headline (all time)</h2>
      <div class='grid tight'>{headline}</div>

      <h2>Windowed Activity</h2>
      {windows_tbl}

      <h2>Hourly Win/Loss (24h)</h2>
      {hourly_tbl}

      <h2>Per Pair (24h)</h2>
      {pair_tbl}

      <h2>Recent Opportunities</h2>
      {opp_tbl}
    """
    return page("Dashboard", body, active="dashboard", refresh_seconds=10)


# ---------------------------------------------------------------------------
# Windowed-activity table (Phase 2d / ops request).
#
# Shows a single row per window key with consistent columns so operators
# can eyeball 5m vs 1h vs 24h trends without clicking tabs. Pulls from
# observability.time_windows.get_windowed_stats, which already powers the
# /windows and /windows/{key} API endpoints.
# ---------------------------------------------------------------------------


# Ordered subset of WINDOWS to render in the table. Omits 8h and 1m to
# keep the row count tight; users can still hit /windows/8h via the API.
_RENDERED_WINDOWS: list[str] = ["5m", "15m", "1h", "4h", "24h", "3d", "1w"]


def _render_windows_section(repo: Repository) -> str:
    from observability.time_windows import get_windowed_stats

    rows_html = []
    for key in _RENDERED_WINDOWS:
        data = get_windowed_stats(repo.conn, key)
        opps = (data.get("opportunities") or {})
        trades = (data.get("trades") or {})
        profit = (data.get("profit") or {})
        total = int(opps.get("total") or 0)
        funnel = opps.get("funnel") or {}
        approved = int(funnel.get("approved", 0)) + int(funnel.get("dry_run", 0)) + int(funnel.get("simulation_approved", 0))
        rejected = int(funnel.get("rejected", 0))
        executed = int(trades.get("total_trades") or 0)
        realized = float(trades.get("total_profit") or 0.0)
        best_exp = float(profit.get("max_expected_profit") or 0.0)

        rows_html.append(
            f"<tr>"
            f"<td><code>{key}</code></td>"
            f"<td class='num'>{total}</td>"
            f"<td class='num positive'>{approved}</td>"
            f"<td class='num'>{rejected}</td>"
            f"<td class='num'>{executed}</td>"
            f"<td class='num'>{best_exp:.6f}</td>"
            f"<td class='num {'positive' if realized > 0 else ('negative' if realized < 0 else '')}'>"
            f"{fmt_signed_sol(realized)}</td>"
            f"</tr>"
        )

    return f"""
      <table>
        <thead><tr>
          <th>Window</th>
          <th class='num'>Opportunities</th>
          <th class='num'>Approved</th>
          <th class='num'>Rejected</th>
          <th class='num'>Executed</th>
          <th class='num'>Best Exp. SOL</th>
          <th class='num'>Realized SOL</th>
        </tr></thead>
        <tbody>{"".join(rows_html)}</tbody>
      </table>
    """
