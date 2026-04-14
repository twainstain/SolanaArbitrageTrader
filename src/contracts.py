"""On-chain contract addresses, minimal ABIs, and public RPC URLs for DEX quoters.

This module is the contract-level counterpart to ``tokens.py``.  While
``tokens.py`` maps *token* addresses, this file maps *DEX contract* addresses
(quoters, vaults) and provides the ABI fragments needed to call their
read-only quoting functions.  No state-changing call ABIs are included --
execution ABIs live in ``chain_executor.py`` and the Solidity contracts under
``contracts/``.

Contract registry structure
---------------------------
Each DEX family has:

1. **A per-chain address dict** (e.g. ``UNISWAP_V3_QUOTER_PER_CHAIN``) that
   maps chain names to the deployed QuoterV2 address on that chain.
2. **A minimal ABI list** containing only the ``quoteExactInputSingle``
   function signature needed for read-only price quotes.
3. Forks that share the same interface (SushiSwap V3, PancakeSwap V3) reuse
   the Uniswap V3 ABI directly (``SUSHI_V3_QUOTER_ABI = UNISWAP_V3_QUOTER_ABI``).

QuoterV2 addresses -- why they differ per chain
------------------------------------------------
Despite Uniswap using CREATE2 deterministic deployment, the QuoterV2 address
is **not** identical on every chain.  The canonical address
``0x61fFE014...`` works on Ethereum, Arbitrum, Polygon, and Optimism, but
Base, BSC, and Avalanche were deployed with different factory nonces or by
different deployer wallets, producing different addresses.  SushiSwap and
PancakeSwap are entirely separate deployments and have their own addresses.
QuickSwap on Polygon uses the Algebra protocol (not Uniswap V3), so its
quoter has a different ABI altogether (no ``fee`` parameter -- Algebra uses
dynamic fees determined by the pool).

Fee tiers
---------
``UNISWAP_FEE_TIERS`` enumerates the standard Uniswap V3 fee tiers in
hundredths of a basis point (the ``fee`` parameter in pool contracts):

* 100  -> 0.01 % (1 bps)   -- stable-stable pairs (USDC/USDT)
* 500  -> 0.05 % (5 bps)   -- correlated pairs (WETH/stETH)
* 3000 -> 0.30 % (30 bps)  -- standard pairs (WETH/USDC)
* 10000 -> 1.00 % (100 bps) -- exotic / long-tail pairs

PancakeSwap V3 supports 100, 500, 2500, and 10000.  SushiSwap V3 mirrors
Uniswap's tiers.  QuickSwap (Algebra) determines fees dynamically per pool.

Backup / public RPC URLs
-------------------------
``PUBLIC_RPC_URLS`` provides free-tier, rate-limited RPC endpoints for every
supported chain.  These are used as fallbacks when no ``RPC_<CHAIN>`` override
is set in ``.env``.  They are adequate for development and occasional live
scanning but **not** for production execution -- latency and rate limits will
cause missed opportunities.  For production, set per-chain RPC overrides via
environment variables pointing to Alchemy / Infura / QuickNode endpoints.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Uniswap V3 QuoterV2
# QuoterV2 addresses per chain. NOT the same everywhere despite CREATE2.
# ---------------------------------------------------------------------------

UNISWAP_V3_QUOTER_V2 = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"  # default

UNISWAP_V3_QUOTER_PER_CHAIN: dict[str, str] = {
    "ethereum": "0x61fFE014bA17989E743c5F6cB21bF9697530B21e",
    "arbitrum": "0x61fFE014bA17989E743c5F6cB21bF9697530B21e",
    "polygon":  "0x61fFE014bA17989E743c5F6cB21bF9697530B21e",
    "optimism": "0x61fFE014bA17989E743c5F6cB21bF9697530B21e",
    "base":     "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a",
    "bsc":      "0x78D78E420Da98ad378D7799bE8f4AF69033EB077",
    "avax":     "0xbe0F5544EC67e9B3b2D979aaA43f18Fd87E6257F",
}

UNISWAP_V3_QUOTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "tokenIn", "type": "address"},
                    {"name": "tokenOut", "type": "address"},
                    {"name": "amountIn", "type": "uint256"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"name": "amountOut", "type": "uint256"},
            {"name": "sqrtPriceX96After", "type": "uint160"},
            {"name": "initializedTicksCrossed", "type": "uint32"},
            {"name": "gasEstimate", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

# Common Uniswap V3 fee tiers in hundredths of a bip (1 = 0.01 bps).
UNISWAP_FEE_TIERS = {
    "100": 1,       # 0.01%  (1 bps)
    "500": 5,       # 0.05%  (5 bps)
    "3000": 30,     # 0.30%  (30 bps)
    "10000": 100,   # 1.00%  (100 bps)
}

# ---------------------------------------------------------------------------
# SushiSwap V3 — uses the same QuoterV2 interface (Uniswap V3 fork).
# Addresses differ per chain.
# ---------------------------------------------------------------------------

SUSHI_V3_QUOTER: dict[str, str] = {
    "ethereum": "0x64e8802FE490fa7cc61d3463958199161Bb608A7",
    "arbitrum": "0x0524E833cCD057e4d7A296e3aaAb9f7675964Ce1",
    "base": "0xb1E835Dc2785b52265711e17fCCb0fd018226a6e",
    "polygon": "0xb1E835Dc2785b52265711e17fCCb0fd018226a6e",
    "optimism": "0xb1E835Dc2785b52265711e17fCCb0fd018226a6e",
    "avax": "0xb1E835Dc2785b52265711e17fCCb0fd018226a6e",
}

# Sushi V3 uses the same ABI as Uniswap V3 QuoterV2.
SUSHI_V3_QUOTER_ABI = UNISWAP_V3_QUOTER_ABI

# ---------------------------------------------------------------------------
# Balancer V2 Vault — queryBatchSwap (read-only).
# Single canonical Vault address on all EVM chains.
# ---------------------------------------------------------------------------

BALANCER_VAULT = "0xBA12222222228d8Ba445958a75a0704d566BF2C8"

BALANCER_VAULT_ABI = [
    {
        "inputs": [
            {"name": "kind", "type": "uint8"},
            {
                "components": [
                    {"name": "poolId", "type": "bytes32"},
                    {"name": "assetInIndex", "type": "uint256"},
                    {"name": "assetOutIndex", "type": "uint256"},
                    {"name": "amount", "type": "uint256"},
                    {"name": "userData", "type": "bytes"},
                ],
                "name": "swaps",
                "type": "tuple[]",
            },
            {"name": "assets", "type": "address[]"},
            {
                "components": [
                    {"name": "sender", "type": "address"},
                    {"name": "fromInternalBalance", "type": "bool"},
                    {"name": "recipient", "type": "address"},
                    {"name": "toInternalBalance", "type": "bool"},
                ],
                "name": "funds",
                "type": "tuple",
            },
        ],
        "name": "queryBatchSwap",
        "outputs": [{"name": "", "type": "int256[]"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

# ---------------------------------------------------------------------------
# PancakeSwap V3 — Uniswap V3 fork, same QuoterV2 interface.
# Primary chain is BSC; also deployed on Ethereum, Base, Arbitrum.
# The video explicitly uses PancakeSwap as the second DEX alongside Uniswap.
# ---------------------------------------------------------------------------

PANCAKE_V3_QUOTER: dict[str, str] = {
    "bsc": "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997",
    "ethereum": "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997",
    "arbitrum": "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997",
    "base": "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997",
    "polygon": "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997",
}

# PancakeSwap V3 uses the same ABI as Uniswap V3 QuoterV2.
PANCAKE_V3_QUOTER_ABI = UNISWAP_V3_QUOTER_ABI

# ---------------------------------------------------------------------------
# QuickSwap V3 (Algebra) — different quoter interface, no fee parameter.
# Deployed on Polygon.
# ---------------------------------------------------------------------------

QUICKSWAP_QUOTER: dict[str, str] = {
    "polygon": "0xa15F0D7377B2A0C0c10db057f641beD21028FC89",
}

# Camelot V3 (Arbitrum) — Algebra protocol, same interface as QuickSwap.
CAMELOT_QUOTER: dict[str, str] = {
    "arbitrum": "0x0Fc73040b26E9bC8514fA028D998E73A254Fa76E",
}

# Velodrome V2 (Optimism) + Aerodrome (Base) — Solidly-fork routers.
VELODROME_ROUTER: dict[str, str] = {
    "optimism": "0xa062aE8A9c5e11aaA026fc2670B0D65cCc8B2858",
}
AERODROME_ROUTER: dict[str, str] = {
    "base": "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43",
}

# Velodrome/Aerodrome use getAmountsOut with a Route struct.
# Route: (address from, address to, bool stable, address factory)
VELO_ROUTER_ABI = [
    {
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {
                "name": "routes",
                "type": "tuple[]",
                "components": [
                    {"name": "from", "type": "address"},
                    {"name": "to", "type": "address"},
                    {"name": "stable", "type": "bool"},
                    {"name": "factory", "type": "address"},
                ],
            },
        ],
        "name": "getAmountsOut",
        "outputs": [
            {"name": "amounts", "type": "uint256[]"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

# Default pool factories for route construction.
VELO_FACTORY: dict[str, str] = {
    "optimism": "0x420DD381b31aEf6683db6B902084cB0FFECe40Da",  # Velodrome V2
    "base": "0x420DD381b31aEf6683db6B902084cB0FFECe40Da",      # Aerodrome
}

# Algebra V2 quoter ABI — struct-based params (used by newer Camelot deployments).
ALGEBRA_V2_QUOTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "tokenIn", "type": "address"},
                    {"name": "tokenOut", "type": "address"},
                    {"name": "amountIn", "type": "uint256"},
                    {"name": "limitSqrtPrice", "type": "uint160"},
                ],
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"name": "amountOut", "type": "uint256"},
            {"name": "fee", "type": "uint16"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

# Algebra V1 quoter ABI — individual args (QuickSwap, older Camelot).
QUICKSWAP_QUOTER_ABI = [
    {
        "inputs": [
            {"name": "tokenIn", "type": "address"},
            {"name": "tokenOut", "type": "address"},
            {"name": "amountIn", "type": "uint256"},
            {"name": "limitSqrtPrice", "type": "uint160"},
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"name": "amountOut", "type": "uint256"},
            {"name": "fee", "type": "uint16"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

# ---------------------------------------------------------------------------
# Public RPC endpoints (free tier, rate-limited).
# For production, use Alchemy / Infura / QuickNode with an API key.
# ---------------------------------------------------------------------------

PUBLIC_RPC_URLS: dict[str, str] = {
    # Top 10 EVM chains by DeFi Llama TVL.
    "ethereum": "https://eth.llamarpc.com",
    "arbitrum": "https://arb1.arbitrum.io/rpc",
    "base": "https://mainnet.base.org",
    "bsc": "https://bsc-dataseed.binance.org",
    "polygon": "https://1rpc.io/matic",
    "optimism": "https://mainnet.optimism.io",
    "avax": "https://api.avax.network/ext/bc/C/rpc",
    "fantom": "https://rpcapi.fantom.network",
    "linea": "https://rpc.linea.build",
    "scroll": "https://rpc.scroll.io",
    "zksync": "https://mainnet.era.zksync.io",
    "gnosis": "https://rpc.gnosischain.com",
}

# ---------------------------------------------------------------------------
# Well-known Balancer pool IDs for WETH/USDC (optional, per-chain).
# These are used by OnChainMarket when querying Balancer.
# ---------------------------------------------------------------------------

BALANCER_POOL_IDS: dict[str, str] = {
    # Balancer V2 WETH/USDC weighted pool on Ethereum mainnet
    "ethereum": "0x96646936b91d6b9d7d0c47c496afbf3d6ec7b6f8000200000000000000000019",
    # Balancer V2 WETH/USDC on Arbitrum
    "arbitrum": "0x64541216bafffeec8ea535bb71fbc927831d0595000100000000000000000002",
}
