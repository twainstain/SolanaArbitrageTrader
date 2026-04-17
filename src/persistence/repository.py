"""Repository — CRUD operations for the SolanaTrader candidate lifecycle.

Each method maps to one stage:
  detected → priced → risk_approved/rejected → simulated → submitted → confirmed/reverted/dropped

Schema is Solana-native — ``venue`` not ``dex``, ``signature`` not
``tx_hash``, ``confirmation_slot`` not ``block_number``,
``fee_paid_lamports`` not ``gas_used``.  See ``persistence.db`` for the
full schema definition.
"""

from __future__ import annotations

import json
import time as _time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from persistence.db import DbConnection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: Any) -> dict | None:
    if row is None:
        return None
    return dict(row)


class Repository:
    """Persistence operations for the arbitrage candidate lifecycle."""

    def __init__(self, conn: DbConnection) -> None:
        self.conn = conn
        self._count_cache: tuple[float, str, str | None, int] | None = None

    # ------------------------------------------------------------------
    # Opportunities
    # ------------------------------------------------------------------

    def create_opportunity(
        self,
        pair: str,
        buy_venue: str,
        sell_venue: str,
        spread_bps: Decimal,
    ) -> str:
        opp_id = f"opp_{uuid.uuid4().hex[:12]}"
        now = _now()
        self.conn.execute(
            "INSERT INTO opportunities "
            "(opportunity_id, pair, buy_venue, sell_venue, spread_bps, status, detected_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'detected', ?, ?)",
            (opp_id, pair, buy_venue, sell_venue, str(spread_bps), now, now),
        )
        self.conn.commit()
        return opp_id

    def update_opportunity_status(self, opp_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE opportunities SET status = ?, updated_at = ? WHERE opportunity_id = ?",
            (status, _now(), opp_id),
        )
        self.conn.commit()

    def get_opportunity(self, opp_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM opportunities WHERE opportunity_id = ?", (opp_id,)
        ).fetchone()
        return _row_to_dict(row)

    def get_recent_opportunities(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM opportunities ORDER BY detected_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def count_opportunities_since(self, since_iso: str, status: str | None = None) -> int:
        """Count opportunities since a timestamp, optionally filtered by status.

        Cached for 30 seconds — the hourly trade count changes slowly.
        """
        now = _time.monotonic()
        since_key = since_iso[:16]   # minute-precision cache key
        if self._count_cache is not None:
            ts, cached_since, cached_status, cached_count = self._count_cache
            if (now - ts) < 30.0 and cached_since == since_key and cached_status == status:
                return cached_count

        if status:
            row = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM opportunities WHERE detected_at >= ? AND status = ?",
                (since_iso, status),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM opportunities WHERE detected_at >= ?",
                (since_iso,),
            ).fetchone()
        count = row["cnt"] if row else 0
        self._count_cache = (now, since_key, status, count)
        return count

    # ------------------------------------------------------------------
    # Pricing Results
    # ------------------------------------------------------------------

    def save_pricing(
        self,
        opp_id: str,
        input_amount: Decimal,
        estimated_output: Decimal,
        fee_cost: Decimal,
        slippage_cost: Decimal,
        fee_estimate_base: Decimal,
        expected_net_profit: Decimal,
        buy_liquidity_usd: Decimal = Decimal("0"),
        sell_liquidity_usd: Decimal = Decimal("0"),
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO pricing_results "
            "(opportunity_id, input_amount, estimated_output, venue_fee_cost, slippage_cost, "
            "fee_estimate_base, expected_net_profit, buy_liquidity_usd, sell_liquidity_usd, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (opp_id, str(input_amount), str(estimated_output), str(fee_cost),
             str(slippage_cost), str(fee_estimate_base), str(expected_net_profit),
             str(buy_liquidity_usd), str(sell_liquidity_usd), _now()),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_pricing(self, opp_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM pricing_results WHERE opportunity_id = ? ORDER BY created_at DESC LIMIT 1",
            (opp_id,),
        ).fetchone()
        return _row_to_dict(row)

    # ------------------------------------------------------------------
    # Risk Decisions
    # ------------------------------------------------------------------

    def save_risk_decision(
        self,
        opp_id: str,
        approved: bool,
        reason_code: str,
        threshold_snapshot: dict | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO risk_decisions "
            "(opportunity_id, approved, reason_code, threshold_snapshot, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (opp_id, int(approved), reason_code,
             json.dumps(threshold_snapshot or {}, default=str), _now()),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_risk_decision(self, opp_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM risk_decisions WHERE opportunity_id = ? ORDER BY created_at DESC LIMIT 1",
            (opp_id,),
        ).fetchone()
        return _row_to_dict(row)

    # ------------------------------------------------------------------
    # Simulations
    # ------------------------------------------------------------------

    def save_simulation(
        self,
        opp_id: str,
        success: bool,
        revert_reason: str = "",
        expected_output: Decimal = Decimal("0"),
        expected_net_profit: Decimal = Decimal("0"),
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO simulations "
            "(opportunity_id, success, revert_reason, expected_output, expected_net_profit, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (opp_id, int(success), revert_reason,
             str(expected_output), str(expected_net_profit), _now()),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_simulation(self, opp_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM simulations WHERE opportunity_id = ? ORDER BY created_at DESC LIMIT 1",
            (opp_id,),
        ).fetchone()
        return _row_to_dict(row)

    # ------------------------------------------------------------------
    # Execution Attempts
    # ------------------------------------------------------------------

    def save_execution_attempt(
        self,
        opp_id: str,
        submission_kind: str = "rpc",
        signature: str = "",
        metadata: dict | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO execution_attempts "
            "(opportunity_id, submission_kind, signature, submission_ref, metadata, status, submitted_at) "
            "VALUES (?, ?, ?, ?, ?, 'submitted', ?)",
            (opp_id, submission_kind, signature, signature,
             json.dumps(metadata or {}, default=str), _now()),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def update_execution_status(self, execution_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE execution_attempts SET status = ? WHERE execution_id = ?",
            (status, execution_id),
        )
        self.conn.commit()

    def get_latest_execution_attempt(self, opp_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM execution_attempts WHERE opportunity_id = ? "
            "ORDER BY execution_id DESC LIMIT 1",
            (opp_id,),
        ).fetchone()
        return _row_to_dict(row)

    # ------------------------------------------------------------------
    # Trade Results
    # ------------------------------------------------------------------

    def save_trade_result(
        self,
        execution_id: int,
        included: bool,
        reverted: bool = False,
        dropped: bool = False,
        fee_paid_lamports: int = 0,
        realized_profit_quote: Decimal = Decimal("0"),
        fee_paid_base: Decimal = Decimal("0"),
        profit_currency: str = "",
        actual_net_profit: Decimal = Decimal("0"),
        confirmation_slot: int = 0,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO trade_results "
            "(execution_id, included, reverted, dropped, fee_paid_lamports, "
            "realized_profit_quote, fee_paid_base, profit_currency, "
            "actual_net_profit, confirmation_slot, finalized_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (execution_id, int(included), int(reverted), int(dropped),
             fee_paid_lamports, str(realized_profit_quote), str(fee_paid_base),
             profit_currency, str(actual_net_profit), confirmation_slot, _now()),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_trade_result(self, execution_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM trade_results WHERE execution_id = ? LIMIT 1",
            (execution_id,),
        ).fetchone()
        return _row_to_dict(row)

    # ------------------------------------------------------------------
    # System Checkpoints
    # ------------------------------------------------------------------

    def set_checkpoint(self, checkpoint_type: str, value: str) -> None:
        existing = self.conn.execute(
            "SELECT checkpoint_id FROM system_checkpoints WHERE checkpoint_type = ?",
            (checkpoint_type,),
        ).fetchone()
        now = _now()
        if existing:
            self.conn.execute(
                "UPDATE system_checkpoints SET value = ?, updated_at = ? WHERE checkpoint_type = ?",
                (value, now, checkpoint_type),
            )
        else:
            self.conn.execute(
                "INSERT INTO system_checkpoints (checkpoint_type, value, updated_at) VALUES (?, ?, ?)",
                (checkpoint_type, value, now),
            )
        self.conn.commit()

    def get_checkpoint(self, checkpoint_type: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM system_checkpoints WHERE checkpoint_type = ?",
            (checkpoint_type,),
        ).fetchone()
        return row["value"] if row else None

    # ------------------------------------------------------------------
    # Scan History
    # ------------------------------------------------------------------

    def save_scan_history(self, rows: list[dict]) -> int:
        """Batch-insert scan evaluation records."""
        if not rows:
            return 0
        now = _now()
        for r in rows:
            self.conn.execute(
                "INSERT INTO scan_history "
                "(scan_ts, pair, buy_venue, sell_venue, buy_price, sell_price, "
                "spread_bps, gross_profit, net_profit, fee_cost, venue_fee_cost, "
                "slippage_cost, filter_reason, passed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now, r.get("pair", ""),
                 r.get("buy_venue", ""), r.get("sell_venue", ""),
                 str(r.get("buy_price", "0")), str(r.get("sell_price", "0")),
                 str(r.get("spread_bps", "0")),
                 str(r.get("gross_profit", "0")), str(r.get("net_profit", "0")),
                 str(r.get("fee_cost", "0")), str(r.get("venue_fee_cost", "0")),
                 str(r.get("slippage_cost", "0")),
                 r.get("filter_reason", ""), int(r.get("passed", False))),
            )
        self.conn.commit()
        return len(rows)

    def get_scan_history(
        self,
        pair: str | None = None,
        reason: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        conditions, params = [], []
        if pair:
            conditions.append("pair = ?")
            params.append(pair)
        if reason:
            conditions.append("filter_reason = ?")
            params.append(reason)
        if since:
            conditions.append("scan_ts >= ?")
            params.append(since)
        if until:
            conditions.append("scan_ts <= ?")
            params.append(until)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = self.conn.execute(
            f"SELECT * FROM scan_history{where} ORDER BY scan_ts DESC LIMIT ?",
            tuple(params) + (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Aggregations (Solana reporting)
    # ------------------------------------------------------------------

    def get_opportunity_funnel(self) -> dict:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) as count FROM opportunities GROUP BY status"
        ).fetchall()
        return {r["status"]: r["count"] for r in rows}

    def get_pnl_summary(self) -> dict:
        row = self.conn.execute("""
            SELECT
                COUNT(*) as total_trades,
                COALESCE(SUM(CASE WHEN included = 1 AND reverted = 0 THEN 1 ELSE 0 END), 0) as successful,
                COALESCE(SUM(CASE WHEN reverted = 1 THEN 1 ELSE 0 END), 0) as reverted,
                COALESCE(SUM(CASE WHEN dropped = 1 THEN 1 ELSE 0 END), 0) as dropped,
                COALESCE(SUM(CAST(realized_profit_quote AS REAL)), 0) as total_realized_profit_quote,
                COALESCE(SUM(CAST(fee_paid_base AS REAL)), 0) as total_fee_paid_base,
                COALESCE(SUM(CAST(actual_net_profit AS REAL)), 0) as total_profit,
                COALESCE(SUM(fee_paid_lamports), 0) as total_fee_paid_lamports
            FROM trade_results
        """).fetchone()
        return dict(row) if row else {}

    def get_execution_stats(self, since_iso: str | None = None) -> dict:
        _SQL = """
            SELECT
                COUNT(*) as total_trades,
                COALESCE(SUM(CASE WHEN tr.included = 1 AND tr.reverted = 0 THEN 1 ELSE 0 END), 0) as successful,
                COALESCE(SUM(CASE WHEN tr.reverted = 1 THEN 1 ELSE 0 END), 0) as reverted,
                COALESCE(SUM(CASE WHEN tr.dropped = 1 THEN 1 ELSE 0 END), 0) as dropped,
                COALESCE(SUM(CAST(tr.actual_net_profit AS REAL)), 0) as total_profit,
                COALESCE(SUM(CAST(tr.fee_paid_base AS REAL)), 0) as total_fee_cost,
                COALESCE(SUM(tr.fee_paid_lamports), 0) as total_fee_paid_lamports
            FROM trade_results tr
        """
        if since_iso:
            row = self.conn.execute(
                _SQL + " JOIN execution_attempts ea ON tr.execution_id = ea.execution_id"
                       " JOIN opportunities o ON ea.opportunity_id = o.opportunity_id"
                       " WHERE o.detected_at >= ?",
                (since_iso,),
            ).fetchone()
        else:
            row = self.conn.execute(_SQL).fetchone()
        return dict(row) if row else {}

    def get_pnl_analytics(
        self, since: str | None = None, until: str | None = None,
    ) -> dict:
        """Solana-native PnL analytics.  Per-pair + per-venue + hourly + rejection reasons."""
        conditions, params = [], []
        if since:
            conditions.append("o.detected_at >= ?")
            params.append(since)
        if until:
            conditions.append("o.detected_at <= ?")
            params.append(until)
        where = (" AND " + " AND ".join(conditions)) if conditions else ""
        base_join = (
            "FROM trade_results tr "
            "JOIN execution_attempts ea ON tr.execution_id = ea.execution_id "
            "JOIN opportunities o ON ea.opportunity_id = o.opportunity_id"
        )
        tp = tuple(params)

        per_pair = self.conn.execute(f"""
            SELECT o.pair,
                COUNT(*) as trades,
                SUM(CASE WHEN tr.included = 1 AND tr.reverted = 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN tr.reverted = 1 THEN 1 ELSE 0 END) as reverts,
                SUM(CASE WHEN tr.dropped = 1 THEN 1 ELSE 0 END) as dropped,
                COALESCE(SUM(CAST(tr.actual_net_profit AS REAL)), 0) as net_profit,
                COALESCE(SUM(CAST(tr.fee_paid_base AS REAL)), 0) as fee_cost,
                COALESCE(AVG(CAST(tr.actual_net_profit AS REAL)), 0) as avg_profit
            {base_join}
            WHERE 1=1 {where}
            GROUP BY o.pair
            ORDER BY net_profit DESC
        """, tp).fetchall()

        per_venue = self.conn.execute(f"""
            SELECT o.buy_venue, o.sell_venue,
                COUNT(*) as trades,
                SUM(CASE WHEN tr.included = 1 AND tr.reverted = 0 THEN 1 ELSE 0 END) as wins,
                COALESCE(SUM(CAST(tr.actual_net_profit AS REAL)), 0) as net_profit
            {base_join}
            WHERE 1=1 {where}
            GROUP BY o.buy_venue, o.sell_venue
            ORDER BY net_profit DESC
        """, tp).fetchall()

        hourly_pnl = self.conn.execute(f"""
            SELECT
                SUBSTR(o.detected_at, 1, 13) as hour,
                COUNT(*) as trades,
                SUM(CASE WHEN tr.included = 1 AND tr.reverted = 0 THEN 1 ELSE 0 END) as wins,
                COALESCE(SUM(CAST(tr.actual_net_profit AS REAL)), 0) as net_profit
            {base_join}
            WHERE 1=1 {where}
            GROUP BY SUBSTR(o.detected_at, 1, 13)
            ORDER BY hour DESC
            LIMIT 168
        """, tp).fetchall()

        rejection_reasons = self.conn.execute(f"""
            SELECT rd.reason_code, COUNT(*) as cnt,
                COALESCE(AVG(CAST(p.expected_net_profit AS REAL)), 0) as avg_expected_profit
            FROM risk_decisions rd
            JOIN opportunities o ON rd.opportunity_id = o.opportunity_id
            LEFT JOIN pricing_results p ON o.opportunity_id = p.opportunity_id
            WHERE rd.approved = 0 {where}
            GROUP BY rd.reason_code
            ORDER BY cnt DESC
            LIMIT 20
        """, tp).fetchall()

        return {
            "per_pair": [dict(r) for r in per_pair],
            "per_venue": [dict(r) for r in per_venue],
            "hourly_pnl": [dict(r) for r in hourly_pnl],
            "rejection_reasons": [dict(r) for r in rejection_reasons],
            "filters": {"since": since, "until": until},
        }

    def get_scan_filter_breakdown(
        self, since: str | None = None, until: str | None = None,
    ) -> list[dict]:
        """Per filter-reason: count, avg spread, avg/best net profit.

        Feeds the "Scan History — Filter Breakdown" table on /analytics.
        """
        conds, params = [], []
        if since:
            conds.append("scan_ts >= ?")
            params.append(since)
        if until:
            conds.append("scan_ts <= ?")
            params.append(until)
        where = " WHERE " + " AND ".join(conds) if conds else ""
        rows = self.conn.execute(f"""
            SELECT filter_reason,
                   COUNT(*) as cnt,
                   AVG(CAST(spread_bps AS REAL)) as avg_spread,
                   AVG(CAST(net_profit AS REAL)) as avg_net_profit,
                   MAX(CAST(net_profit AS REAL)) as best_net_profit
            FROM scan_history{where}
            GROUP BY filter_reason
            ORDER BY cnt DESC
        """, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def get_spread_distribution(
        self, since: str | None = None, until: str | None = None,
    ) -> list[dict]:
        """Per (pair, buy_venue, sell_venue): sample count + spread stats."""
        conds, params = [], []
        if since:
            conds.append("scan_ts >= ?")
            params.append(since)
        if until:
            conds.append("scan_ts <= ?")
            params.append(until)
        where_extra = (" AND " + " AND ".join(conds)) if conds else ""
        rows = self.conn.execute(f"""
            SELECT pair, buy_venue, sell_venue,
                   COUNT(*) as samples,
                   AVG(CAST(spread_bps AS REAL)) as avg_spread,
                   MAX(CAST(spread_bps AS REAL)) as max_spread,
                   MIN(CAST(spread_bps AS REAL)) as min_spread
            FROM scan_history
            WHERE CAST(spread_bps AS REAL) > 0 {where_extra}
            GROUP BY pair, buy_venue, sell_venue
            ORDER BY avg_spread DESC
            LIMIT 30
        """, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def get_near_misses(
        self,
        threshold_sol: float = 0.002,
        since: str | None = None,
        limit: int = 30,
    ) -> list[dict]:
        """Unprofitable scans within ``threshold_sol`` of break-even.

        A "near miss" is a detection that failed by a tiny margin — a small
        threshold/slippage tune-up might flip these to profitable.
        """
        conds = ["filter_reason = 'unprofitable'",
                 "CAST(net_profit AS REAL) > ?"]
        params: list = [-threshold_sol]
        if since:
            conds.append("scan_ts >= ?")
            params.append(since)
        where = " AND ".join(conds)
        rows = self.conn.execute(f"""
            SELECT scan_ts, pair, buy_venue, sell_venue,
                   CAST(spread_bps AS REAL) as spread,
                   CAST(net_profit AS REAL) as net_profit,
                   CAST(fee_cost AS REAL) as fee_cost
            FROM scan_history
            WHERE {where}
            ORDER BY CAST(net_profit AS REAL) DESC
            LIMIT ?
        """, tuple(params) + (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_expected_vs_realized(
        self, limit: int = 50, since: str | None = None,
    ) -> list[dict]:
        """Compare what pricing predicted with what the on-chain tx delivered.

        Only rows where a trade_result exists (included or reverted).  Feeds
        the "Expected vs Realized" table on /analytics.
        """
        conds = ["tr.included = 1"]
        params: list = []
        if since:
            conds.append("o.detected_at >= ?")
            params.append(since)
        where = " AND ".join(conds)
        rows = self.conn.execute(f"""
            SELECT o.opportunity_id, o.pair, o.buy_venue, o.sell_venue,
                   o.detected_at,
                   CAST(p.expected_net_profit AS REAL) as expected,
                   CAST(tr.actual_net_profit AS REAL) as realized,
                   CAST(tr.fee_paid_base AS REAL) as fee_paid,
                   tr.fee_paid_lamports,
                   tr.confirmation_slot,
                   ea.signature
            FROM trade_results tr
            JOIN execution_attempts ea ON tr.execution_id = ea.execution_id
            JOIN opportunities o ON ea.opportunity_id = o.opportunity_id
            LEFT JOIN pricing_results p ON o.opportunity_id = p.opportunity_id
            WHERE {where}
            ORDER BY o.detected_at DESC
            LIMIT ?
        """, tuple(params) + (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_diagnostics_snapshot(self, limit: int = 100) -> list[dict]:
        """Most recent quote_diagnostics snapshots, newest first."""
        rows = self.conn.execute(
            "SELECT * FROM quote_diagnostics ORDER BY snapshot_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Pairs / Venues
    # ------------------------------------------------------------------

    def save_pair(
        self,
        pair: str,
        base_symbol: str,
        quote_symbol: str,
        base_mint: str,
        quote_mint: str,
        base_decimals: int = 9,
        quote_decimals: int = 6,
        risk_class: str = "blue_chip",
        max_trade_size: Decimal = Decimal("100"),
    ) -> int:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO pairs "
            "(pair, base_symbol, quote_symbol, base_mint, quote_mint, "
            "base_decimals, quote_decimals, risk_class, max_trade_size, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (pair, base_symbol, quote_symbol, base_mint, quote_mint,
             base_decimals, quote_decimals, risk_class, str(max_trade_size), _now()),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_pair(self, pair: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM pairs WHERE pair = ?", (pair,)).fetchone()
        return _row_to_dict(row)

    def get_enabled_pairs(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM pairs WHERE enabled = 1 ORDER BY pair"
        ).fetchall()
        return [dict(r) for r in rows]

    def set_pair_enabled(self, pair: str, enabled: bool) -> None:
        self.conn.execute(
            "UPDATE pairs SET enabled = ? WHERE pair = ?", (int(enabled), pair),
        )
        self.conn.commit()

    def save_venue(self, name: str, kind: str = "aggregator", enabled: bool = True) -> int:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO venues (name, kind, enabled, created_at) VALUES (?, ?, ?, ?)",
            (name, kind, int(enabled), _now()),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_enabled_venues(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM venues WHERE enabled = 1 ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Quote diagnostics
    # ------------------------------------------------------------------

    def save_diagnostics_snapshot(self, snapshot: dict[str, dict]) -> int:
        """Persist a per-venue+pair quote diagnostics snapshot.

        Keys in ``snapshot`` are ``"venue:pair"`` (e.g. ``"Jupiter-Best:SOL/USDC"``).
        """
        now = _now()
        inserted = 0
        for key, data in snapshot.items():
            parts = key.split(":", 1)
            if len(parts) != 2:
                continue
            venue, pair = parts
            self.conn.execute(
                "INSERT INTO quote_diagnostics "
                "(venue, pair, success_count, total_count, avg_latency_ms, "
                "last_outcome, last_error, snapshot_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (venue, pair,
                 data.get("success_count", 0), data.get("total_quotes", 0),
                 data.get("avg_latency_ms", 0.0),
                 data.get("last_outcome", ""), data.get("last_error", "") or "",
                 now),
            )
            inserted += 1
        self.conn.commit()
        return inserted
