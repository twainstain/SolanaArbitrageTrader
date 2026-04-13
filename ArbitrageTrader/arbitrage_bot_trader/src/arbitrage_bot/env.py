"""Load .env file and provide typed access to environment settings."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Walk up from this file to find the project root .env.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _PROJECT_ROOT / ".env"


def load_env() -> None:
    """Load the .env file if it exists.  Already-set env vars take precedence."""
    load_dotenv(_ENV_PATH, override=False)


def get_thegraph_api_key() -> str:
    return os.environ.get("THEGRAPH_API_KEY", "")


def get_rpc_overrides() -> dict[str, str]:
    """Return any custom RPC URLs from the environment."""
    overrides: dict[str, str] = {}
    for chain, env_var in (
        ("ethereum", "RPC_ETHEREUM"),
        ("base", "RPC_BASE"),
        ("arbitrum", "RPC_ARBITRUM"),
        ("bsc", "RPC_BSC"),
    ):
        url = os.environ.get(env_var, "")
        if url:
            overrides[chain] = url
    return overrides


def get_bot_config_path() -> str:
    return os.environ.get("BOT_CONFIG", "config/example_config.json")


def get_bot_iterations() -> int:
    return int(os.environ.get("BOT_ITERATIONS", "10"))


def get_bot_dry_run() -> bool:
    return os.environ.get("BOT_DRY_RUN", "false").lower() in ("true", "1", "yes")


def get_bot_no_sleep() -> bool:
    return os.environ.get("BOT_NO_SLEEP", "false").lower() in ("true", "1", "yes")


def get_bot_mode() -> str:
    return os.environ.get("BOT_MODE", "simulated").lower()
