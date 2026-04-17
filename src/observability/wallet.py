"""Wallet balance helper — fetches Solana balances via RPC ``getBalance``.

Returns SOL and token balances for the bot wallet.  Silent no-op when no
wallet key is configured (normal in scanner-phase).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from core.env import get_solana_rpc_urls

logger = logging.getLogger(__name__)

LAMPORTS_PER_SOL = 10 ** 9


def _first_rpc() -> str:
    urls = get_solana_rpc_urls()
    return urls[0] if urls else "https://api.mainnet-beta.solana.com"


def _rpc_call(url: str, method: str, params: list) -> Any | None:
    try:
        resp = requests.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=3.0,
        )
        resp.raise_for_status()
        return resp.json().get("result")
    except Exception as exc:
        logger.debug("[wallet] %s failed: %s", method, exc)
        return None


def get_wallet_balances() -> dict:
    """Return the bot wallet's native SOL balance.

    Returns {"address": "base58", "balances": {"SOL": 0.5}}.  When no wallet
    pubkey is configured (scanner-phase default) the balances dict is empty.
    The 'balances' dict is preserved (rather than returning just a float)
    so alerting/dashboard code can be extended later to include SPL token
    balances (USDC, USDT) without breaking the shape.
    """
    pubkey = os.environ.get("SOLANA_WALLET_PUBKEY", "")
    if not pubkey:
        return {"address": "", "balances": {}}

    url = _first_rpc()
    balances: dict[str, float | None] = {}
    result = _rpc_call(url, "getBalance", [pubkey])
    if result is None:
        balances["SOL"] = None
    else:
        # Solana RPC returns {"value": lamports, "context": {...}}
        lamports = result.get("value", 0) if isinstance(result, dict) else result
        balances["SOL"] = lamports / LAMPORTS_PER_SOL
    return {"address": pubkey, "balances": balances}
