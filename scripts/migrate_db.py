#!/usr/bin/env python3
"""Run the SolanaTrader schema against whatever ``DATABASE_URL`` points at.

Safe to run repeatedly — the schema uses ``CREATE TABLE IF NOT EXISTS``
and ``CREATE INDEX IF NOT EXISTS`` everywhere.  Use this on a fresh Neon
DB before first deploy, and again after any future schema change.

Usage:
    DATABASE_URL=postgres://user:pass@host.neon.tech/solana_arb \\
      PYTHONPATH=src python3 scripts/migrate_db.py
    # or with SQLite (default when DATABASE_URL is empty)
    PYTHONPATH=src python3 scripts/migrate_db.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.env import load_env
from persistence.db import init_db


def main() -> None:
    load_env()
    url = os.environ.get("DATABASE_URL", "")
    target = url if url else "SQLite (data/solana_arb.db)"
    print(f"Running schema against: {target}")
    db = init_db()

    # Sanity-check: we can read the schema back.  Both SQLite and Postgres
    # expose table listings via different catalogues — handle each.
    if db.backend == "sqlite":
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT table_name AS name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        ).fetchall()
    tables = [r["name"] for r in rows]
    print(f"Tables present ({len(tables)}):")
    for t in tables:
        print(f"  - {t}")
    print("Migration complete.")


if __name__ == "__main__":
    main()
