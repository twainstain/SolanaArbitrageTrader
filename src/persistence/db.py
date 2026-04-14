"""Database setup and connection management.

Supports two backends:
  - SQLite (default, zero-config, for development)
  - PostgreSQL via psycopg2 (for production, set DATABASE_URL env var)

The DATABASE_URL environment variable determines the backend:
  - Not set / empty → SQLite at data/arbitrage.db
  - "sqlite:///path" → SQLite at path
  - "postgres://..." or "postgresql://..." → PostgreSQL

All SQL uses standard syntax compatible with both backends.
The Repository uses a DbConnection wrapper that normalizes placeholders.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SQLITE_PATH = _PROJECT_ROOT / "data" / "arbitrage.db"

_db: "DbConnection | None" = None


# ---------------------------------------------------------------------------
# Schema — uses standard SQL compatible with both SQLite and PostgreSQL.
# AUTOINCREMENT syntax differs: SQLite uses INTEGER PRIMARY KEY AUTOINCREMENT,
# PostgreSQL uses SERIAL. We use a list of CREATE TABLE statements and
# adjust per backend.
# ---------------------------------------------------------------------------

_TABLES_SQLITE = """
CREATE TABLE IF NOT EXISTS pairs (
    pair_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pair            TEXT NOT NULL UNIQUE,
    chain           TEXT NOT NULL,
    base_token      TEXT NOT NULL,
    quote_token     TEXT NOT NULL,
    base_decimals   INTEGER NOT NULL DEFAULT 18,
    quote_decimals  INTEGER NOT NULL DEFAULT 6,
    risk_class      TEXT NOT NULL DEFAULT 'blue_chip',
    max_trade_size  TEXT NOT NULL DEFAULT '10',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pools (
    pool_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id         INTEGER NOT NULL REFERENCES pairs(pair_id),
    chain           TEXT NOT NULL,
    dex             TEXT NOT NULL,
    address         TEXT NOT NULL,
    fee_tier_bps    TEXT NOT NULL DEFAULT '30',
    dex_type        TEXT NOT NULL DEFAULT 'uniswap_v3',
    liquidity_class TEXT NOT NULL DEFAULT 'medium',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opportunities (
    opportunity_id  TEXT PRIMARY KEY,
    pair            TEXT NOT NULL,
    chain           TEXT NOT NULL DEFAULT '',
    buy_dex         TEXT NOT NULL,
    sell_dex        TEXT NOT NULL,
    spread_bps      TEXT NOT NULL DEFAULT '0',
    status          TEXT NOT NULL DEFAULT 'detected',
    detected_at     TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pricing_results (
    pricing_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id  TEXT NOT NULL REFERENCES opportunities(opportunity_id),
    input_amount    TEXT NOT NULL,
    estimated_output TEXT NOT NULL,
    fee_cost        TEXT NOT NULL DEFAULT '0',
    slippage_cost   TEXT NOT NULL DEFAULT '0',
    gas_estimate    TEXT NOT NULL DEFAULT '0',
    expected_net_profit TEXT NOT NULL DEFAULT '0',
    buy_liquidity_usd TEXT NOT NULL DEFAULT '0',
    sell_liquidity_usd TEXT NOT NULL DEFAULT '0',
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_decisions (
    decision_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id  TEXT NOT NULL REFERENCES opportunities(opportunity_id),
    approved        INTEGER NOT NULL DEFAULT 0,
    reason_code     TEXT NOT NULL DEFAULT '',
    threshold_snapshot TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS simulations (
    simulation_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id  TEXT NOT NULL REFERENCES opportunities(opportunity_id),
    success         INTEGER NOT NULL DEFAULT 0,
    revert_reason   TEXT NOT NULL DEFAULT '',
    expected_output TEXT NOT NULL DEFAULT '0',
    expected_net_profit TEXT NOT NULL DEFAULT '0',
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_attempts (
    execution_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id  TEXT NOT NULL REFERENCES opportunities(opportunity_id),
    submission_type TEXT NOT NULL DEFAULT 'flashbots',
    relay_target    TEXT NOT NULL DEFAULT '',
    tx_hash         TEXT NOT NULL DEFAULT '',
    bundle_id       TEXT NOT NULL DEFAULT '',
    target_block    INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'pending',
    submitted_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_results (
    result_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id    INTEGER NOT NULL REFERENCES execution_attempts(execution_id),
    included        INTEGER NOT NULL DEFAULT 0,
    reverted        INTEGER NOT NULL DEFAULT 0,
    gas_used        INTEGER NOT NULL DEFAULT 0,
    actual_output   TEXT NOT NULL DEFAULT '0',
    actual_net_profit TEXT NOT NULL DEFAULT '0',
    block_number    INTEGER NOT NULL DEFAULT 0,
    finalized_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_checkpoints (
    checkpoint_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    checkpoint_type TEXT NOT NULL,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discovered_pairs (
    discovered_pair_id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair            TEXT NOT NULL,
    chain           TEXT NOT NULL,
    base_symbol     TEXT NOT NULL,
    quote_symbol    TEXT NOT NULL,
    dex_count       INTEGER NOT NULL DEFAULT 0,
    total_volume_24h REAL NOT NULL DEFAULT 0,
    total_liquidity REAL NOT NULL DEFAULT 0,
    dex_names_json  TEXT NOT NULL DEFAULT '[]',
    base_address    TEXT NOT NULL DEFAULT '',
    quote_address   TEXT NOT NULL DEFAULT '',
    is_blue_chip    INTEGER NOT NULL DEFAULT 0,
    arbitrage_score REAL NOT NULL DEFAULT 0,
    refreshed_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pairs_chain ON pairs(chain);
CREATE INDEX IF NOT EXISTS idx_pools_pair ON pools(pair_id);
CREATE INDEX IF NOT EXISTS idx_pools_chain ON pools(chain);
CREATE INDEX IF NOT EXISTS idx_opp_status ON opportunities(status);
CREATE INDEX IF NOT EXISTS idx_opp_detected ON opportunities(detected_at);
CREATE INDEX IF NOT EXISTS idx_pricing_opp ON pricing_results(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_risk_opp ON risk_decisions(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_sim_opp ON simulations(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_exec_opp ON execution_attempts(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_result_exec ON trade_results(execution_id);
CREATE INDEX IF NOT EXISTS idx_checkpoint_type ON system_checkpoints(checkpoint_type);
CREATE INDEX IF NOT EXISTS idx_discovered_pairs_chain ON discovered_pairs(chain);
CREATE INDEX IF NOT EXISTS idx_discovered_pairs_pair ON discovered_pairs(pair);

CREATE TABLE IF NOT EXISTS quote_diagnostics (
    diagnostic_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    dex             TEXT NOT NULL,
    chain           TEXT NOT NULL,
    pair            TEXT NOT NULL,
    success_count   INTEGER NOT NULL DEFAULT 0,
    total_count     INTEGER NOT NULL DEFAULT 0,
    avg_latency_ms  REAL NOT NULL DEFAULT 0,
    last_outcome    TEXT NOT NULL DEFAULT '',
    last_error      TEXT NOT NULL DEFAULT '',
    snapshot_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_diag_dex_chain ON quote_diagnostics(dex, chain);
"""

_TABLES_POSTGRES = _TABLES_SQLITE.replace(
    "INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY"
)


# ---------------------------------------------------------------------------
# DbConnection — thin wrapper that normalizes placeholders (? vs %s)
# and provides a consistent interface for both backends.
# ---------------------------------------------------------------------------

class DbConnection:
    """Unified database connection wrapper for SQLite and PostgreSQL."""

    def __init__(self, conn: Any, backend: str) -> None:
        self._conn = conn
        self.backend = backend  # "sqlite" or "postgres"
        self._batch_depth = 0

    def _adapt_sql(self, sql: str) -> str:
        """Convert SQLite-specific SQL to PostgreSQL equivalents.

        Handles:
          - ? → %s placeholder conversion
          - INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
        """
        if self.backend == "postgres":
            if "INSERT OR IGNORE" in sql:
                sql = sql.replace("INSERT OR IGNORE", "INSERT")
                # Append ON CONFLICT DO NOTHING if not already present.
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
            # PostgreSQL: execute as a single statement block.
            cur = self._conn.cursor()
            cur.execute(sql)
            cur.close()

    def commit(self) -> None:
        if self._batch_depth > 0:
            return
        self._conn.commit()

    @contextmanager
    def batch(self):
        """Suppress individual commit() calls; do one commit at the end."""
        self._batch_depth += 1
        try:
            yield
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0:
                self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @property
    def raw(self) -> Any:
        """Access the underlying connection (for advanced use)."""
        return self._conn


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------

def _parse_database_url() -> tuple[str, str]:
    """Parse DATABASE_URL and return (backend, url/path).

    Returns:
        ("sqlite", "/path/to/db") or ("postgres", "postgres://...")
    """
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
    conn.execute("PRAGMA mmap_size=268435456")
    conn.execute("PRAGMA cache_size=-8192")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA foreign_keys=ON")
    return DbConnection(conn, "sqlite")


def _connect_postgres(url: str) -> DbConnection:
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        raise ImportError(
            "psycopg2 is required for PostgreSQL. Install it with: "
            "pip install psycopg2-binary"
        )
    conn = psycopg2.connect(url)
    conn.autocommit = False
    # Use RealDictCursor so rows come back as dicts (like sqlite3.Row).
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return DbConnection(conn, "postgres")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_db(db_path: str | Path | None = None) -> DbConnection:
    """Initialize the database and return a DbConnection.

    Args:
        db_path: Override path/URL. If None, uses DATABASE_URL env var
                 or defaults to SQLite at data/arbitrage.db.
    """
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
    """Return the current database connection, initializing if needed."""
    global _db
    if _db is None:
        return init_db()
    return _db


def close_db() -> None:
    """Close the database connection."""
    global _db
    if _db is not None:
        _db.close()
        _db = None
