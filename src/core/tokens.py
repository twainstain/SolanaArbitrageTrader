"""Solana SPL token registry.

Maps common ticker symbols to their on-chain SPL mint addresses and
decimals.  Used by the market adapter (Jupiter) to translate human-readable
pairs like "SOL/USDC" into the mint pubkeys the Jupiter API expects.

All addresses are Solana mainnet-beta SPL mints.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Token:
    symbol: str
    mint: str       # base58 SPL mint address
    decimals: int   # exponent for integer-to-human amount conversion


# Mainnet-beta SPL mints.
# References:
#   - Native SOL wrapped:  https://explorer.solana.com/address/So11111111111111111111111111111111111111112
#   - USDC (Circle):       https://explorer.solana.com/address/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v
#   - USDT (Tether):       https://explorer.solana.com/address/Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB
TOKENS: dict[str, Token] = {
    "SOL":     Token("SOL",     "So11111111111111111111111111111111111111112",  9),
    "WSOL":    Token("WSOL",    "So11111111111111111111111111111111111111112",  9),
    "USDC":    Token("USDC",    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 6),
    "USDT":    Token("USDT",    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", 6),
    # Liquid staking derivatives — for future SOL/mSOL, SOL/jitoSOL arb.
    # Keys are uppercase so get_token() lookup works with any casing.
    "MSOL":    Token("mSOL",    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",  9),
    "JITOSOL": Token("jitoSOL", "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn", 9),
    "BSOL":    Token("bSOL",    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1",  9),
    # Solana-native assets surfaced by `scripts/discover_pairs.py` as the
    # deepest non-LST pools (volume × dex_count × blue-chip).
    "JUP":     Token("JUP",     "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  6),
    "BONK":    Token("BONK",    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  5),
}


def get_token(symbol: str) -> Token:
    """Return the Token for a symbol, case-insensitive.

    Raises KeyError if the symbol is not in the registry.
    """
    key = symbol.upper()
    if key not in TOKENS:
        raise KeyError(f"Unknown SPL token symbol: {symbol}")
    return TOKENS[key]


def is_known(symbol: str) -> bool:
    return symbol.upper() in TOKENS


def native_unit(symbol: str, human_amount: float | str) -> int:
    """Convert a human-readable amount (e.g. 1.5 SOL) to integer minor units.

    For SOL this returns lamports (10^9).  For USDC/USDT it returns micro-units
    (10^6).  The result is always an ``int`` — financial math on-chain is
    integer-only (no float drift).
    """
    from decimal import Decimal
    tok = get_token(symbol)
    return int(Decimal(str(human_amount)) * (Decimal(10) ** tok.decimals))
