"""Environment configuration — load .env files."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_env(env_path: str | Path | None = None) -> None:
    """Load a .env file. Already-set env vars take precedence."""
    if env_path is None:
        # Walk up from CWD to find .env
        for parent in [Path.cwd()] + list(Path.cwd().parents):
            candidate = parent / ".env"
            if candidate.exists():
                env_path = candidate
                break
    if env_path:
        load_dotenv(env_path, override=False)


def get_env(key: str, default: str = "") -> str:
    """Get an environment variable with a default."""
    return os.environ.get(key, default)


def require_env(key: str) -> str:
    """Get a required environment variable. Raises if not set."""
    val = os.environ.get(key)
    if not val:
        raise EnvironmentError(f"Required environment variable not set: {key}")
    return val
