"""On-chain pool discovery via factory contracts.

Queries Uniswap V3 and Solidly (Velodrome/Aerodrome) factory contracts to
resolve pool addresses for configured trading pairs.

WHY THIS EXISTS:
  Pool addresses are deterministic (CREATE2) but not hardcoded in our config.
  Instead of manually looking up every pool address for every pair on every
  chain, we call the factory's ``getPool(tokenA, tokenB, fee)`` at startup to
  discover them automatically.

WHEN IT RUNS:
  Once at startup via ``discover_and_persist_pools()``.  Pool addresses are
  immutable (they never change once deployed), so re-running is unnecessary.
  Discovered pools are persisted to DB — subsequent startups load from cache.

WHAT IT DISCOVERS:
  - Uniswap V3 pools across 3 fee tiers (500, 3000, 10000 = 0.05%, 0.30%, 1%)
  - Solidly-fork pools (Velodrome on Optimism, Aerodrome on Base) in both
    volatile (20 bps) and stable (2 bps) variants
  - Tries bridged USDC (USDC.e / USDbC) as fallback if native USDC pool
    doesn't exist (common on L2s where liquidity migrated from bridged to native)

FLOW:
  For each (pair, chain):
    1. Resolve token addresses from the token registry
    2. Call factory.getPool() for each fee tier / pool type
    3. Filter out zero addresses (pool doesn't exist for that fee tier)
    4. Persist to pools table via save_pool_if_missing() (skips duplicates)
"""

from __future__ import annotations

import logging
from decimal import Decimal

from web3 import Web3

from core.config import PairConfig
from core.contracts import PUBLIC_RPC_URLS, VELO_FACTORY
from core.tokens import bridged_usdc_address, resolve_token_address

logger = logging.getLogger(__name__)

ZERO_ADDRESS = "0x" + "0" * 40

# Uniswap V3 factory addresses per chain.
UNISWAP_V3_FACTORY: dict[str, str] = {
    "ethereum": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
    "arbitrum": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
    "optimism": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
    "base": "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
    "polygon": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
    "avax": "0x740b1c1de25031C31FF4fC9A62f554A55cdC1baD",
}

