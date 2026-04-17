"""Orca Whirlpool direct-pool quote adapter.

Concentrated-liquidity pools encode price directly in the pool account as
``sqrt_price`` — a Q64.64 fixed-point number.  Spot price (in token_b per
token_a raw units) is ``(sqrt_price / 2**64) ** 2``.  We then scale by the
token decimals to get a human-readable price.

Token ordering
--------------

Whirlpool orders the two tokens as ``(token_mint_a, token_mint_b)``.  The
raw price always expresses *how much B you get for 1 A*.  The adapter
decodes ``token_mint_a``/``token_mint_b`` from the pool and matches them
against the configured ``base_symbol``/``quote_symbol`` so we always emit
``buy_price = quote per 1 base``, regardless of whether base=token_a or
base=token_b.

Per-scan cost: one batched ``getMultipleAccounts`` call covering all
configured Whirlpools.  No vault reads needed.
"""

from __future__ import annotations

import base58
import logging
import time
from decimal import Decimal
from typing import Iterable

from core.models import ZERO, MarketQuote
from core.pools import PoolRef, pools_for_venue
from core.tokens import get_token
from market.solana_rpc import AccountInfo, SolanaRPC

logger = logging.getLogger(__name__)

D = Decimal

# Anchor-discriminator-prefixed Whirlpool layout.  Offsets from
# the whirlpools-sdk (verified on-chain 2026-04-16).
_FEE_RATE_OFFSET      = 45      # u16
_LIQUIDITY_OFFSET     = 49      # u128
_SQRT_PRICE_OFFSET    = 65      # u128 (Q64.64)
_MINT_A_OFFSET        = 101     # Pubkey (32)
_MINT_B_OFFSET        = 181     # Pubkey (32)

_Q64 = D(2) ** 64


def _u16(data: bytes, off: int) -> int:
    return int.from_bytes(data[off:off + 2], "little")


def _u128(data: bytes, off: int) -> int:
    return int.from_bytes(data[off:off + 16], "little")


def _pk_at(data: bytes, off: int) -> str:
    return base58.b58encode(data[off:off + 32]).decode()


class OrcaMarket:
    """Whirlpool spot-price market source."""

    def __init__(
        self,
        rpc: SolanaRPC | None = None,
        pools: Iterable[PoolRef] | None = None,
    ) -> None:
        self.rpc = rpc or SolanaRPC()
        self._pools: list[PoolRef] = list(pools) if pools is not None else pools_for_venue("Orca")

    def get_quotes(self) -> list[MarketQuote]:
        if not self._pools:
            return []
        accounts = self.rpc.get_multiple_accounts([p.address for p in self._pools])
        now = time.time()
        out: list[MarketQuote] = []
        for pool, acc in zip(self._pools, accounts):
            quote = self._price_from_whirlpool(pool, acc, now)
            if quote is not None:
                out.append(quote)
        return out

    @staticmethod
    def _price_from_whirlpool(
        pool: PoolRef,
        acc: AccountInfo | None,
        timestamp: float,
    ) -> MarketQuote | None:
        if acc is None:
            logger.warning("[orca] pool %s not found", pool.name)
            return None
        if acc.owner != pool.program:
            logger.warning("[orca] %s wrong owner: %s", pool.name, acc.owner)
            return None
        if len(acc.data) < _MINT_B_OFFSET + 32:
            logger.warning("[orca] %s data too short: %d bytes", pool.name, len(acc.data))
            return None

        sqrt_price_raw = _u128(acc.data, _SQRT_PRICE_OFFSET)
        if sqrt_price_raw == 0:
            return None
        liquidity_raw = _u128(acc.data, _LIQUIDITY_OFFSET)
        fee_rate_raw = _u16(acc.data, _FEE_RATE_OFFSET)
        mint_a = _pk_at(acc.data, _MINT_A_OFFSET)
        mint_b = _pk_at(acc.data, _MINT_B_OFFSET)

        # Raw ratio: B per A in *native* (integer) units.
        sqrt_ratio = D(sqrt_price_raw) / _Q64
        b_per_a_raw = sqrt_ratio * sqrt_ratio

        base_tok = get_token(pool.base_symbol)
        quote_tok = get_token(pool.quote_symbol)

        # Orient so ``price`` = quote per base in human units.
        if mint_a == base_tok.mint and mint_b == quote_tok.mint:
            # base is A, quote is B: human_price = raw × 10^(decA - decB)
            price = b_per_a_raw * (D(10) ** (base_tok.decimals - quote_tok.decimals))
        elif mint_a == quote_tok.mint and mint_b == base_tok.mint:
            # quote is A, base is B: we have (base per quote) raw → invert
            if b_per_a_raw == 0:
                return None
            a_per_b_raw = D(1) / b_per_a_raw
            # Now a_per_b is raw quote-per-base; apply decimal scaling.
            price = a_per_b_raw * (D(10) ** (base_tok.decimals - quote_tok.decimals))
        else:
            logger.warning(
                "[orca] %s mints don't match config: pool=(%s,%s) config=(%s,%s)",
                pool.name, mint_a[:8], mint_b[:8], base_tok.mint[:8], quote_tok.mint[:8],
            )
            return None

        if price <= ZERO:
            return None

        # Fee rate in Whirlpool is in hundredths of a basis point (100 = 0.01%,
        # 500 = 0.05%, 3000 = 0.3%).  Convert to bps: raw / 100.
        fee_bps = D(fee_rate_raw) / D("100")

        # Apply half of the pool fee so the returned price is symmetric and
        # comparable with Jupiter's net-of-fee output.  See Raydium adapter
        # for the full rationale (Phase 2c finding).
        half_fee = fee_bps / (D("2") * D("10000"))
        effective_price = price * (D("1") - half_fee)

        return MarketQuote(
            venue=f"Orca-{pool.pair.split('/')[0]}/{pool.pair.split('/')[1]}",
            pair=pool.pair,
            buy_price=effective_price,
            sell_price=effective_price,
            fee_bps=fee_bps,
            fee_included=True,
            quote_timestamp=timestamp,
            liquidity_usd=ZERO,
            venue_type="amm",
        )
