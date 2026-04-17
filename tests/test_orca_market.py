"""OrcaMarket tests — mocked RPC, no network."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import base58

from core.pools import PoolRef, ORCA_WHIRLPOOL_PROGRAM
from core.tokens import get_token
from market.orca_market import OrcaMarket
from market.solana_rpc import AccountInfo

D = Decimal


def _whirlpool_data(
    mint_a: str,
    mint_b: str,
    sqrt_price: int,
    fee_rate: int = 500,   # 0.05% tier
    liquidity: int = 10**15,
) -> bytes:
    """Build a minimally valid 653-byte Whirlpool account with the fields we read."""
    data = bytearray(653)
    # fee_rate @ 45 (u16 LE)
    data[45:47] = fee_rate.to_bytes(2, "little")
    # liquidity @ 49 (u128 LE)
    data[49:65] = liquidity.to_bytes(16, "little")
    # sqrt_price @ 65 (u128 LE, Q64.64)
    data[65:81] = sqrt_price.to_bytes(16, "little")
    # token_mint_a @ 101 (32 bytes)
    data[101:133] = base58.b58decode(mint_a).ljust(32, b"\x00")[:32]
    # token_mint_b @ 181 (32 bytes)
    data[181:213] = base58.b58decode(mint_b).ljust(32, b"\x00")[:32]
    return bytes(data)


def _q64_for_human_price(quote_per_base: Decimal, base_dec: int, quote_dec: int) -> int:
    """Invert the adapter's math to produce a sqrt_price that yields a given human price.

    Adapter: human = (sqrt_price/2^64)^2 × 10^(decA - decB)  [when base=A, quote=B]
    Solve:   sqrt_price = sqrt(human × 10^(decB - decA)) × 2^64
    """
    raw_ratio = quote_per_base * (Decimal(10) ** (quote_dec - base_dec))
    # integer sqrt via Newton's method would be overkill; Decimal precision is fine.
    sqrt_ratio = raw_ratio.sqrt()
    return int(sqrt_ratio * (Decimal(2) ** 64))


def test_orca_sol_usdc_decodes_price_correctly():
    sol = get_token("SOL")
    usdc = get_token("USDC")
    # Configure a Whirlpool where base=A=SOL, quote=B=USDC and price = $90.
    sqrt_price = _q64_for_human_price(D("90"), sol.decimals, usdc.decimals)
    pool = PoolRef(
        name="Orca-SOL/USDC", venue="Orca", pair="SOL/USDC",
        base_symbol="SOL", quote_symbol="USDC",
        address="11111111111111111111111111111111", program=ORCA_WHIRLPOOL_PROGRAM, fee_bps=5,
    )
    data = _whirlpool_data(sol.mint, usdc.mint, sqrt_price, fee_rate=500)
    rpc = MagicMock()
    rpc.get_multiple_accounts = MagicMock(return_value=[
        AccountInfo("11111111111111111111111111111111", ORCA_WHIRLPOOL_PROGRAM, 0, data),
    ])
    m = OrcaMarket(rpc=rpc, pools=[pool])
    quotes = m.get_quotes()
    assert len(quotes) == 1
    q = quotes[0]
    # Raw price ≈ 90, half-fee of 5 bps = 2.5 bps: effective ≈ 90 × (1 - 0.00025) = 89.9775
    assert abs(q.buy_price - D("89.9775")) < D("0.001")
    assert q.fee_bps == D("5")  # 500 raw / 100
    assert q.fee_included is True


def test_orca_inverts_when_base_is_token_b():
    """If pool's mint_a is quote and mint_b is base, adapter must invert."""
    sol = get_token("SOL")
    usdc = get_token("USDC")
    # Declare pool as SOL/USDC but put USDC as mint_a and SOL as mint_b.
    # That's unusual but the adapter must handle it.
    # Raw price B-per-A in this orientation = SOL per USDC (small number).
    # Target human USDC-per-SOL = 90, so USDC-per-SOL raw in "decimals of B for A" with A=USDC(6), B=SOL(9):
    #   human price of B per A = 1/90, with decimal shift 10^(6-9) = 10^-3
    #   raw = (1/90) × 10^3 = 11.111
    price_b_per_a = (D(1) / D("90")) * (Decimal(10) ** 3)
    sqrt_price = int(price_b_per_a.sqrt() * (Decimal(2) ** 64))
    pool = PoolRef(
        name="Orca-SOL/USDC", venue="Orca", pair="SOL/USDC",
        base_symbol="SOL", quote_symbol="USDC",
        address="11111111111111111111111111111111", program=ORCA_WHIRLPOOL_PROGRAM, fee_bps=5,
    )
    data = _whirlpool_data(usdc.mint, sol.mint, sqrt_price)
    rpc = MagicMock()
    rpc.get_multiple_accounts = MagicMock(return_value=[
        AccountInfo("11111111111111111111111111111111", ORCA_WHIRLPOOL_PROGRAM, 0, data),
    ])
    m = OrcaMarket(rpc=rpc, pools=[pool])
    quotes = m.get_quotes()
    assert len(quotes) == 1
    # Price should still come out as ~89.9775 USDC per SOL (90 minus half-fee).
    assert abs(quotes[0].buy_price - D("89.9775")) < D("0.01")


def test_orca_skips_wrong_owner():
    pool = PoolRef(
        name="Orca-SOL/USDC", venue="Orca", pair="SOL/USDC",
        base_symbol="SOL", quote_symbol="USDC",
        address="11111111111111111111111111111111", program=ORCA_WHIRLPOOL_PROGRAM, fee_bps=5,
    )
    rpc = MagicMock()
    rpc.get_multiple_accounts = MagicMock(return_value=[
        AccountInfo("11111111111111111111111111111111",
                    "22222222222222222222222222222222", 0, b"\x00" * 653),
    ])
    assert OrcaMarket(rpc=rpc, pools=[pool]).get_quotes() == []


def test_orca_skips_missing_pool():
    pool = PoolRef(
        name="Orca-SOL/USDC", venue="Orca", pair="SOL/USDC",
        base_symbol="SOL", quote_symbol="USDC",
        address="11111111111111111111111111111111", program=ORCA_WHIRLPOOL_PROGRAM, fee_bps=5,
    )
    rpc = MagicMock()
    rpc.get_multiple_accounts = MagicMock(return_value=[None])
    assert OrcaMarket(rpc=rpc, pools=[pool]).get_quotes() == []
