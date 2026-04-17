"""RaydiumMarket tests — mocked RPC, no network."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import base58

from core.pools import PoolRef, RAYDIUM_AMM_V4_PROGRAM, SPL_TOKEN_PROGRAM
from core.tokens import get_token
from market.raydium_market import RaydiumMarket
from market.solana_rpc import AccountInfo

D = Decimal


def _fake_pubkey(seed: int) -> str:
    """Deterministic valid base58 pubkey from a seed int."""
    return base58.b58encode(seed.to_bytes(32, "big")).decode()


def _pool_data_with_vaults(base_vault: str, quote_vault: str) -> bytes:
    """Fake 752-byte Raydium pool state with vault pubkeys at the right offsets."""
    data = bytearray(752)
    data[336:368] = base58.b58decode(base_vault)[:32].ljust(32, b"\x00")
    data[368:400] = base58.b58decode(quote_vault)[:32].ljust(32, b"\x00")
    return bytes(data)


def _spl_token_data(amount: int) -> bytes:
    """Fake SPL token account with ``amount`` at offset 64 (u64 LE)."""
    data = bytearray(165)
    data[64:72] = amount.to_bytes(8, "little")
    return bytes(data)


def _mock_rpc(account_map: dict[str, AccountInfo | None]) -> MagicMock:
    rpc = MagicMock()
    rpc.get_multiple_accounts = MagicMock(
        side_effect=lambda pks: [account_map.get(pk) for pk in pks],
    )
    return rpc


def test_raydium_computes_spot_price_from_vaults():
    pool = PoolRef(
        name="Raydium-SOL/USDC", venue="Raydium", pair="SOL/USDC",
        base_symbol="SOL", quote_symbol="USDC",
        address=_fake_pubkey(999),
        program=RAYDIUM_AMM_V4_PROGRAM,
        fee_bps=25,
    )
    bv = _fake_pubkey(1)
    qv = _fake_pubkey(2)
    # 1000 SOL (9 decimals), 90000 USDC (6 decimals) → price = 90
    base_raw = 1_000 * 10**9
    quote_raw = 90_000 * 10**6
    rpc = _mock_rpc({
        pool.address: AccountInfo(pool.address, pool.program, 0,
                                  _pool_data_with_vaults(bv, qv)),
        bv: AccountInfo(bv, SPL_TOKEN_PROGRAM, 0, _spl_token_data(base_raw)),
        qv: AccountInfo(qv, SPL_TOKEN_PROGRAM, 0, _spl_token_data(quote_raw)),
    })
    m = RaydiumMarket(rpc=rpc, pools=[pool])
    quotes = m.get_quotes()
    assert len(quotes) == 1
    q = quotes[0]
    assert q.pair == "SOL/USDC"
    # Raw midpoint 90, half-fee of 25 bps = 12.5 bps: effective = 90 × (1 - 0.00125) = 89.8875
    assert q.buy_price == D("89.8875")
    assert q.fee_bps == D("25")
    assert q.fee_included is True  # half-fee baked in; strategy must not re-apply
    # TVL ≈ 2 × quote side = 2 × 90000 = $180k
    assert q.liquidity_usd == D("180000")


def test_raydium_skips_pool_with_wrong_owner():
    pool = PoolRef(
        name="Raydium-SOL/USDC", venue="Raydium", pair="SOL/USDC",
        base_symbol="SOL", quote_symbol="USDC",
        address=_fake_pubkey(777), program=RAYDIUM_AMM_V4_PROGRAM, fee_bps=25,
    )
    rpc = _mock_rpc({
        pool.address: AccountInfo(pool.address, "SOMETHING_ELSE", 0, b"\x00" * 752),
    })
    m = RaydiumMarket(rpc=rpc, pools=[pool])
    assert m.get_quotes() == []


def test_raydium_caches_vaults_across_calls():
    pool = PoolRef(
        name="Raydium-SOL/USDC", venue="Raydium", pair="SOL/USDC",
        base_symbol="SOL", quote_symbol="USDC",
        address=_fake_pubkey(10), program=RAYDIUM_AMM_V4_PROGRAM, fee_bps=25,
    )
    bv = _fake_pubkey(1)
    qv = _fake_pubkey(2)
    accounts_map = {
        pool.address: AccountInfo(pool.address, pool.program, 0,
                                  _pool_data_with_vaults(bv, qv)),
        bv: AccountInfo(bv, SPL_TOKEN_PROGRAM, 0, _spl_token_data(10**12)),
        qv: AccountInfo(qv, SPL_TOKEN_PROGRAM, 0, _spl_token_data(10**9)),
    }
    call_count = {"n": 0}

    def side_effect(pks):
        call_count["n"] += 1
        return [accounts_map.get(pk) for pk in pks]

    rpc = MagicMock()
    rpc.get_multiple_accounts = MagicMock(side_effect=side_effect)
    m = RaydiumMarket(rpc=rpc, pools=[pool])
    m.get_quotes()  # first call: 1 call for vault resolution + 1 for read = 2
    m.get_quotes()  # second call: 1 call for read only
    # Total should be 3, not 4 — vaults are cached.
    assert call_count["n"] == 3


def test_raydium_handles_zero_reserves():
    pool = PoolRef(
        name="Raydium-SOL/USDC", venue="Raydium", pair="SOL/USDC",
        base_symbol="SOL", quote_symbol="USDC",
        address=_fake_pubkey(10), program=RAYDIUM_AMM_V4_PROGRAM, fee_bps=25,
    )
    bv = _fake_pubkey(1)
    qv = _fake_pubkey(2)
    rpc = _mock_rpc({
        pool.address: AccountInfo(pool.address, pool.program, 0,
                                  _pool_data_with_vaults(bv, qv)),
        bv: AccountInfo(bv, SPL_TOKEN_PROGRAM, 0, _spl_token_data(0)),
        qv: AccountInfo(qv, SPL_TOKEN_PROGRAM, 0, _spl_token_data(10**9)),
    })
    m = RaydiumMarket(rpc=rpc, pools=[pool])
    assert m.get_quotes() == []
