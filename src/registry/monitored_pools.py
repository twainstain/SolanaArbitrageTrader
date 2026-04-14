"""Known monitored pools and bootstrap sync for repository metadata."""

from __future__ import annotations

import logging
from decimal import Decimal

from persistence.repository import Repository
from tokens import token_decimals

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
    },
    "base": {
        "WETH/USDC": [
            "0xd0b53D9277642d899DF5C87A3966A349A798F224",
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

            pair_row = repo.get_pair(pair_name)
            if pair_row is None:
                pair_id = repo.save_pair(
                    pair=pair_name,
                    chain=chain,
                    base_token=base_asset,
                    quote_token=quote_asset,
                    base_decimals=token_decimals(base_asset),
                    quote_decimals=token_decimals(quote_asset),
                    max_trade_size=Decimal("10"),
                )
                pair_row = repo.get_pair(pair_name)
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