UNISWAP_V3_FACTORY_ABI = [
    {
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
            {"name": "fee", "type": "uint24"},
        ],
        "name": "getPool",
        "outputs": [{"name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

SOLIDLY_FACTORY_ABI = [
    {
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
            {"name": "stable", "type": "bool"},
        ],
        "name": "getPool",
        "outputs": [{"name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

# Uniswap V3 fee tiers to probe.  Each fee tier has a separate pool contract.
# 500  = 0.05% (correlated pairs like USDC/USDT — tight spread, high volume)
# 3000 = 0.30% (standard pairs like WETH/USDC — most liquidity lives here)
# 10000 = 1.00% (exotic/long-tail pairs — wide spread, low volume)
# We skip 100 (0.01%) because it's only used by stable-stable and rarely
# has arb opportunities worth the gas.
V3_FEE_TIERS = (500, 3000, 10000)


def discover_uniswap_v3_pools(
    w3: Web3, chain: str, token_a: str, token_b: str,
) -> list[dict]:
    """Discover Uniswap V3 pool addresses via the factory contract."""
    factory_addr = UNISWAP_V3_FACTORY.get(chain)
    if not factory_addr:
        return []

    factory = w3.eth.contract(
        address=Web3.to_checksum_address(factory_addr),
        abi=UNISWAP_V3_FACTORY_ABI,
    )
    results = []
    for fee in V3_FEE_TIERS:
        try:
            pool = factory.functions.getPool(
                Web3.to_checksum_address(token_a),
                Web3.to_checksum_address(token_b),
                fee,
            ).call()
            if pool and pool != ZERO_ADDRESS:
                results.append({
                    "address": pool,
                    "dex": "uniswap_v3",
                    "dex_type": "uniswap_v3",
                    "fee_tier_bps": Decimal(fee) / Decimal("100"),
                })
        except Exception as exc:
            logger.debug("V3 factory lookup failed for fee=%d on %s: %s", fee, chain, exc)
    return results


def discover_solidly_pools(
    w3: Web3, chain: str, token_a: str, token_b: str, dex_type: str = "velodrome_v2",
) -> list[dict]:
    """Discover Solidly-fork pool addresses via the factory contract.

    Tries both stable and volatile pool types, and also tries bridged USDC
    if native USDC discovery returns zero address.
    """
    factory_addr = VELO_FACTORY.get(chain)
    if not factory_addr:
        return []

    factory = w3.eth.contract(
        address=Web3.to_checksum_address(factory_addr),
        abi=SOLIDLY_FACTORY_ABI,
    )

    def _try_pair(a: str, b: str) -> list[dict]:
        pools = []
        for stable in [False, True]:
            try:
                pool = factory.functions.getPool(
                    Web3.to_checksum_address(a),
                    Web3.to_checksum_address(b),
                    stable,
                ).call()
                if pool and pool != ZERO_ADDRESS:
                    label = "stable" if stable else "volatile"
                    pools.append({
                        "address": pool,
                        "dex": dex_type,
                        "dex_type": dex_type,
                        "fee_tier_bps": Decimal("2") if stable else Decimal("20"),
                        "liquidity_class": label,
                    })
            except Exception as exc:
                logger.debug("Solidly factory lookup failed stable=%s on %s: %s", stable, chain, exc)
        return pools

    results = _try_pair(token_a, token_b)

    # Try bridged USDC if no pools found with native.
    if not results:
        bridged = bridged_usdc_address(chain)
        if bridged:
            alt_a = bridged if token_a.lower() != bridged.lower() else token_a
            alt_b = bridged if token_b.lower() != bridged.lower() else token_b
            if alt_a != token_a or alt_b != token_b:
                results = _try_pair(
                    alt_a if token_a.lower() != bridged.lower() else token_a,
                    alt_b if token_b.lower() != bridged.lower() else token_b,
                )

    return results


def discover_and_persist_pools(
    repo: "Repository",
    chains: list[str],
    pairs: list[PairConfig],
    rpc_overrides: dict[str, str] | None = None,
) -> int:
    """Discover pools for all configured pairs and persist to DB.

    This is the main entry point, called once at startup from
    run_event_driven.py.  For each pair on each chain it:
      1. Resolves token addresses (WETH → 0xC02a... on ethereum)
      2. Ensures the pair exists in the pairs table
      3. Calls discover_uniswap_v3_pools() for V3 pools
      4. Calls discover_solidly_pools() for Velodrome/Aerodrome pools
      5. Persists new pools via save_pool_if_missing() (idempotent)

    Returns the number of newly inserted pools (0 on subsequent startups
    if all pools were already discovered).
    """
    from persistence.repository import Repository as _Repo

    rpc_overrides = rpc_overrides or {}
    inserted = 0

    # Build web3 instances per chain.
    w3_map: dict[str, Web3] = {}
    for chain in chains:
        url = rpc_overrides.get(chain, PUBLIC_RPC_URLS.get(chain, ""))
        if url:
            w3_map[chain] = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 8}))

    for pair_cfg in pairs:
        pair_chains = [pair_cfg.chain] if pair_cfg.chain else chains
        for chain in pair_chains:
            w3 = w3_map.get(chain)
            if not w3:
                continue

            base_addr = resolve_token_address(chain, pair_cfg.base_asset)
            quote_addr = resolve_token_address(chain, pair_cfg.quote_asset)
            if not base_addr or not quote_addr:
                continue

            # Ensure pair exists in DB.
            pair_row = repo.get_pair_on_chain(pair_cfg.pair, chain)
            if pair_row is None:
                from core.tokens import token_decimals
                repo.save_pair(
                    pair=pair_cfg.pair,
                    chain=chain,
                    base_token=pair_cfg.base_asset,
                    quote_token=pair_cfg.quote_asset,
                    base_decimals=token_decimals(pair_cfg.base_asset),
                    quote_decimals=token_decimals(pair_cfg.quote_asset),
                )
                pair_row = repo.get_pair_on_chain(pair_cfg.pair, chain)
            if pair_row is None:
                continue
            pair_id = pair_row["pair_id"]

            # Discover V3 pools.
            v3_pools = discover_uniswap_v3_pools(w3, chain, base_addr, quote_addr)
            for pool in v3_pools:
                created = repo.save_pool_if_missing(
                    pair_id=pair_id,
                    chain=chain,
                    dex=pool["dex"],
                    address=pool["address"],
                    fee_tier_bps=pool["fee_tier_bps"],
                    dex_type=pool["dex_type"],
                    liquidity_class="factory_discovered",
                )
                if created is not None:
                    inserted += 1
                    logger.info(
                        "Factory discovered V3 pool: %s on %s — %s (fee=%s bps)",
                        pair_cfg.pair, chain, pool["address"], pool["fee_tier_bps"],
                    )

            # Discover Solidly pools (Velodrome/Aerodrome).
            if VELO_FACTORY.get(chain):
                dex_type = "aerodrome" if chain == "base" else "velodrome_v2"
                solidly_pools = discover_solidly_pools(w3, chain, base_addr, quote_addr, dex_type)
                for pool in solidly_pools:
                    created = repo.save_pool_if_missing(
                        pair_id=pair_id,
                        chain=chain,
                        dex=pool["dex"],
                        address=pool["address"],
                        fee_tier_bps=pool["fee_tier_bps"],
                        dex_type=pool["dex_type"],
                        liquidity_class=pool.get("liquidity_class", "factory_discovered"),
                    )
                    if created is not None:
                        inserted += 1
                        logger.info(
                            "Factory discovered Solidly pool: %s on %s — %s (%s)",
                            pair_cfg.pair, chain, pool["address"],
                            pool.get("liquidity_class", "?"),
                        )

    logger.info("Factory pool discovery complete: %d new pools", inserted)
    return inserted
