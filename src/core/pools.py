"""Pool registry — Raydium and Orca mainnet pool addresses.

Each entry maps a ``pair`` + ``venue`` to the on-chain account(s) needed to
compute a spot price without going through Jupiter.

Address provenance
------------------

Pool addresses here are well-known public mainnet pools.  Each is
documented with a Solscan link for verification.  If an address here is
wrong (deprecated pool, typo, etc.) the adapter will detect it at startup
via ``getAccountInfo`` and skip the pool with a warning rather than crash.

To add a new pool:

1. Find the pool on https://birdeye.so/ or https://solscan.io/
2. For Raydium AMM V4: copy the pool account address.  The adapter
   resolves base/quote vaults by reading the pool state on first use.
3. For Orca Whirlpool: copy the whirlpool account address.  The pool
   encodes ``sqrt_price`` directly — no separate vault reads needed.
4. Append a ``PoolRef`` here; no other code changes required.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Program IDs (owner on-chain of each kind of pool account).
# Used by the adapter to verify the account we read is actually the pool
# type we expect.  If a pool's ``owner`` doesn't match, we skip it.
# ---------------------------------------------------------------------------

RAYDIUM_AMM_V4_PROGRAM   = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
RAYDIUM_CPMM_PROGRAM     = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
ORCA_WHIRLPOOL_PROGRAM   = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
SPL_TOKEN_PROGRAM        = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


@dataclass(frozen=True)
class PoolRef:
    """Reference to one on-chain pool.

    ``base_vault``/``quote_vault`` are optional: if None the Raydium adapter
    resolves them from the pool state on first read.  Orca Whirlpool uses
    only ``address`` (pool account encodes sqrt_price directly) so those
    fields are ignored.
    """
    name: str                   # logical name (e.g. "Raydium-SOL/USDC")
    venue: str                  # must match a key in core.venues.VENUES
    pair: str                   # "SOL/USDC" etc.
    base_symbol: str            # "SOL"
    quote_symbol: str           # "USDC"
    address: str                # pool account pubkey
    program: str                # expected account owner program ID
    fee_bps: int                # pool fee tier for display
    base_vault: str | None = None
    quote_vault: str | None = None


# ---------------------------------------------------------------------------
# v1 pool list — a small curated set, verify before expanding.
# ---------------------------------------------------------------------------

POOLS: list[PoolRef] = [
    # Raydium AMM V4 — SOL / USDC
    # https://solscan.io/account/58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2
    PoolRef(
        name="Raydium-SOL/USDC",
        venue="Raydium",
        pair="SOL/USDC",
        base_symbol="SOL",
        quote_symbol="USDC",
        address="58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2",
        program=RAYDIUM_AMM_V4_PROGRAM,
        fee_bps=25,
    ),
    # Orca Whirlpool — SOL / USDC (0.05% tier, tick spacing 8)
    # Verified on-chain 2026-04-16: fee_rate=500, tick_spacing=8, mint_a=WSOL, mint_b=USDC.
    # https://solscan.io/account/7qbRF6YsyGuLUVs6Y1q64bdVrfe4ZcUUz1JRdoVNUJnm
    PoolRef(
        name="Orca-SOL/USDC",
        venue="Orca",
        pair="SOL/USDC",
        base_symbol="SOL",
        quote_symbol="USDC",
        address="7qbRF6YsyGuLUVs6Y1q64bdVrfe4ZcUUz1JRdoVNUJnm",
        program=ORCA_WHIRLPOOL_PROGRAM,
        fee_bps=5,
    ),
    # Orca Whirlpool — USDC / USDT (1bp tier)
    # https://solscan.io/account/4fuUiYxTQ6QCrdSq9ouBYcTM7bqSwYTSyLueGZLTy4T4
    PoolRef(
        name="Orca-USDC/USDT",
        venue="Orca",
        pair="USDC/USDT",
        base_symbol="USDC",
        quote_symbol="USDT",
        address="4fuUiYxTQ6QCrdSq9ouBYcTM7bqSwYTSyLueGZLTy4T4",
        program=ORCA_WHIRLPOOL_PROGRAM,
        fee_bps=1,
    ),
    # Orca Whirlpool — SOL / jitoSOL (1bp tier, ~$31M TVL as of 2026-04-17)
    # Deepest LST pool on Solana. Clean price-source for SOL-staking-discount arbs.
    # Pool ordering: tokenA=SOL, tokenB=jitoSOL.
    # https://solscan.io/account/Hp53XEtt4S8SvPCXarsLSdGfZBuUr5mMmZmX2DRNXQKp
    PoolRef(
        name="Orca-SOL/jitoSOL",
        venue="Orca",
        pair="SOL/jitoSOL",
        base_symbol="SOL",
        quote_symbol="jitoSOL",
        address="Hp53XEtt4S8SvPCXarsLSdGfZBuUr5mMmZmX2DRNXQKp",
        program=ORCA_WHIRLPOOL_PROGRAM,
        fee_bps=1,
    ),
    # Orca Whirlpool — SOL / mSOL (1bp tier, ~$312k TVL as of 2026-04-17)
    # Below the 500k TVL guideline but still viable for the scanner: reading
    # a midpoint is fine, and 1-SOL trade-size price impact is ~0.03%. Gate
    # execution size via min_liquidity_usd in the strategy.
    # Pool ordering: tokenA=SOL, tokenB=mSOL.
    # https://solscan.io/account/HQcY5n2zP6rW74fyFEhWeBd3LnJpBcZechkvJpmdb8cx
    PoolRef(
        name="Orca-SOL/mSOL",
        venue="Orca",
        pair="SOL/mSOL",
        base_symbol="SOL",
        quote_symbol="mSOL",
        address="HQcY5n2zP6rW74fyFEhWeBd3LnJpBcZechkvJpmdb8cx",
        program=ORCA_WHIRLPOOL_PROGRAM,
        fee_bps=1,
    ),
    # Orca Whirlpool — mSOL / USDC (30bp tier, ~$100k TVL as of 2026-04-17)
    # Thin but the only meaningful direct on-chain mSOL/USDC venue. Scanner
    # midpoint is still informative even if un-executable; real trades on
    # this pair should go through Jupiter aggregation. Watch for mint_order
    # — pool orders tokenA=mSOL, tokenB=USDC.
    # https://solscan.io/account/AiMZS5U3JMvpdvsr1KeaMiS354Z1DeSg5XjA4yYRxtFf
    PoolRef(
        name="Orca-mSOL/USDC",
        venue="Orca",
        pair="mSOL/USDC",
        base_symbol="mSOL",
        quote_symbol="USDC",
        address="AiMZS5U3JMvpdvsr1KeaMiS354Z1DeSg5XjA4yYRxtFf",
        program=ORCA_WHIRLPOOL_PROGRAM,
        fee_bps=30,
    ),
]


def pools_for_pair(pair: str) -> list[PoolRef]:
    return [p for p in POOLS if p.pair == pair]


def pools_for_venue(venue: str) -> list[PoolRef]:
    return [p for p in POOLS if p.venue == venue]
