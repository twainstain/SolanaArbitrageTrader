#!/usr/bin/env python3
"""Write a clean Postgres + S3 block into .env for SolanaTrader.

Idempotent: strips any existing copies of the managed keys before writing.
Generates a fresh 64-char hex POSTGRES_PASSWORD (unless --keep-password).
DATABASE_URL defaults to the local `postgres` docker-compose service; pass
an explicit one for a managed DB.

Usage:
    # Fresh install on EC2 — use the local container:
    python3 scripts/setup_env.py

    # Preserve an existing password (e.g. rerun after a bot restart):
    python3 scripts/setup_env.py --keep-password

    # Point DATABASE_URL elsewhere (managed Postgres, Neon, etc.):
    python3 scripts/setup_env.py \\
        --database-url "postgresql://user:pass@host:5432/solana_arb"

    # Read the URL from a file (no shell quoting):
    python3 scripts/setup_env.py --database-url-file /tmp/db_url.txt
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import secrets
import sys
from datetime import datetime, timezone

MANAGED_KEYS = (
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "DATABASE_URL",
    "BACKUP_S3_BUCKET",
    "BACKUP_S3_PREFIX",
    "BACKUP_RETENTION_DAYS",
    "AWS_REGION",
)

DEFAULT_DB = "solana_arb"
DEFAULT_USER = "solana"
DEFAULT_BUCKET = "yeda-ai-solana-backups"
DEFAULT_PREFIX = "daily"
DEFAULT_REGION = "us-east-1"
DEFAULT_RETENTION = "30"


def read_env(path: pathlib.Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(
        re.findall(r"^([A-Z_][A-Z0-9_]*)=(.*)$", path.read_text(), re.MULTILINE)
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=".env")
    ap.add_argument(
        "--keep-password",
        action="store_true",
        help="reuse existing POSTGRES_PASSWORD if one is set",
    )
    ap.add_argument(
        "--database-url",
        help="explicit DATABASE_URL value (managed / external DB)",
    )
    ap.add_argument(
        "--database-url-file",
        help="read DATABASE_URL from this file (avoids shell quoting)",
    )
    ap.add_argument("--bucket", default=DEFAULT_BUCKET)
    ap.add_argument("--prefix", default=DEFAULT_PREFIX)
    ap.add_argument("--region", default=DEFAULT_REGION)
    args = ap.parse_args()

    if args.database_url and args.database_url_file:
        print(
            "ERROR: pass at most one of --database-url / --database-url-file",
            file=sys.stderr,
        )
        return 2
    if args.database_url_file:
        db_url = pathlib.Path(args.database_url_file).read_text().strip()
    elif args.database_url:
        db_url = args.database_url.strip()
    else:
        db_url = None

    env_path = pathlib.Path(args.env_file)
    if not env_path.exists():
        print(f"{env_path} not found — creating fresh.")
        env_path.touch(mode=0o600)

    existing = read_env(env_path)
    if args.keep_password and existing.get("POSTGRES_PASSWORD"):
        pw = existing["POSTGRES_PASSWORD"]
        print("Reusing existing POSTGRES_PASSWORD.")
    else:
        pw = secrets.token_hex(32)  # 64 clean hex chars

    if db_url is None:
        db_url = f"postgresql://{DEFAULT_USER}:{pw}@postgres:5432/{DEFAULT_DB}"
        print("DATABASE_URL → local postgres container (default).")
    else:
        if not db_url.startswith(("postgres://", "postgresql://")):
            print(
                f"ERROR: DATABASE_URL must start with postgres:// or postgresql:// "
                f"— got {db_url[:30]!r}",
                file=sys.stderr,
            )
            return 2
        print(f"DATABASE_URL → {db_url[:40]}... (explicit)")

    backup = env_path.with_name(
        f".env.bak.{int(datetime.now(timezone.utc).timestamp())}"
    )
    backup.write_text(env_path.read_text())
    os.chmod(backup, 0o600)
    print(f"Backed up existing .env → {backup.name}")

    lines = env_path.read_text().splitlines()
    kept = [
        l for l in lines
        if not any(re.match(rf"^\s*{k}\s*=", l) for k in MANAGED_KEYS)
    ]

    block = f"""
# --- Self-hosted Postgres (managed by scripts/setup_env.py) ---
POSTGRES_DB={DEFAULT_DB}
POSTGRES_USER={DEFAULT_USER}
POSTGRES_PASSWORD={pw}
DATABASE_URL={db_url}

# --- S3 backup ---
BACKUP_S3_BUCKET={args.bucket}
BACKUP_S3_PREFIX={args.prefix}
BACKUP_RETENTION_DAYS={DEFAULT_RETENTION}
AWS_REGION={args.region}
""".strip()

    env_path.write_text("\n".join(kept).rstrip() + "\n\n" + block + "\n")
    os.chmod(env_path, 0o600)

    verify = read_env(env_path)
    for k in MANAGED_KEYS:
        v = verify.get(k, "")
        if not v:
            print(f"FAIL: {k} missing after write", file=sys.stderr)
            return 1
        if v != v.strip():
            print(f"FAIL: {k} has whitespace: {v!r}", file=sys.stderr)
            return 1

    print(f"Wrote clean block to {env_path} (chmod 600).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
