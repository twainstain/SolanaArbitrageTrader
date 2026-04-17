"""Subgraph IDs, pool addresses, and GraphQL queries for The Graph.

Requires a free API key from https://thegraph.com/studio/apikeys/
Set via the THEGRAPH_API_KEY environment variable.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Subgraph IDs (decentralized network)
# ---------------------------------------------------------------------------

UNISWAP_V3_SUBGRAPH: dict[str, str] = {
    "ethereum": "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
    "base": "FUbEPQw1oMghy39fwWBFY5fE6MXPXZQtjncQy2cXdrNS",
    "arbitrum": "FQ6JYszEKApsBpAmiHesRsd9Ygc6mzmpNRANeVQFYoVX",
}

SUSHI_V3_SUBGRAPH: dict[str, str] = {
    "ethereum": "2tGWMrDha4164KkFAfkU3rDCtuxGb4q1emXmFdLLzJ8x",
}

BALANCER_V2_SUBGRAPH: dict[str, str] = {
    "ethereum": "C4ayEZP2yTXRAB8vSaTrgN4m9anTe9Mdm2ViyiAuV9TV",
    "arbitrum": "98cQDy6tufTJtshDCuhh9z2kWXsQWBHVh2bqnLHsGAeS",
}

# ---------------------------------------------------------------------------
# Well-known WETH/USDC pool addresses per DEX + chain
# ---------------------------------------------------------------------------

UNISWAP_V3_POOLS: dict[str, str] = {
    "ethereum": "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",  # 0.05% tier
    "arbitrum": "0xc6962004f452be9203591991d15f6b388e09e8d0",  # 0.05%
    "base": "0xd0b53d9277642d899df5c87a3966a349a798f224",      # 0.05%
}

SUSHI_V3_POOLS: dict[str, str] = {
    "ethereum": "0x35644fb61afbc458bf92b15add6abc1996be5014",
}

# ---------------------------------------------------------------------------
# Gateway URL
# ---------------------------------------------------------------------------

THEGRAPH_GATEWAY = "https://gateway.thegraph.com/api/{api_key}/subgraphs/id/{subgraph_id}"

# ---------------------------------------------------------------------------
# GraphQL queries
# ---------------------------------------------------------------------------

# Current pool state — returns token0Price/token1Price (pre-computed by indexer).
POOL_CURRENT_QUERY = """
query PoolCurrent($poolId: ID!) {
  pool(id: $poolId) {
    id
    token0 { symbol id decimals }
    token1 { symbol id decimals }
    feeTier
    token0Price
    token1Price
    liquidity
    totalValueLockedUSD
  }
}
"""

# Hourly snapshots for historical download.
# Uniswap V3 subgraph indexes `poolHourDatas`.
POOL_HOUR_DATA_QUERY = """
query PoolHourData($poolId: String!, $startTime: Int!, $endTime: Int!, $skip: Int!) {
  poolHourDatas(
    first: 1000
    skip: $skip
    orderBy: periodStartUnix
    orderDirection: asc
    where: { pool: $poolId, periodStartUnix_gte: $startTime, periodStartUnix_lte: $endTime }
  ) {
    periodStartUnix
    open
    high
    low
    close
    liquidity
    volumeUSD
    token0Price
    token1Price
  }
}
"""

# Messari-standardized hourly snapshots (used by Sushi V3, Uniswap V3 Arbitrum/Base).
MESSARI_POOL_HOURLY_QUERY = """
query MessariPoolHourly($poolId: String!, $startTime: Int!, $endTime: Int!, $skip: Int!) {
  liquidityPoolHourlySnapshots(
    first: 1000
    skip: $skip
    orderBy: timestamp
    orderDirection: asc
    where: { pool: $poolId, timestamp_gte: $startTime, timestamp_lte: $endTime }
  ) {
    timestamp
    tick
    hourlyVolumeUSD
    totalValueLockedUSD
    inputTokenBalances
    inputTokenBalancesUSD
    pool {
      inputTokens { symbol decimals }
    }
  }
}
"""

# Which subgraphs use Messari schema vs Uniswap-native schema.
# Uniswap V3 Ethereum uses native `poolHourDatas`; all others use Messari.
NATIVE_SCHEMA_SUBGRAPHS = {
    ("uniswap_v3", "ethereum"),
}

# Balancer pool tokens query for price derivation.
BALANCER_POOL_QUERY = """
query BalancerPool($poolId: ID!) {
  pool(id: $poolId) {
    id
    address
    poolType
    swapFee
    totalLiquidity
    tokens {
      symbol
      address
      balance
      decimals
      weight
    }
  }
}
"""

# Balancer historical snapshots.
BALANCER_POOL_SNAPSHOTS_QUERY = """
query BalancerSnapshots($poolId: String!, $startTime: Int!, $endTime: Int!, $skip: Int!) {
  poolSnapshots(
    first: 1000
    skip: $skip
    orderBy: timestamp
    orderDirection: asc
    where: { pool: $poolId, timestamp_gte: $startTime, timestamp_lte: $endTime }
  ) {
    timestamp
    amounts
    totalShares
    swapVolume
    swapFees
    pool {
      tokens {
        symbol
        address
        balance
        weight
      }
    }
  }
}
"""
