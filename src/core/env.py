"""Load .env file and provide typed access to environment settings."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _PROJECT_ROOT / ".env"


def load_env() -> None:
    """Load the .env file if it exists.  Already-set env vars take precedence."""
    load_dotenv(_ENV_PATH, override=False)


# ---------------------------------------------------------------------------
# Solana RPC endpoints
# ---------------------------------------------------------------------------

def get_solana_rpc_urls() -> list[str]:
    """Return Solana RPC URLs in priority order.

    Collects ``SOLANA_RPC_URL`` first, then ``SOLANA_RPC_URL_2``,
    ``SOLANA_RPC_URL_3``, ... so operators can configure fallbacks
    (e.g. Helius primary, Triton secondary, public mainnet-beta last).
    """
    urls: list[str] = []
    primary = os.environ.get("SOLANA_RPC_URL", "")
    if primary:
        urls.append(primary)
    for i in range(2, 10):
        url = os.environ.get(f"SOLANA_RPC_URL_{i}", "")
        if url:
            urls.append(url)
    return urls


def get_helius_api_key() -> str:
    return os.environ.get("HELIUS_API_KEY", "")


def get_jupiter_api_url() -> str:
    """Return the Jupiter quote API base URL.

    Default: public ``lite-api.jup.ag/swap/v1`` (free tier, rate-limited).
    For the paid/pro tier override with ``JUPITER_API_URL=https://api.jup.ag/swap/v1``.

    The legacy ``quote-api.jup.ag/v6`` was deprecated — DNS doesn't resolve
    any more.  Do not use it.
    """
    return os.environ.get("JUPITER_API_URL", "https://lite-api.jup.ag/swap/v1")


# ---------------------------------------------------------------------------
# Bot run parameters
# ---------------------------------------------------------------------------

def get_bot_config_path() -> str:
    return os.environ.get("BOT_CONFIG", "config/example_config.json")


def get_bot_iterations() -> int:
    return int(os.environ.get("BOT_ITERATIONS", "10"))


def get_bot_dry_run() -> bool:
    return os.environ.get("BOT_DRY_RUN", "false").lower() in ("true", "1", "yes")


def get_bot_no_sleep() -> bool:
    return os.environ.get("BOT_NO_SLEEP", "false").lower() in ("true", "1", "yes")


def get_bot_mode() -> str:
    """Active market source.  Allowed values: 'simulated' | 'jupiter'."""
    return os.environ.get("BOT_MODE", "simulated").lower()
