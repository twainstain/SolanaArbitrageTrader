"""On-chain contract addresses and minimal ABIs for DEX quoters.

Only the read-only quoting functions are included — no state-changing calls.
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
