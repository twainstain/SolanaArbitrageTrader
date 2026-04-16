"""Environment configuration helpers for shared trading-platform code.

The shared package should not assume a single repository layout. Callers can
either pass an explicit ``env_path`` or provide a ``start_dir`` to search from.
If neither is provided, we search upward from the current working directory.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - fallback used only in minimal envs.
    def load_dotenv(path: str | Path, override: bool = False) -> bool:
        """Lightweight fallback loader when python-dotenv is unavailable."""
        env_file = Path(path)
        if not env_file.exists():
            return False

        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if override or key not in os.environ:
                os.environ[key] = value
        return True


def find_env_file(
    env_path: str | Path | None = None,
    *,
    start_dir: str | Path | None = None,
) -> Path | None:
    """Resolve the .env file to load."""
    if env_path is not None:
        candidate = Path(env_path).expanduser()
        return candidate.resolve() if candidate.exists() else None

    search_root = Path(start_dir).expanduser() if start_dir is not None else Path.cwd()
    search_root = search_root.resolve()

    for parent in [search_root] + list(search_root.parents):
        candidate = parent / ".env"
        if candidate.exists():
            return candidate
    return None


def load_env(
    env_path: str | Path | None = None,
    *,
    start_dir: str | Path | None = None,
) -> Path | None:
    """Load a .env file and return the path used, if any.

    Already-set environment variables take precedence.
    """
    resolved = find_env_file(env_path, start_dir=start_dir)
    if resolved is not None:
        load_dotenv(resolved, override=False)
    return resolved


def get_env(key: str, default: str = "") -> str:
    """Get an environment variable with a default."""
    return os.environ.get(key, default)


def require_env(key: str) -> str:
    """Get a required environment variable. Raises if not set."""
    val = os.environ.get(key)
    if not val:
        raise EnvironmentError(f"Required environment variable not set: {key}")
    return val
