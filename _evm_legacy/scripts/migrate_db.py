#!/usr/bin/env python3
"""Apply schema migrations to the production database.

Adds new columns/tables that were added after the initial deployment.
Safe to run multiple times — uses IF NOT EXISTS / catches duplicate column errors.

Usage:
    PYTHONPATH=src python scripts/migrate_db.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from env import load_env
load_env()

from persistence.db import init_db

conn = init_db()
print(f"Backend: {conn.backend}")
print()

migrations = [
    (
        "Add buy_liquidity_usd to pricing_results",
        "ALTER TABLE pricing_results ADD COLUMN buy_liquidity_usd TEXT NOT NULL DEFAULT '0'",
    ),
    (
        "Add sell_liquidity_usd to pricing_results",
        "ALTER TABLE pricing_results ADD COLUMN sell_liquidity_usd TEXT NOT NULL DEFAULT '0'",
    ),
    (
        "Create quote_diagnostics table",
        """CREATE TABLE IF NOT EXISTS quote_diagnostics (
            diagnostic_id {pk},
            dex TEXT NOT NULL,
            chain TEXT NOT NULL,
            pair TEXT NOT NULL,
            success_count INTEGER NOT NULL DEFAULT 0,
            total_count INTEGER NOT NULL DEFAULT 0,
            avg_latency_ms REAL NOT NULL DEFAULT 0,
            last_outcome TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            snapshot_at TEXT NOT NULL
        )""".format(pk="SERIAL PRIMARY KEY" if conn.backend == "postgres" else "INTEGER PRIMARY KEY AUTOINCREMENT"),
    ),
    (
        "Create idx_diag_dex_chain index",
        "CREATE INDEX IF NOT EXISTS idx_diag_dex_chain ON quote_diagnostics(dex, chain)",
    ),
]

applied = 0
skipped = 0
for desc, sql in migrations:
    try:
        conn.execute(sql)
        conn.commit()
        print(f"  OK: {desc}")
        applied += 1
    except Exception as e:
        err = str(e).lower()
        if "already exists" in err or "duplicate column" in err:
            print(f"  SKIP: {desc} (already applied)")
            skipped += 1
        else:
            print(f"  FAIL: {desc} — {e}")

print()
print(f"Done. Applied: {applied}, Skipped: {skipped}")
