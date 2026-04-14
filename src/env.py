"""Load .env file and provide typed access to environment settings."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Walk up from this file (src/env.py) to the project root.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ENV_PATH = _PROJECT_ROOT / ".env"


def load_env() -> None:
    """Load the .env file if it exists.  Already-set env vars take precedence."""
    load_dotenv(_ENV_PATH, override=False)


def get_thegraph_api_key() -> str:
    return os.environ.get("THEGRAPH_API_KEY", "")


def get_rpc_overrides() -> dict[str, str]:
    """Return any custom RPC URLs from the environment.

    Supports all 12 chains. Format: RPC_{CHAIN_UPPER}=https://...
    """
    overrides: dict[str, str] = {}
    for chain, env_var in (
        ("ethereum", "RPC_ETHEREUM"),
        ("arbitrum", "RPC_ARBITRUM"),
        ("base", "RPC_BASE"),
        ("bsc", "RPC_BSC"),
        ("polygon", "RPC_POLYGON"),
        ("optimism", "RPC_OPTIMISM"),
        ("avax", "RPC_AVAX"),
        ("fantom", "RPC_FANTOM"),
        ("linea", "RPC_LINEA"),
        ("scroll", "RPC_SCROLL"),
        ("zksync", "RPC_ZKSYNC"),
        ("gnosis", "RPC_GNOSIS"),
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
