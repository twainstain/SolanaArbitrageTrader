"""SolanaTrader database setup and connection management.

Supports two backends:
  - SQLite (default, zero-config, for development)
  - PostgreSQL via psycopg2 (for production, set DATABASE_URL env var)

All financial/amount fields are TEXT and hold canonical ``str(Decimal)`` so
values round-trip without float drift.  Integer slot/lamport fields are
BIGINT/INTEGER because Solana slots exceed 2^31 and lamports can too for
whale transfers.

Schema shape is Solana-native — replaces the EVM schema.  The new schema
lives in a separate DB file by default (``data/solana_arb.db``) so the
legacy EVM database at ``data/arbitrage.db`` is untouched.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SQLITE_PATH = _PROJECT_ROOT / "data" / "solana_arb.db"

_db: "DbConnection | None" = None


# ---------------------------------------------------------------------------
# Schema — Solana-native field names. Uses BIGINT for slot/lamport fields.
# ---------------------------------------------------------------------------

_TABLES_SQLITE = """
CREATE TABLE IF NOT EXISTS pairs (
    pair_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pair            TEXT NOT NULL UNIQUE,
    base_symbol     TEXT NOT NULL,
    quote_symbol    TEXT NOT NULL,
    base_mint       TEXT NOT NULL,
    quote_mint      TEXT NOT NULL,
    base_decimals   INTEGER NOT NULL DEFAULT 9,
    quote_decimals  INTEGER NOT NULL DEFAULT 6,
    risk_class      TEXT NOT NULL DEFAULT 'blue_chip',
    max_trade_size  TEXT NOT NULL DEFAULT '100',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS venues (
    venue_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    kind            TEXT NOT NULL DEFAULT 'aggregator',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opportunities (
    opportunity_id  TEXT PRIMARY KEY,
    pair            TEXT NOT NULL,
    buy_venue       TEXT NOT NULL,
    sell_venue      TEXT NOT NULL,
    spread_bps      TEXT NOT NULL DEFAULT '0',
    status          TEXT NOT NULL DEFAULT 'detected',
    detected_at     TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pricing_results (
    pricing_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id      TEXT NOT NULL REFERENCES opportunities(opportunity_id),
    input_amount        TEXT NOT NULL,
    estimated_output    TEXT NOT NULL,
    venue_fee_cost      TEXT NOT NULL DEFAULT '0',
    slippage_cost       TEXT NOT NULL DEFAULT '0',
    fee_estimate_base   TEXT NOT NULL DEFAULT '0',
    expected_net_profit TEXT NOT NULL DEFAULT '0',
    buy_liquidity_usd   TEXT NOT NULL DEFAULT '0',
    sell_liquidity_usd  TEXT NOT NULL DEFAULT '0',
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_decisions (
    decision_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id     TEXT NOT NULL REFERENCES opportunities(opportunity_id),
    approved           INTEGER NOT NULL DEFAULT 0,
    reason_code        TEXT NOT NULL DEFAULT '',
    threshold_snapshot TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS simulations (
    simulation_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id      TEXT NOT NULL REFERENCES opportunities(opportunity_id),
    success             INTEGER NOT NULL DEFAULT 0,
    revert_reason       TEXT NOT NULL DEFAULT '',
    expected_output     TEXT NOT NULL DEFAULT '0',
    expected_net_profit TEXT NOT NULL DEFAULT '0',
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_attempts (
    execution_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id   TEXT NOT NULL REFERENCES opportunities(opportunity_id),
    submission_kind  TEXT NOT NULL DEFAULT 'rpc',
    submission_ref   TEXT NOT NULL DEFAULT '',
    signature        TEXT NOT NULL DEFAULT '',
    metadata         TEXT NOT NULL DEFAULT '{}',
    status           TEXT NOT NULL DEFAULT 'pending',
    submitted_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_results (
    result_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id          INTEGER NOT NULL REFERENCES execution_attempts(execution_id),
    included              INTEGER NOT NULL DEFAULT 0,
    reverted              INTEGER NOT NULL DEFAULT 0,
    dropped               INTEGER NOT NULL DEFAULT 0,
    fee_paid_lamports     INTEGER NOT NULL DEFAULT 0,
    realized_profit_quote TEXT NOT NULL DEFAULT '0',
    fee_paid_base         TEXT NOT NULL DEFAULT '0',
    profit_currency       TEXT NOT NULL DEFAULT '',
    actual_net_profit     TEXT NOT NULL DEFAULT '0',
    confirmation_slot     INTEGER NOT NULL DEFAULT 0,
    finalized_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_history (
    scan_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_ts         TEXT NOT NULL,
    pair            TEXT NOT NULL,
    buy_venue       TEXT NOT NULL,
    sell_venue      TEXT NOT NULL,
    buy_price       TEXT NOT NULL DEFAULT '0',
    sell_price      TEXT NOT NULL DEFAULT '0',
    spread_bps      TEXT NOT NULL DEFAULT '0',
    gross_profit    TEXT NOT NULL DEFAULT '0',
    net_profit      TEXT NOT NULL DEFAULT '0',
    fee_cost        TEXT NOT NULL DEFAULT '0',
    venue_fee_cost  TEXT NOT NULL DEFAULT '0',
    slippage_cost   TEXT NOT NULL DEFAULT '0',
    filter_reason   TEXT NOT NULL DEFAULT '',
    passed          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS quote_diagnostics (
    diagnostic_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    venue           TEXT NOT NULL,
    pair            TEXT NOT NULL,
    success_count   INTEGER NOT NULL DEFAULT 0,
    total_count     INTEGER NOT NULL DEFAULT 0,
    avg_latency_ms  REAL NOT NULL DEFAULT 0,
    last_outcome    TEXT NOT NULL DEFAULT '',
    last_error      TEXT NOT NULL DEFAULT '',
    snapshot_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_checkpoints (
    checkpoint_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    checkpoint_type TEXT NOT NULL,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_opp_status         ON opportunities(status);
CREATE INDEX IF NOT EXISTS idx_opp_detected       ON opportunities(detected_at);
CREATE INDEX IF NOT EXISTS idx_pricing_opp        ON pricing_results(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_risk_opp           ON risk_decisions(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_sim_opp            ON simulations(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_exec_opp           ON execution_attempts(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_result_exec        ON trade_results(execution_id);
CREATE INDEX IF NOT EXISTS idx_scan_ts            ON scan_history(scan_ts);
CREATE INDEX IF NOT EXISTS idx_scan_pair          ON scan_history(pair);
CREATE INDEX IF NOT EXISTS idx_scan_reason        ON scan_history(filter_reason);
CREATE INDEX IF NOT EXISTS idx_diag_venue         ON quote_diagnostics(venue);
CREATE INDEX IF NOT EXISTS idx_checkpoint_type    ON system_checkpoints(checkpoint_type);
"""

_TABLES_POSTGRES = _TABLES_SQLITE.replace(
    "INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY"
)


# ---------------------------------------------------------------------------
# DbConnection
# ---------------------------------------------------------------------------

class DbConnection:
    """Unified database connection wrapper for SQLite and PostgreSQL."""

    def __init__(self, conn: Any, backend: str) -> None:
        self._conn = conn
        self.backend = backend
        self._batch_depth = 0

    def _adapt_sql(self, sql: str) -> str:
        if self.backend == "postgres":
            if "INSERT OR IGNORE" in sql:
                sql = sql.replace("INSERT OR IGNORE", "INSERT")
                if "ON CONFLICT" not in sql:
                    sql = sql.rstrip() + " ON CONFLICT DO NOTHING"
            return sql.replace("?", "%s")
        return sql

    def execute(self, sql: str, params: tuple = ()) -> Any:
        if self.backend == "postgres":
            cur = self._conn.cursor()
            cur.execute(self._adapt_sql(sql), params)
            return cur
        return self._conn.execute(self._adapt_sql(sql), params)

    def executescript(self, sql: str) -> None:
        if self.backend == "sqlite":
            self._conn.executescript(sql)
        else:
            cur = self._conn.cursor()
            cur.execute(sql)
            cur.close()

    def commit(self) -> None:
        if self._batch_depth > 0:
            return
        self._conn.commit()

    @contextmanager
    def batch(self):
        """Suppress commits inside the block; single commit at end, rollback on error."""
        self._batch_depth += 1
        try:
            yield
        except Exception:
            self._batch_depth -= 1
            if self._batch_depth == 0:
                self._conn.rollback()
            raise
        else:
            self._batch_depth -= 1
            if self._batch_depth == 0:
                self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @property
    def raw(self) -> Any:
        return self._conn


def _parse_database_url() -> tuple[str, str]:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return "sqlite", str(_DEFAULT_SQLITE_PATH)
    if url.startswith("sqlite:///"):
        return "sqlite", url[len("sqlite:///"):]
    if url.startswith(("postgres://", "postgresql://")):
        return "postgres", url
    return "sqlite", str(_DEFAULT_SQLITE_PATH)


def _connect_sqlite(path: str) -> DbConnection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return DbConnection(conn, "sqlite")


def _connect_postgres(url: str) -> DbConnection:
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        raise ImportError(
            "psycopg2 is required for PostgreSQL. Install: pip install psycopg2-binary"
        )
    conn = psycopg2.connect(url)
    conn.autocommit = False
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return DbConnection(conn, "postgres")


def init_db(db_path: str | Path | None = None) -> DbConnection:
    """Initialize the Solana database and return a DbConnection."""
    global _db
    if db_path is not None:
        path_str = str(db_path)
        if path_str.startswith(("postgres://", "postgresql://")):
            db = _connect_postgres(path_str)
            db.executescript(_TABLES_POSTGRES)
        else:
            db = _connect_sqlite(path_str)
            db.executescript(_TABLES_SQLITE)
    else:
        backend, url = _parse_database_url()
        if backend == "postgres":
            db = _connect_postgres(url)
            db.executescript(_TABLES_POSTGRES)
        else:
            db = _connect_sqlite(url)
            db.executescript(_TABLES_SQLITE)
    db.commit()
    _db = db
    return db


def get_db() -> DbConnection:
    global _db
    if _db is None:
        return init_db()
    return _db


def close_db() -> None:
    global _db
    if _db is not None:
        _db.close()
        _db = None
