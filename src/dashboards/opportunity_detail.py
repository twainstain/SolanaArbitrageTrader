"""Per-opportunity deep-dive view.

Shows the full pipeline lifecycle for one opportunity:
  detect → price → risk → simulate → submit → verify

Useful for investigating why a specific opp was rejected / why realized
PnL differed from expected.
"""

from __future__ import annotations

import json

from dashboards._shared import fmt_sol, page, tag, tx_link
from persistence.repository import Repository


def render(repo: Repository, opp_id: str) -> str:
    opp = repo.get_opportunity(opp_id)
    if opp is None:
        return page("Not found", f"<p>No opportunity <code>{opp_id}</code>.</p>", refresh_seconds=0)

    pricing = repo.get_pricing(opp_id) or {}
    risk = repo.get_risk_decision(opp_id) or {}
    sim = repo.get_simulation(opp_id) or {}
    exec_row = repo.get_latest_execution_attempt(opp_id) or {}

    # --- header block ---
    kind = "ok" if opp["status"] in ("confirmed", "approved", "dry_run", "simulation_approved") else (
        "err" if opp["status"] in ("reverted", "dropped") else "muted"
    )

    header = f"""
      <h2>Opportunity {opp_id}</h2>
      <p>
        <strong>{opp['pair']}</strong>:
        buy <code>{opp['buy_venue']}</code> → sell <code>{opp['sell_venue']}</code>
        &nbsp; spread <strong>{opp['spread_bps']}</strong>%
        &nbsp; {tag(opp['status'], kind)}
      </p>
      <p>
        Detected at {opp['detected_at']} · Last updated {opp['updated_at']}
      </p>
    """

    # --- pricing ---
    pricing_tbl = _kv_table(pricing, [
        "input_amount", "estimated_output",
        "venue_fee_cost", "slippage_cost", "fee_estimate_base",
        "expected_net_profit", "buy_liquidity_usd", "sell_liquidity_usd",
    ])

    # --- risk ---
    try:
        thresholds = json.loads(risk.get("threshold_snapshot") or "{}")
    except Exception:
        thresholds = {}
    # Legacy rows may have stored a JSON-encoded string, which round-trips
    # to a str here instead of a dict. Coerce so .items() below is safe.
    if not isinstance(thresholds, dict):
        thresholds = {}
    thresholds_rows = "".join(
        f"<tr><td>{k}</td><td>{_fmt(v)}</td></tr>" for k, v in thresholds.items()
    )
    risk_tbl = f"""
      <table>
        <tbody>
          <tr><td>approved</td><td>{_fmt(risk.get('approved'))}</td></tr>
          <tr><td>reason_code</td><td>{_fmt(risk.get('reason_code'))}</td></tr>
        </tbody>
      </table>
      <h3 style='color:#8b949e;font-size:11px;text-transform:uppercase;margin-top:18px;'>Threshold snapshot</h3>
      <table><tbody>{thresholds_rows or "<tr><td colspan='2'><em>—</em></td></tr>"}</tbody></table>
    """

    # --- simulation ---
    sim_tbl = _kv_table(sim, [
        "success", "revert_reason", "expected_output", "expected_net_profit", "created_at",
    ])

    # --- execution ---
    sig = exec_row.get("signature") or ""
    exec_content = ""
    if exec_row:
        exec_content = f"""
          <table>
            <tbody>
              <tr><td>submission_kind</td><td>{_fmt(exec_row.get('submission_kind'))}</td></tr>
              <tr><td>signature</td><td>{tx_link(sig)}</td></tr>
              <tr><td>status</td><td>{_fmt(exec_row.get('status'))}</td></tr>
              <tr><td>submitted_at</td><td>{_fmt(exec_row.get('submitted_at'))}</td></tr>
              <tr><td>metadata</td><td><pre style='margin:0;white-space:pre-wrap;'>{_fmt(exec_row.get('metadata'))}</pre></td></tr>
            </tbody>
          </table>
        """

    body = f"""
      {header}

      <h2>Pricing</h2>
      {pricing_tbl}

      <h2>Risk decision</h2>
      {risk_tbl}

      <h2>Simulation</h2>
      {sim_tbl or "<p><em>No simulation record.</em></p>"}

      <h2>Execution attempt</h2>
      {exec_content or "<p><em>No execution (scanner-only or rejected upstream).</em></p>"}
    """
    return page(f"Opp {opp_id[-8:]}", body, refresh_seconds=0)


# ---------------------------------------------------------------------------

def _kv_table(d: dict, keys: list[str]) -> str:
    if not d:
        return "<p><em>— no record —</em></p>"
    rows = "".join(
        f"<tr><td>{k}</td><td>{_fmt(d.get(k))}</td></tr>" for k in keys if k in d
    )
    return f"<table><tbody>{rows or '<tr><td colspan=2><em>—</em></td></tr>'}</tbody></table>"


def _fmt(v) -> str:
    if v is None:
        return "—"
    return str(v)
