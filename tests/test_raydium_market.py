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


# ---------------------------------------------------------------------------
# CPMM quote-at-size tests (Phase 2d).
# ---------------------------------------------------------------------------


from market.raydium_market import CpmmQuote, cpmm_quote


class TestCpmmQuoteMath:
    def test_returns_none_on_zero_input(self):
        assert cpmm_quote(D("10000"), D("90"), 25, D("0"), True) is None

    def test_returns_none_on_empty_pool(self):
        assert cpmm_quote(D("0"), D("90"), 25, D("1"), True) is None
        assert cpmm_quote(D("10000"), D("0"), 25, D("1"), True) is None

    def test_negative_input_returns_none(self):
        assert cpmm_quote(D("10000"), D("900000"), 25, D("-1"), True) is None

    def test_base_to_quote_with_zero_fee(self):
        """No fee, x·y=k: 1 SOL into a 1000-SOL/90000-USDC pool → 89.82... USDC."""
        q = cpmm_quote(
            base_reserve=D("1000"),
            quote_reserve=D("90000"),
            fee_bps=0,
            amount_in=D("1"),
            base_to_quote=True,
        )
        assert q is not None
        # exact: 90000 × 1 / (1000 + 1) = 89.910089910...
        assert abs(q.amount_out_human - D("89.91008991")) < D("0.0001")
        assert q.raw_midpoint == D("90")          # 90000 / 1000
        assert q.fee_paid_human == D("0")
        assert q.base_to_quote is True

    def test_base_to_quote_with_25bp_fee(self):
        """25bp fee: input reduced by 0.25% before CPMM."""
        q = cpmm_quote(
            base_reserve=D("1000"),
            quote_reserve=D("90000"),
            fee_bps=25,
            amount_in=D("1"),
            base_to_quote=True,
        )
        assert q is not None
        # in_after_fee = 0.9975; out = 90000 × 0.9975 / 1000.9975 ≈ 89.6853
        assert abs(q.amount_out_human - D("89.6853")) < D("0.001")
        assert q.fee_paid_human == D("0.0025")
        # Effective price is out/in = ~89.6853 / 1 = 89.6853.
        assert abs(q.effective_price - D("89.6853")) < D("0.001")

    def test_price_impact_scales_with_size(self):
        """Bigger trades move the price more."""
        r_base, r_quote, fee = D("1000"), D("90000"), 25
        small = cpmm_quote(r_base, r_quote, fee, D("1"), True)
        big   = cpmm_quote(r_base, r_quote, fee, D("100"), True)
        assert small is not None and big is not None
        # Big trade has strictly more price impact.
        assert big.price_impact_bps > small.price_impact_bps
        # And strictly worse effective price.
        assert big.effective_price < small.effective_price

    def test_quote_to_base_inverts(self):
        """Swapping quote for base: 90 USDC into 1000/90000 pool ≈ 0.998 SOL (no fee)."""
        q = cpmm_quote(
            base_reserve=D("1000"),
            quote_reserve=D("90000"),
            fee_bps=0,
            amount_in=D("90"),
            base_to_quote=False,
        )
        assert q is not None
        # R_in=90000, R_out=1000, Δ=90: out = 1000 × 90 / (90000 + 90) = 0.99900...
        assert abs(q.amount_out_human - D("0.99900099900")) < D("0.00001")
        assert q.raw_midpoint == D("1") / D("90")   # 1000 / 90000 SOL per USDC
        assert q.base_to_quote is False

    def test_returns_a_frozen_dataclass(self):
        q = cpmm_quote(D("1000"), D("90000"), 25, D("1"), True)
        assert isinstance(q, CpmmQuote)
        # frozen — assignment should raise.
        try:
            q.amount_in_human = D("99")      # type: ignore[misc]
            assert False, "expected FrozenInstanceError"
        except Exception:
            pass


class TestRaydiumQuoteAtSize:
    def test_returns_none_for_unknown_pool(self):
        rpc = MagicMock()
        m = RaydiumMarket(rpc=rpc, pools=[])
        assert m.quote_at_size("Raydium-NOPE/NOPE", D("1"), True) is None

    def test_returns_cpmm_quote_for_valid_pool(self):
        sol = get_token("SOL")
        usdc = get_token("USDC")
        base_vault = _fake_pubkey(11)
        quote_vault = _fake_pubkey(22)
        pool = PoolRef(
            name="Raydium-SOL/USDC", venue="Raydium", pair="SOL/USDC",
            base_symbol="SOL", quote_symbol="USDC",
            address=_fake_pubkey(1), program=RAYDIUM_AMM_V4_PROGRAM, fee_bps=25,
            base_vault=base_vault, quote_vault=quote_vault,
        )

        # Vault reads: 1000 SOL base, 90000 USDC quote.
        def _vault_data(raw_amount: int) -> bytes:
            data = bytearray(165)           # SPL token account size
            data[64:72] = raw_amount.to_bytes(8, "little")
            return bytes(data)

        rpc = MagicMock()
        rpc.get_multiple_accounts = MagicMock(return_value=[
            AccountInfo(base_vault,  SPL_TOKEN_PROGRAM, 0,
                        _vault_data(1000 * 10 ** sol.decimals)),
            AccountInfo(quote_vault, SPL_TOKEN_PROGRAM, 0,
                        _vault_data(90000 * 10 ** usdc.decimals)),
        ])
        m = RaydiumMarket(rpc=rpc, pools=[pool])
        m._vaults_cached = True            # skip the pool-state resolution step

        q = m.quote_at_size("Raydium-SOL/USDC", D("1"), True)
        assert q is not None
        assert abs(q.amount_out_human - D("89.6853")) < D("0.001")
        assert q.price_impact_bps > D("0")
