"""SolanaTrader analytics dashboard.

Mirrors production /analytics (arb-trader.yeda-ai.com) adapted for
Solana.  Sections:

  1. Summary cards
  2. Profit by Pair
  3. Profit by Venue Route (buy → sell)
  4. Expected vs Realized (included trades)
  5. Hourly PnL
  6. Rejection Reasons
  7. Scan History — Filter Breakdown
  8. Spread Distribution by Pair
  9. Near Misses (almost profitable)
"""

from __future__ import annotations

from dashboards._shared import (
    card, filter_bar, fmt_num, fmt_signed_sol, fmt_sol,
    page, resolve_filters, tx_link,
)
from persistence.repository import Repository


def render(repo: Repository, filters: dict | None = None) -> str:
    f = resolve_filters(filters)
    analytics = repo.get_pnl_analytics(since=f["since"] or None,
                                        until=f["until"] or None)
    pair_options = sorted({
        r["pair"] for r in repo.get_recent_opportunities(limit=500) if r.get("pair")
    })

    # Pair filter is applied in-Python across the analytics payloads
    # (repo query is pair-agnostic to keep SQL simple).
    if f["pair"]:
        analytics["per_pair"] = [r for r in analytics.get("per_pair", []) if r.get("pair") == f["pair"]]
        analytics["hourly_pnl"] = analytics.get("hourly_pnl", [])  # unchanged (global by hour)

    # --- summary ---
    per_pair = analytics.get("per_pair", []) or []
    total_trades = sum(r["trades"] for r in per_pair)
    total_wins = sum(r["wins"] for r in per_pair)
    total_reverts = sum(r["reverts"] for r in per_pair)
    total_profit = sum(r["net_profit"] for r in per_pair)
    total_fees = sum(r.get("fee_cost", 0) for r in per_pair)
    win_rate = (total_wins / total_trades * 100) if total_trades else 0

    summary = "".join([
        card("Total trades",  fmt_num(total_trades)),
        card("Wins",          fmt_num(total_wins), value_class="status-ok"),
        card("Reverts",       fmt_num(total_reverts),
             value_class="status-bad" if total_reverts else ""),
        card("Win rate",      f"{win_rate:.1f}%"),
        card("Realized PnL",  fmt_signed_sol(total_profit)),
        card("Fees paid",     fmt_sol(total_fees)),
    ])

    # --- profit by pair ---
    pair_rows = "".join(
        f"<tr><td>{r['pair']}</td>"
        f"<td class='num'>{r['trades']}</td>"
        f"<td class='num positive'>{r['wins']}</td>"
        f"<td class='num {'negative' if r['reverts'] else 'muted'}'>{r['reverts']}</td>"
        f"<td class='num'>{fmt_signed_sol(r['net_profit'])}</td>"
        f"<td class='num muted'>{fmt_sol(r.get('fee_cost', 0))}</td>"
        f"<td class='num'>{fmt_signed_sol(r['avg_profit'])}</td></tr>"
        for r in per_pair
    ) or "<tr><td colspan='7'><span class='muted'>no trades yet</span></td></tr>"
    pair_tbl = f"""
      <table>
        <thead><tr><th>Pair</th><th class='num'>Trades</th><th class='num'>Wins</th>
          <th class='num'>Reverts</th><th class='num'>Net</th>
          <th class='num'>Fees</th><th class='num'>Avg</th></tr></thead>
        <tbody>{pair_rows}</tbody>
      </table>
    """

    # --- profit by venue route ---
    per_venue = analytics.get("per_venue", []) or []
    venue_rows = "".join(
        f"<tr><td>{r['buy_venue']}</td><td>{r['sell_venue']}</td>"
        f"<td class='num'>{r['trades']}</td>"
        f"<td class='num positive'>{r['wins']}</td>"
        f"<td class='num'>"
        f"{(r['wins'] / r['trades'] * 100):.1f}%"
        f"</td>"
        f"<td class='num'>{fmt_signed_sol(r['net_profit'])}</td></tr>"
        for r in per_venue if r.get("trades", 0)
    ) or "<tr><td colspan='6'><span class='muted'>no executed trades yet</span></td></tr>"
    venue_tbl = f"""
      <table>
        <thead><tr><th>Buy venue</th><th>Sell venue</th>
          <th class='num'>Trades</th><th class='num'>Wins</th>
          <th class='num'>Win rate</th><th class='num'>Net</th></tr></thead>
        <tbody>{venue_rows}</tbody>
      </table>
    """

    # --- expected vs realized ---
    evr = repo.get_expected_vs_realized(limit=50, since=f["since"] or None)
    if f["pair"]:
        evr = [r for r in evr if r.get("pair") == f["pair"]]
    evr_rows = []
    for r in evr:
        expected = r.get("expected") or 0
        realized = r.get("realized") or 0
        capture = (realized / expected * 100) if expected else 0
        capture_cls = "positive" if capture >= 80 else "negative" if capture < 50 else "muted"
        evr_rows.append(
            f"<tr>"
            f"<td>{(r['detected_at'] or '')[11:19]}</td>"
            f"<td>{r['pair']}</td>"
            f"<td>{r['buy_venue']}</td>"
            f"<td>{r['sell_venue']}</td>"
            f"<td class='num'>{fmt_signed_sol(expected)}</td>"
            f"<td class='num'>{fmt_signed_sol(realized)}</td>"
            f"<td class='num {capture_cls}'>{capture:.0f}%</td>"
            f"<td class='num muted'>{fmt_sol(r.get('fee_paid', 0))}</td>"
            f"<td>{tx_link(r.get('signature'))}</td>"
            f"</tr>"
        )
    evr_tbl = f"""
      <table>
        <thead><tr><th>Time</th><th>Pair</th><th>Buy</th><th>Sell</th>
          <th class='num'>Expected</th><th class='num'>Realized</th>
          <th class='num'>Capture</th><th class='num'>Fee</th><th>TX</th></tr></thead>
        <tbody>{"".join(evr_rows) or "<tr><td colspan='9'><span class='muted'>no included trades yet</span></td></tr>"}</tbody>
      </table>
    """

    # --- hourly pnl ---
    hourly = analytics.get("hourly_pnl", [])
    hourly_rows = "".join(
        f"<tr><td>{r['hour']}:00 UTC</td>"
        f"<td class='num'>{r['trades']}</td>"
        f"<td class='num positive'>{r['wins']}</td>"
        f"<td class='num'>{fmt_signed_sol(r['net_profit'])}</td></tr>"
        for r in hourly[:48]
    ) or "<tr><td colspan='4'><span class='muted'>no data</span></td></tr>"
    hourly_tbl = f"""
      <table>
        <thead><tr><th>Hour</th><th class='num'>Trades</th><th class='num'>Wins</th>
          <th class='num'>Net</th></tr></thead>
        <tbody>{hourly_rows}</tbody>
      </table>
    """

    # --- rejection reasons ---
    rej = analytics.get("rejection_reasons", [])
    rej_rows = "".join(
        f"<tr><td>{r['reason_code']}</td>"
        f"<td class='num'>{r['cnt']}</td>"
        f"<td class='num'>{fmt_signed_sol(r['avg_expected_profit'])}</td></tr>"
        for r in rej
    ) or "<tr><td colspan='3'><span class='muted'>no rejections recorded</span></td></tr>"
    rej_tbl = f"""
      <table>
        <thead><tr><th>Reason</th><th class='num'>Count</th>
          <th class='num'>Avg expected profit</th></tr></thead>
        <tbody>{rej_rows}</tbody>
      </table>
    """

    # --- scan filter breakdown ---
    filter_bk = repo.get_scan_filter_breakdown(since=f["since"] or None,
                                                until=f["until"] or None)
    filter_rows = "".join(
        f"<tr><td>{r['filter_reason']}</td>"
        f"<td class='num'>{r['cnt']}</td>"
        f"<td class='num'>{(r['avg_spread'] or 0):.4f}%</td>"
        f"<td class='num'>{fmt_signed_sol(r['avg_net_profit'])}</td>"
        f"<td class='num'>{fmt_signed_sol(r['best_net_profit'])}</td></tr>"
        for r in filter_bk
    ) or "<tr><td colspan='5'><span class='muted'>no scan history yet</span></td></tr>"
    filter_tbl = f"""
      <table>
        <thead><tr><th>Reason</th><th class='num'>Count</th>
          <th class='num'>Avg spread</th>
          <th class='num'>Avg net</th><th class='num'>Best net</th></tr></thead>
        <tbody>{filter_rows}</tbody>
      </table>
    """

    # --- spread distribution ---
    spread_dist = repo.get_spread_distribution(since=f["since"] or None,
                                                 until=f["until"] or None)
    if f["pair"]:
        spread_dist = [r for r in spread_dist if r.get("pair") == f["pair"]]
    spread_rows = "".join(
        f"<tr><td>{r['pair']}</td>"
        f"<td>{r['buy_venue']} → {r['sell_venue']}</td>"
        f"<td class='num'>{r['samples']}</td>"
        f"<td class='num'>{(r['avg_spread'] or 0):.4f}%</td>"
        f"<td class='num positive'>{(r['max_spread'] or 0):.4f}%</td>"
        f"<td class='num muted'>{(r['min_spread'] or 0):.4f}%</td></tr>"
        for r in spread_dist
    ) or "<tr><td colspan='6'><span class='muted'>no spread data yet</span></td></tr>"
    spread_tbl = f"""
      <table>
        <thead><tr><th>Pair</th><th>Route</th>
          <th class='num'>Samples</th><th class='num'>Avg spread</th>
          <th class='num'>Max</th><th class='num'>Min</th></tr></thead>
        <tbody>{spread_rows}</tbody>
      </table>
    """

    # --- near misses ---
    misses = repo.get_near_misses(threshold_sol=0.002, limit=30,
                                   since=f["since"] or None)
    if f["pair"]:
        misses = [r for r in misses if r.get("pair") == f["pair"]]
    miss_rows = "".join(
        f"<tr><td>{(r['scan_ts'] or '')[11:19]}</td>"
        f"<td>{r['pair']}</td>"
        f"<td>{r['buy_venue']} → {r['sell_venue']}</td>"
        f"<td class='num'>{(r['spread'] or 0):.4f}%</td>"
        f"<td class='num'>{fmt_signed_sol(r['net_profit'])}</td>"
        f"<td class='num muted'>{fmt_sol(r.get('fee_cost', 0))}</td></tr>"
        for r in misses
    ) or "<tr><td colspan='6'><span class='muted'>no near misses — either we're winning or nothing's close</span></td></tr>"
    miss_tbl = f"""
      <table>
        <thead><tr><th>Time</th><th>Pair</th><th>Route</th>
          <th class='num'>Spread</th><th class='num'>Net</th>
          <th class='num'>Fee</th></tr></thead>
        <tbody>{miss_rows}</tbody>
      </table>
    """

    body = f"""
      {filter_bar(f, "analytics", pair_options=pair_options, show_status=False)}

      <h2>Summary</h2>
      <div class='grid tight'>{summary}</div>

      <h2>Profit by Pair</h2>
      {pair_tbl}

      <h2>Profit by Venue Route</h2>
      {venue_tbl}

      <h2>Expected vs Realized (Included Trades)</h2>
      {evr_tbl}

      <h2>Hourly PnL</h2>
      {hourly_tbl}

      <h2>Rejection Reasons</h2>
      {rej_tbl}

      <h2>Scan History — Filter Breakdown</h2>
      {filter_tbl}

      <h2>Spread Distribution by Route</h2>
      {spread_tbl}

      <h2>Near Misses (almost profitable)</h2>
      {miss_tbl}
    """
    return page("PnL Analytics", body, active="analytics", refresh_seconds=30)
