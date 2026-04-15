"""Wallet balance helper — fetches on-chain ETH balances for the bot wallet."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

from core.models import SUPPORTED_CHAINS

CHAINS = SUPPORTED_CHAINS


def get_wallet_balances() -> dict:
    """Fetch wallet ETH balances across chains.

    Returns {"address": "0x...", "balances": {"arbitrum": 0.011, ...}}.
    Chains that fail to query return None.
    """
    from web3 import Web3
    from core.contracts import PUBLIC_RPC_URLS
    from core.env import get_rpc_overrides

    private_key = os.environ.get("EXECUTOR_PRIVATE_KEY", "")
    if not private_key:
        return {"address": "", "balances": {}}

    try:
        account = Web3().eth.account.from_key(private_key)
        address = account.address
    except Exception:
        return {"address": "", "balances": {}}

    rpc_overrides = get_rpc_overrides()
    balances: dict[str, float | None] = {}
    for chain in CHAINS:
        rpc_url = rpc_overrides.get(chain, PUBLIC_RPC_URLS.get(chain, ""))
        if not rpc_url:
            continue
        try:
            w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 5}))
            bal_wei = w3.eth.get_balance(address)
            balances[chain] = float(bal_wei) / 1e18
        except Exception as exc:
            logger.debug("Wallet balance fetch failed for %s: %s", chain, exc)
            balances[chain] = None

    return {"address": address, "balances": balances}
