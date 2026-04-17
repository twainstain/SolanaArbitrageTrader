"""Time-windowed aggregations for the Solana dashboard and reporting.

No per-chain partitioning — Solana is a single chain.  Uses Solana-native
trade_results columns (``fee_paid_base`` / ``fee_paid_lamports``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from persistence.db import DbConnection

WINDOWS = {
    "5m":  timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h":  timedelta(hours=1),
    "4h":  timedelta(hours=4),
    "8h":  timedelta(hours=8),
    "24h": timedelta(hours=24),
    "3d":  timedelta(days=3),
    "1w":  timedelta(weeks=1),
    "1m":  timedelta(days=30),
}


def _since(window: timedelta) -> str:
    return (datetime.now(timezone.utc) - window).isoformat()


def get_windowed_stats(conn: DbConnection, window_key: str) -> dict:
    td = WINDOWS.get(window_key)
    if td is None:
        return {"error": f"Unknown window: {window_key}"}
    since = _since(td)

    # Opportunity funnel by status
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM opportunities "
        "WHERE detected_at >= ? GROUP BY status",
        (since,),
    ).fetchall()
    funnel = {r["status"]: r["cnt"] for r in rows}
    total_opps = sum(funnel.values())

    # Trade results
    trade_row = conn.execute(
        "SELECT "
        "  COUNT(*) as total_trades, "
        "  COALESCE(SUM(CASE WHEN tr.included = 1 AND tr.reverted = 0 THEN 1 ELSE 0 END), 0) as successful, "
        "  COALESCE(SUM(CASE WHEN tr.reverted = 1 THEN 1 ELSE 0 END), 0) as reverted, "
        "  COALESCE(SUM(CASE WHEN tr.dropped = 1 THEN 1 ELSE 0 END), 0) as dropped, "
        "  COALESCE(SUM(CAST(tr.realized_profit_quote AS REAL)), 0) as total_realized_profit_quote, "
        "  COALESCE(SUM(CAST(tr.fee_paid_base AS REAL)), 0) as total_fee_paid_base, "
        "  COALESCE(SUM(CAST(tr.actual_net_profit AS REAL)), 0) as total_profit, "
        "  COALESCE(SUM(tr.fee_paid_lamports), 0) as total_fee_paid_lamports "
        "FROM trade_results tr "
        "JOIN execution_attempts ea ON tr.execution_id = ea.execution_id "
        "JOIN opportunities o ON ea.opportunity_id = o.opportunity_id "
        "WHERE o.detected_at >= ?",
        (since,),
    ).fetchone()
    trades = dict(trade_row) if trade_row else {}

    # Expected profit (scanner-mode reporting)
    profit_row = conn.execute(
        "SELECT "
        "  COUNT(*) as priced_count, "
        "  COALESCE(SUM(CAST(pr.expected_net_profit AS REAL)), 0) as total_expected_profit, "
        "  COALESCE(AVG(CAST(pr.expected_net_profit AS REAL)), 0) as avg_expected_profit, "
        "  COALESCE(MAX(CAST(pr.expected_net_profit AS REAL)), 0) as max_expected_profit, "
        "  COALESCE(MIN(CAST(pr.expected_net_profit AS REAL)), 0) as min_expected_profit "
        "FROM pricing_results pr "
        "JOIN opportunities o ON pr.opportunity_id = o.opportunity_id "
        "WHERE o.detected_at >= ? "
        "AND CAST(pr.expected_net_profit AS REAL) > 0",
        (since,),
    ).fetchone()
    profit = dict(profit_row) if profit_row else {}

    return {
        "window": window_key,
        "since": since,
        "opportunities": {"total": total_opps, "funnel": funnel},
        "trades": trades,
        "profit": profit,
    }


def get_all_windows(conn: DbConnection) -> dict:
    return {key: get_windowed_stats(conn, key) for key in WINDOWS}


def get_pair_summary(conn: DbConnection, window_key: str = "24h") -> list[dict]:
    """Per-pair funnel within a window."""
    td = WINDOWS.get(window_key, timedelta(hours=24))
    since = _since(td)
    rows = conn.execute(
        "SELECT pair, status, COUNT(*) as cnt FROM opportunities "
        "WHERE detected_at >= ? GROUP BY pair, status ORDER BY pair",
        (since,),
    ).fetchall()
    pairs: dict[str, dict] = {}
    for r in rows:
        p = r["pair"] or "unknown"
        if p not in pairs:
            pairs[p] = {"pair": p, "funnel": {}, "total": 0}
        pairs[p]["funnel"][r["status"]] = r["cnt"]
        pairs[p]["total"] += r["cnt"]
    return sorted(pairs.values(), key=lambda x: x["total"], reverse=True)
