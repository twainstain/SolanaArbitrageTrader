"""Known monitored pools — hardcoded high-liquidity pool addresses.

WHY THIS EXISTS:
  The event-driven scanner listens for Swap events on specific pool addresses
  to trigger rescan cycles.  These addresses must be known at startup before
  any discovery or DexScreener queries run.  This file provides a hardcoded
  bootstrap set of the highest-liquidity pools on each chain.

vs. pool_discovery.py:
  pool_discovery.py dynamically queries factory contracts to find pools.
  This file provides a static fallback that works even if RPC is down.
  Both are persisted to the same `pools` table — sync_monitored_pools()
  is idempotent (save_pool_if_missing skips duplicates).

WHAT sync_monitored_pools() DOES:
  For each pool in MONITORED_POOLS:
    1. Ensure the pair exists in the pairs table
    2. Insert the pool if not already present (idempotent)
  Called once at startup from run_event_driven.py.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from persistence.repository import Repository
from core.tokens import token_decimals

logger = logging.getLogger(__name__)


# Well-known pools to monitor for swap-driven rescans.
MONITORED_POOLS: dict[str, dict[str, list[str]]] = {
    "ethereum": {
        "WETH/USDC": [
            "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640",
            "0x8ad599c3A0ff1De082011EFDDc58f1908eb6e6D8",
        ],
        "WETH/USDT": [
            "0x11b815efB8f581194ae5486326A680323dB6E0aA",
            "0x4e68Ccd3E89f51C3074ca5072bbAC773960dFa36",
        ],
        "WBTC/USDC": [
            "0x99ac8cA7087fA4A2A1FB6357269965A2014ABc35",
        ],
    },
    "bsc": {
        "WETH/USDC": [
            "0x36696169C63e42cd08ce11f5deeBbCeBae652050",
        ],
    },
    "arbitrum": {
        "WETH/USDC": [
            "0xC6962004f452bE9203591991D15f6b388e09E8D0",
        ],
        "WETH/USDT": [
            "0xC31E54c7a869B9FcBEcc14363CF510d1c41fa443",
        ],
        "cbETH/WETH": [
            "0x2C936Dd9D2D7c58C565F70F2320E96Ad8f7eF8C2",  # Uniswap V3 0.05%
        ],
    },
    "base": {
        "WETH/USDC": [
            "0xd0b53D9277642d899DF5C87A3966A349A798F224",
        ],
        "wstETH/WETH": [
            "0x6f4482cBF7b43599078fcb012732e20480015644",  # Uniswap V3 0.05%
            "0xA6385c73961dd9C58db2EF0c4EB98cE4B60651e8",  # Aerodrome volatile
        ],
        "cbETH/WETH": [
            "0x7B9636266734270DE5bE02544c04E27046903ff8",  # Uniswap V3 0.30%
            "0x44Ecc644449fC3a9858d2007CaA8CFAa4C561f91",  # Aerodrome volatile
        ],
        "AERO/WETH": [
            "0x9E88239ac8c225e4Fe63c72A4c7fc9D1c9Ef7e24",  # Uniswap V3 0.05%
            "0x7f670f78B17dEC44d5Ef68a48740b6f8849cc2e6",  # Aerodrome volatile
        ],
    },
    "optimism": {
        "WETH/USDC": [
            "0x85149247691df622eaF1a8Bd0CaFd40BC45154a9",  # Uniswap V3 0.05%
        ],
        "OP/USDC": [
            "0x1C3140aB59d6cAf9fa7459C6f83D4B52ba881d36",  # Uniswap V3 0.30%
        ],
        "WETH/USDT": [
            "0xc858A329Bf053BE78D6239C4A4343B8FbD21472b",  # Uniswap V3 0.05%
        ],
        "wstETH/WETH": [
            "0x4a5a2A152e985078e1A4AA9C3362c412B7dd0a86",  # Uniswap V3 0.05%
            "0x6dA98Bde0068d10DDD11b468b197eA97D96F96Bc",  # Velodrome volatile
        ],
    },
    "avax": {
        "WAVAX/USDC": [
            "0xfAe3f424a0a47706811521E3ee268f00cFb5c45E",  # Uniswap V3 0.05%
        ],
        "WAVAX/USDT": [
            "0x78b58A7E21b08f1FCeB8d6AE9a235ABB900b5716",  # Uniswap V3 0.05%
        ],
    },
}


def sync_monitored_pools(repo: Repository) -> int:
    """Persist the built-in monitored pools into pairs/pools tables.

    This gives production a DB-backed bootstrap source for event monitoring
    while still allowing the static map to serve as a code fallback.
    """
    inserted = 0
    for chain, pair_map in MONITORED_POOLS.items():
        for pair_name, addresses in pair_map.items():
            parts = pair_name.split("/", 1)
            if len(parts) != 2:
                continue
            base_asset, quote_asset = parts

            pair_row = repo.get_pair_on_chain(pair_name, chain)
            if pair_row is None:
                repo.save_pair(
                    pair=pair_name,
                    chain=chain,
                    base_token=base_asset,
                    quote_token=quote_asset,
                    base_decimals=token_decimals(base_asset),
                    quote_decimals=token_decimals(quote_asset),
                    max_trade_size=Decimal("10"),
                )
                pair_row = repo.get_pair_on_chain(pair_name, chain)
            if pair_row is None:
                continue

            pair_id = pair_row["pair_id"]
            for address in addresses:
                created = repo.save_pool_if_missing(
                    pair_id=pair_id,
                    chain=chain,
                    dex="monitored",
                    address=address,
                    fee_tier_bps=Decimal("0"),
                    dex_type="event_monitor",
                    liquidity_class="high",
                )
                if created is not None:
                    inserted += 1

    logger.info("Monitored pool bootstrap sync complete: %d new pools inserted", inserted)
    return inserted
