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


# ---------------------------------------------------------------------------
# LST pool tests (Phase 2d): SOL/jitoSOL, SOL/mSOL, mSOL/USDC.
# These exercise the equal-decimals code path (SOL:9 ↔ mSOL:9, SOL:9 ↔ jitoSOL:9)
# and the cross-decimal path (mSOL:9 ↔ USDC:6).
# ---------------------------------------------------------------------------


def test_orca_sol_jitosol_decodes_lst_ratio():
    """SOL/jitoSOL — same decimals, raw ratio = human price."""
    sol = get_token("SOL")
    jito = get_token("jitoSOL")
    # jitoSOL historically trades below SOL (it's a staking derivative),
    # so "jitoSOL per SOL" > 1. Use 1.14 as a plausible price.
    sqrt_price = _q64_for_human_price(D("1.14"), sol.decimals, jito.decimals)
    pool = PoolRef(
        name="Orca-SOL/jitoSOL", venue="Orca", pair="SOL/jitoSOL",
        base_symbol="SOL", quote_symbol="jitoSOL",
        address="Hp53XEtt4S8SvPCXarsLSdGfZBuUr5mMmZmX2DRNXQKp",
        program=ORCA_WHIRLPOOL_PROGRAM, fee_bps=1,
    )
    # 1bp fee tier → raw 100.
    data = _whirlpool_data(sol.mint, jito.mint, sqrt_price, fee_rate=100)
    rpc = MagicMock()
    rpc.get_multiple_accounts = MagicMock(return_value=[
        AccountInfo("Hp53XEtt4S8SvPCXarsLSdGfZBuUr5mMmZmX2DRNXQKp",
                    ORCA_WHIRLPOOL_PROGRAM, 0, data),
    ])
    m = OrcaMarket(rpc=rpc, pools=[pool])
    quotes = m.get_quotes()
    assert len(quotes) == 1
    q = quotes[0]
    # 1bp fee → half-fee = 0.5bp = 0.00005 → effective = 1.14 × 0.99995 ≈ 1.139943
    assert abs(q.buy_price - D("1.139943")) < D("0.001")
    assert q.fee_bps == D("1")
    assert q.fee_included is True
    assert q.pair == "SOL/jitoSOL"


def test_orca_sol_msol_decodes_lst_ratio():
    """SOL/mSOL — same decimals. mSOL trades above SOL due to accrued rewards."""
    sol = get_token("SOL")
    msol = get_token("mSOL")
    # mSOL accrues over time, so "mSOL per SOL" < 1. Use 0.87 (~15% rewards).
    sqrt_price = _q64_for_human_price(D("0.87"), sol.decimals, msol.decimals)
    pool = PoolRef(
        name="Orca-SOL/mSOL", venue="Orca", pair="SOL/mSOL",
        base_symbol="SOL", quote_symbol="mSOL",
        address="HQcY5n2zP6rW74fyFEhWeBd3LnJpBcZechkvJpmdb8cx",
        program=ORCA_WHIRLPOOL_PROGRAM, fee_bps=1,
    )
    data = _whirlpool_data(sol.mint, msol.mint, sqrt_price, fee_rate=100)
    rpc = MagicMock()
    rpc.get_multiple_accounts = MagicMock(return_value=[
        AccountInfo("HQcY5n2zP6rW74fyFEhWeBd3LnJpBcZechkvJpmdb8cx",
                    ORCA_WHIRLPOOL_PROGRAM, 0, data),
    ])
    m = OrcaMarket(rpc=rpc, pools=[pool])
    quotes = m.get_quotes()
    assert len(quotes) == 1
    assert abs(quotes[0].buy_price - D("0.869957")) < D("0.001")
    assert quotes[0].fee_bps == D("1")


def test_orca_msol_usdc_decodes_cross_decimal():
    """mSOL(9) / USDC(6) — exercises the 10^3 decimal-scaling code path."""
    msol = get_token("mSOL")
    usdc = get_token("USDC")
    # mSOL ~ $103 (SOL price × mSOL/SOL ratio).
    sqrt_price = _q64_for_human_price(D("103"), msol.decimals, usdc.decimals)
    pool = PoolRef(
        name="Orca-mSOL/USDC", venue="Orca", pair="mSOL/USDC",
        base_symbol="mSOL", quote_symbol="USDC",
        address="AiMZS5U3JMvpdvsr1KeaMiS354Z1DeSg5XjA4yYRxtFf",
        program=ORCA_WHIRLPOOL_PROGRAM, fee_bps=30,
    )
    # 30bp fee tier → raw 3000.
    data = _whirlpool_data(msol.mint, usdc.mint, sqrt_price, fee_rate=3000)
    rpc = MagicMock()
    rpc.get_multiple_accounts = MagicMock(return_value=[
        AccountInfo("AiMZS5U3JMvpdvsr1KeaMiS354Z1DeSg5XjA4yYRxtFf",
                    ORCA_WHIRLPOOL_PROGRAM, 0, data),
    ])
    m = OrcaMarket(rpc=rpc, pools=[pool])
    quotes = m.get_quotes()
    assert len(quotes) == 1
    # 30bp half-fee = 15bp = 0.0015 → effective = 103 × 0.9985 ≈ 102.8455
    assert abs(quotes[0].buy_price - D("102.8455")) < D("0.01")
    assert quotes[0].fee_bps == D("30")
    assert quotes[0].pair == "mSOL/USDC"


def test_orca_lst_pools_registered_in_core_pools():
    """Phase 2d acceptance: pools.py has the three LST entries."""
    from core.pools import POOLS
    names = {p.name for p in POOLS}
    assert "Orca-SOL/jitoSOL" in names
    assert "Orca-SOL/mSOL" in names
    assert "Orca-mSOL/USDC" in names
    # All three are wired to the Orca venue.
    for p in POOLS:
        if p.name.startswith("Orca-"):
            assert p.venue == "Orca"
            assert p.program == ORCA_WHIRLPOOL_PROGRAM
