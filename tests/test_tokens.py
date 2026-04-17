"""Solana SPL token registry tests."""

from decimal import Decimal

import pytest

from core.tokens import TOKENS, Token, get_token, is_known, native_unit


def test_core_tokens_present():
    for sym in ("SOL", "WSOL", "USDC", "USDT", "mSOL", "jitoSOL", "bSOL"):
        assert sym in TOKENS or sym.upper() in {k.upper() for k in TOKENS}
        tok = get_token(sym)
        assert isinstance(tok, Token)
        assert tok.mint and len(tok.mint) > 30


def test_decimals():
    assert get_token("SOL").decimals == 9
    assert get_token("USDC").decimals == 6
    assert get_token("USDT").decimals == 6


def test_case_insensitive_lookup():
    assert get_token("sol") is get_token("SOL")
    assert get_token("usdc") is get_token("USDC")


def test_is_known():
    assert is_known("SOL") is True
    assert is_known("sol") is True
    assert is_known("DOGE") is False


def test_native_unit_conversion():
    # 1 SOL → 1e9 lamports
    assert native_unit("SOL", 1) == 10 ** 9
    # 1.5 SOL → 1_500_000_000
    assert native_unit("SOL", 1.5) == 1_500_000_000
    # 100 USDC → 100_000_000 (6 decimals)
    assert native_unit("USDC", 100) == 100_000_000
    # Fractional USDC
    assert native_unit("USDC", Decimal("0.5")) == 500_000


def test_unknown_symbol_raises():
    with pytest.raises(KeyError):
        get_token("NOTATHING")
