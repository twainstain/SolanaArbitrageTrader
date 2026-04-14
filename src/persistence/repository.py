"""Repository — CRUD operations for the candidate lifecycle.

Each method maps to one stage in the architecture doc's candidate lifecycle:
  detected → priced → risk_approved/rejected → simulated → submitted → outcome
"""

from __future__ import annotations

import json
import time as _time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from persistence.db import DbConnection
from registry.discovery import DiscoveredPair


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
        self._count_cache: tuple[float, str, str | None, int] | None = None  # (ts, since, status, count)

    # ------------------------------------------------------------------
    # Opportunities
    # ------------------------------------------------------------------

    def create_opportunity(
        self,
        pair: str,
        chain: str,
        buy_dex: str,
        sell_dex: str,
        spread_bps: Decimal,
    ) -> str:
        """Insert a new detected opportunity. Returns the opportunity_id."""
        opp_id = f"opp_{uuid.uuid4().hex[:12]}"
        now = _now()
        self.conn.execute(
            "INSERT INTO opportunities "
            "(opportunity_id, pair, chain, buy_dex, sell_dex, spread_bps, status, detected_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'detected', ?, ?)",
            (opp_id, pair, chain, buy_dex, sell_dex, str(spread_bps), now, now),
        )
        self.conn.commit()
        return opp_id

    def update_opportunity_status(self, opp_id: str, status: str) -> None:
        """Update the status of an opportunity."""
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

        Result is cached for 5 seconds — the hourly trade count changes slowly
        and doesn't need a fresh SELECT on every pipeline call.
        """
        now = _time.monotonic()
        if self._count_cache is not None:
            ts, cached_since, cached_status, cached_count = self._count_cache
            if (now - ts) < 5.0 and cached_since == since_iso and cached_status == status:
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
        self._count_cache = (now, since_iso, status, count)
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
        gas_estimate: Decimal,
        expected_net_profit: Decimal,
        buy_liquidity_usd: Decimal = Decimal("0"),
        sell_liquidity_usd: Decimal = Decimal("0"),
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO pricing_results "
            "(opportunity_id, input_amount, estimated_output, fee_cost, slippage_cost, "
            "gas_estimate, expected_net_profit, buy_liquidity_usd, sell_liquidity_usd, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (opp_id, str(input_amount), str(estimated_output), str(fee_cost),
             str(slippage_cost), str(gas_estimate), str(expected_net_profit),
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
             json.dumps(threshold_snapshot or {}), _now()),
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
        submission_type: str = "flashbots",
        relay_target: str = "",
        tx_hash: str = "",
        bundle_id: str = "",
        target_block: int = 0,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO execution_attempts "
            "(opportunity_id, submission_type, relay_target, tx_hash, bundle_id, "
            "target_block, status, submitted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'submitted', ?)",
            (opp_id, submission_type, relay_target, tx_hash, bundle_id, target_block, _now()),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def update_execution_status(self, execution_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE execution_attempts SET status = ? WHERE execution_id = ?",
            (status, execution_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Trade Results
    # ------------------------------------------------------------------

    def save_trade_result(
        self,
        execution_id: int,
        included: bool,
        reverted: bool = False,
        gas_used: int = 0,
        actual_output: Decimal = Decimal("0"),
        actual_net_profit: Decimal = Decimal("0"),
        block_number: int = 0,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO trade_results "
            "(execution_id, included, reverted, gas_used, actual_output, "
            "actual_net_profit, block_number, finalized_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (execution_id, int(included), int(reverted), gas_used,
             str(actual_output), str(actual_net_profit), block_number, _now()),
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
        """Upsert a system checkpoint (e.g., last_processed_block)."""
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
    # Aggregation queries
    # ------------------------------------------------------------------

    def get_pnl_summary(self) -> dict:
        """Return aggregate PnL stats from trade_results."""
        row = self.conn.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN included = 1 AND reverted = 0 THEN 1 ELSE 0 END) as successful,
                SUM(CASE WHEN reverted = 1 THEN 1 ELSE 0 END) as reverted,
                SUM(CASE WHEN included = 0 THEN 1 ELSE 0 END) as not_included,
                COALESCE(SUM(CAST(actual_net_profit AS REAL)), 0) as total_profit,
                COALESCE(SUM(gas_used), 0) as total_gas
            FROM trade_results
        """).fetchone()
        return dict(row) if row else {}

    def get_opportunity_funnel(self) -> dict:
        """Return counts by opportunity status for the funnel view."""
        rows = self.conn.execute(
            "SELECT status, COUNT(*) as count FROM opportunities GROUP BY status"
        ).fetchall()
        return {r["status"]: r["count"] for r in rows}

    # ------------------------------------------------------------------
    # Pairs
    # ------------------------------------------------------------------

    def save_pair(
        self,
        pair: str,
        chain: str,
        base_token: str,
        quote_token: str,
        base_decimals: int = 18,
        quote_decimals: int = 6,
        risk_class: str = "blue_chip",
        max_trade_size: Decimal = Decimal("10"),
    ) -> int:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO pairs "
            "(pair, chain, base_token, quote_token, base_decimals, quote_decimals, "
            "risk_class, max_trade_size, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (pair, chain, base_token, quote_token, base_decimals, quote_decimals,
             risk_class, str(max_trade_size), _now()),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_pair(self, pair: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM pairs WHERE pair = ?", (pair,)
        ).fetchone()
        return _row_to_dict(row)

    def get_pair_on_chain(self, pair: str, chain: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM pairs WHERE pair = ? AND chain = ?",
            (pair, chain),
        ).fetchone()
        return _row_to_dict(row)

    def get_enabled_pairs(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM pairs WHERE enabled = 1 ORDER BY pair"
        ).fetchall()
        return [dict(r) for r in rows]

    def set_pair_enabled(self, pair: str, enabled: bool) -> None:
        self.conn.execute(
            "UPDATE pairs SET enabled = ? WHERE pair = ?", (int(enabled), pair)
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Pools
    # ------------------------------------------------------------------

    def save_pool(
        self,
        pair_id: int,
        chain: str,
        dex: str,
        address: str,
        fee_tier_bps: Decimal = Decimal("30"),
        dex_type: str = "uniswap_v3",
        liquidity_class: str = "medium",
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO pools "
            "(pair_id, chain, dex, address, fee_tier_bps, dex_type, "
            "liquidity_class, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (pair_id, chain, dex, address, str(fee_tier_bps), dex_type,
             liquidity_class, _now()),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_pool_by_address(self, address: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM pools WHERE address = ? LIMIT 1", (address,)
        ).fetchone()
        return _row_to_dict(row)

    def save_pool_if_missing(
        self,
        pair_id: int,
        chain: str,
        dex: str,
        address: str,
        fee_tier_bps: Decimal = Decimal("30"),
        dex_type: str = "uniswap_v3",
        liquidity_class: str = "medium",
    ) -> int | None:
        existing = self.get_pool_by_address(address)
        if existing is not None:
            return None
        return self.save_pool(
            pair_id=pair_id,
            chain=chain,
            dex=dex,
            address=address,
            fee_tier_bps=fee_tier_bps,
            dex_type=dex_type,
            liquidity_class=liquidity_class,
        )

    def get_pools_for_pair(self, pair_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM pools WHERE pair_id = ? AND enabled = 1", (pair_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_enabled_pools_for_pair_name(self, pair: str, chain: str | None = None) -> list[dict]:
        sql = (
            "SELECT pools.* FROM pools "
            "JOIN pairs ON pairs.pair_id = pools.pair_id "
            "WHERE pairs.pair = ? AND pools.enabled = 1"
        )
        params: tuple[Any, ...] = (pair,)
        if chain is not None:
            sql += " AND pools.chain = ?"
            params += (chain,)
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def set_pool_enabled(self, pool_id: int, enabled: bool) -> None:
        self.conn.execute(
            "UPDATE pools SET enabled = ? WHERE pool_id = ?", (int(enabled), pool_id)
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Discovered Pair Metadata
    # ------------------------------------------------------------------

    def replace_discovered_pairs(self, pairs: list[DiscoveredPair]) -> None:
        """Replace the persisted discovery snapshot with the latest result set."""
        now = _now()
        with self.conn.batch():
            self.conn.execute("DELETE FROM discovered_pairs")
            for pair in pairs:
                self.conn.execute(
                    "INSERT INTO discovered_pairs "
                    "(pair, chain, base_symbol, quote_symbol, dex_count, total_volume_24h, "
                    "total_liquidity, dex_names_json, base_address, quote_address, "
                    "is_blue_chip, arbitrage_score, refreshed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        pair.pair_name,
                        pair.chain,
                        pair.base_symbol,
                        pair.quote_symbol,
                        pair.dex_count,
                        pair.total_volume_24h,
                        pair.total_liquidity,
                        json.dumps(pair.dex_names),
                        pair.base_address,
                        pair.quote_address,
                        int(pair.is_blue_chip),
                        pair.arbitrage_score,
                        now,
                    ),
                )

    def get_discovered_pairs(self, limit: int | None = None) -> list[DiscoveredPair]:
        sql = (
            "SELECT pair, chain, base_symbol, quote_symbol, dex_count, total_volume_24h, "
            "total_liquidity, dex_names_json, base_address, quote_address, is_blue_chip, "
            "arbitrage_score FROM discovered_pairs ORDER BY arbitrage_score DESC, pair ASC"
        )
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        rows = self.conn.execute(sql, params).fetchall()
        return [
            DiscoveredPair(
                pair_name=row["pair"],
                base_symbol=row["base_symbol"],
                quote_symbol=row["quote_symbol"],
                chain=row["chain"],
                dex_count=row["dex_count"],
                total_volume_24h=row["total_volume_24h"],
                total_liquidity=row["total_liquidity"],
                dex_names=json.loads(row["dex_names_json"]),
                base_address=row["base_address"],
                quote_address=row["quote_address"],
                is_blue_chip=bool(row["is_blue_chip"]),
                arbitrage_score=row["arbitrage_score"],
            )
            for row in rows
        ]

    def count_discovered_pairs(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM discovered_pairs"
        ).fetchone()
        return row["cnt"] if row else 0

    def count_enabled_pools(self, chain: str | None = None) -> int:
        sql = "SELECT COUNT(*) as cnt FROM pools WHERE enabled = 1"
        params: tuple[Any, ...] = ()
        if chain is not None:
            sql += " AND chain = ?"
            params = (chain,)
        row = self.conn.execute(sql, params).fetchone()
        return row["cnt"] if row else 0
