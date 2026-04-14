"""Time-windowed aggregations for the dashboard.

Provides aggregated opportunity/trade metrics over:
  15min, 1h, 4h, 8h, 24h, 3d, 1w, 1m

Per chain and globally.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from persistence.db import DbConnection

WINDOWS = {
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "8h": timedelta(hours=8),
    "24h": timedelta(hours=24),
    "3d": timedelta(days=3),
    "1w": timedelta(weeks=1),
    "1m": timedelta(days=30),
}


def _since(window: timedelta) -> str:
    return (datetime.now(timezone.utc) - window).isoformat()


def get_windowed_stats(conn: DbConnection, window_key: str, chain: str | None = None) -> dict:
    """Get opportunity + trade stats for a given time window.

    Args:
        conn: Database connection.
        window_key: One of "15m", "1h", "4h", "8h", "24h", "3d", "1w", "1m".
        chain: Optional chain filter. None = all chains.
    """
    td = WINDOWS.get(window_key)
    if td is None:
        return {"error": f"Unknown window: {window_key}"}

    since = _since(td)

    # Opportunity counts by status.
    if chain:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM opportunities "
            "WHERE detected_at >= ? AND chain = ? GROUP BY status",
            (since, chain),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM opportunities "
            "WHERE detected_at >= ? GROUP BY status",
            (since,),
        ).fetchall()

    funnel = {r["status"]: r["cnt"] for r in rows}
    total_opps = sum(funnel.values())

    # Trade results in window — join through execution_attempts → opportunities.
    if chain:
        trade_row = conn.execute(
            "SELECT "
            "  COUNT(*) as total_trades, "
            "  SUM(CASE WHEN tr.included = 1 AND tr.reverted = 0 THEN 1 ELSE 0 END) as successful, "
            "  SUM(CASE WHEN tr.reverted = 1 THEN 1 ELSE 0 END) as reverted, "
            "  COALESCE(SUM(CAST(tr.actual_net_profit AS REAL)), 0) as total_profit, "
            "  COALESCE(SUM(tr.gas_used), 0) as total_gas "
            "FROM trade_results tr "
            "JOIN execution_attempts ea ON tr.execution_id = ea.execution_id "
            "JOIN opportunities o ON ea.opportunity_id = o.opportunity_id "
            "WHERE o.detected_at >= ? AND o.chain = ?",
            (since, chain),
        ).fetchone()
    else:
        trade_row = conn.execute(
            "SELECT "
            "  COUNT(*) as total_trades, "
            "  SUM(CASE WHEN tr.included = 1 AND tr.reverted = 0 THEN 1 ELSE 0 END) as successful, "
            "  SUM(CASE WHEN tr.reverted = 1 THEN 1 ELSE 0 END) as reverted, "
            "  COALESCE(SUM(CAST(tr.actual_net_profit AS REAL)), 0) as total_profit, "
            "  COALESCE(SUM(tr.gas_used), 0) as total_gas "
            "FROM trade_results tr "
            "JOIN execution_attempts ea ON tr.execution_id = ea.execution_id "
            "JOIN opportunities o ON ea.opportunity_id = o.opportunity_id "
            "WHERE o.detected_at >= ?",
            (since,),
        ).fetchone()

    trades = dict(trade_row) if trade_row else {}

    # Expected profit from pricing (available even in simulation mode).
    if chain:
        profit_row = conn.execute(
            "SELECT "
            "  COUNT(*) as priced_count, "
            "  COALESCE(SUM(CAST(pr.expected_net_profit AS REAL)), 0) as total_expected_profit, "
            "  COALESCE(AVG(CAST(pr.expected_net_profit AS REAL)), 0) as avg_expected_profit, "
            "  COALESCE(MAX(CAST(pr.expected_net_profit AS REAL)), 0) as max_expected_profit, "
            "  COALESCE(MIN(CAST(pr.expected_net_profit AS REAL)), 0) as min_expected_profit "
            "FROM pricing_results pr "
            "JOIN opportunities o ON pr.opportunity_id = o.opportunity_id "
            "WHERE o.detected_at >= ? AND o.chain = ? "
            "AND CAST(pr.expected_net_profit AS REAL) > 0",
            (since, chain),
        ).fetchone()
    else:
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
        "chain": chain or "all",
        "since": since,
        "opportunities": {
            "total": total_opps,
            "funnel": funnel,
        },
        "trades": trades,
        "profit": profit,
    }


def get_all_windows(conn: DbConnection, chain: str | None = None) -> dict:
    """Get stats for all time windows at once."""
    return {
        key: get_windowed_stats(conn, key, chain)
        for key in WINDOWS
    }


def get_chain_summary(conn: DbConnection, window_key: str = "24h") -> list[dict]:
    """Get stats per chain for a given window."""
    td = WINDOWS.get(window_key, timedelta(hours=24))
    since = _since(td)

    rows = conn.execute(
        "SELECT chain, status, COUNT(*) as cnt FROM opportunities "
        "WHERE detected_at >= ? GROUP BY chain, status ORDER BY chain",
        (since,),
    ).fetchall()

    # Group by chain.
    chains: dict[str, dict] = {}
    for r in rows:
        ch = r["chain"] or "unknown"
        if ch not in chains:
            chains[ch] = {"chain": ch, "funnel": {}, "total": 0}
        chains[ch]["funnel"][r["status"]] = r["cnt"]
        chains[ch]["total"] += r["cnt"]

    return sorted(chains.values(), key=lambda x: x["total"], reverse=True)
