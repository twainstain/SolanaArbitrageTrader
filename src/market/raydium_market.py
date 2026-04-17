"""Raydium AMM V4 direct-pool quote adapter.

Reads pool reserves from Solana RPC (via our Alchemy endpoint) and computes
a spot price with constant-product math — no Jupiter, no rate limits.

First scan per pool: ``getAccountInfo`` for the pool state to resolve the
base/quote SPL token vault addresses.  These are cached for the process
lifetime.

Subsequent scans: a single ``getMultipleAccounts`` batch reads *all*
configured vaults for *all* configured pools at once.  Per-scan RPC cost
stays at one round-trip regardless of pool count.

Price formula (ignoring fees — Jupiter-style fee_included=False so the
strategy handles fees explicitly):

    price = (quote_reserve / 10^quote_decimals) /
            (base_reserve  / 10^base_decimals)
"""

from __future__ import annotations

import base58
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from core.models import ZERO, MarketQuote
from core.pools import PoolRef, pools_for_venue
from core.tokens import get_token
from market.solana_rpc import AccountInfo, SolanaRPC, parse_spl_token_amount

logger = logging.getLogger(__name__)

D = Decimal


# ---------------------------------------------------------------------------
# CPMM swap-output simulation (Phase 2d).
# Pure function so the math is trivially unit-testable without RPC mocks.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CpmmQuote:
    """Result of a CPMM swap simulation at a specific input size."""
    amount_in_human: Decimal
    amount_out_human: Decimal
    effective_price: Decimal         # amount_out / amount_in (quote per base if base→quote)
    raw_midpoint: Decimal            # pre-fee quote_reserve / base_reserve
    price_impact_bps: Decimal        # (midpoint - effective) / midpoint × 10000
    fee_paid_human: Decimal          # fee in input-side units
    base_to_quote: bool


def cpmm_quote(
    base_reserve: Decimal,
    quote_reserve: Decimal,
    fee_bps: Decimal | int,
    amount_in: Decimal,
    base_to_quote: bool = True,
) -> CpmmQuote | None:
    """Simulate a CPMM swap with Uniswap-v2 fee model (fee on input).

    Reserves are in human units (pre-decimals). ``amount_in`` is the human
    amount on the input side: base if ``base_to_quote`` else quote.

    Returns None if the input is non-positive or reserves are empty.

    Formula (fee taken from input, not output):
        in_after_fee = amount_in × (1 - fee_bps/10000)
        amount_out = R_out × in_after_fee / (R_in + in_after_fee)
    """
    if amount_in <= ZERO or base_reserve <= ZERO or quote_reserve <= ZERO:
        return None

    fee_decimal = D(fee_bps) / D("10000")
    in_after_fee = amount_in * (D("1") - fee_decimal)
    if base_to_quote:
        r_in, r_out = base_reserve, quote_reserve
        raw_mid = quote_reserve / base_reserve
    else:
        r_in, r_out = quote_reserve, base_reserve
        raw_mid = base_reserve / quote_reserve

    amount_out = (r_out * in_after_fee) / (r_in + in_after_fee)
    effective_price = amount_out / amount_in if amount_in > ZERO else ZERO
    # Price impact as bps of the pre-fee midpoint.
    price_impact_bps = (
        (raw_mid - effective_price) / raw_mid * D("10000")
        if raw_mid > ZERO else ZERO
    )
    return CpmmQuote(
        amount_in_human=amount_in,
        amount_out_human=amount_out,
        effective_price=effective_price,
        raw_midpoint=raw_mid,
        price_impact_bps=price_impact_bps,
        fee_paid_human=amount_in * fee_decimal,
        base_to_quote=base_to_quote,
    )

# LIQUIDITY_STATE_LAYOUT_V4 offsets — verified on-chain 2026-04-16.
_BASE_VAULT_OFFSET = 336
_QUOTE_VAULT_OFFSET = 368


def _pk_at(data: bytes, offset: int) -> str:
    """Decode a 32-byte Pubkey at ``offset`` as base58."""
    return base58.b58encode(data[offset:offset + 32]).decode()


class RaydiumMarket:
    """Raydium AMM V4 spot-price market source.

    Only handles pools whose venue is ``"Raydium"`` in ``core.pools``.
    Other pools are ignored.  Constructing this with no matching pools
    yields an adapter that returns ``[]`` — safe no-op, won't error.
    """

    def __init__(
        self,
        rpc: SolanaRPC | None = None,
        pools: Iterable[PoolRef] | None = None,
    ) -> None:
        self.rpc = rpc or SolanaRPC()
        self._pools: list[PoolRef] = list(pools) if pools is not None else pools_for_venue("Raydium")
        self._vaults_cached: bool = False

    # ------------------------------------------------------------------
    # Public API (matches the MarketSource protocol used by the scanner)
    # ------------------------------------------------------------------

    def get_quotes(self) -> list[MarketQuote]:
        if not self._pools:
            return []
        self._ensure_vaults_resolved()
        return self._read_and_price()

    def quote_at_size(
        self,
        pool_name: str,
        amount_in: Decimal,
        base_to_quote: bool = True,
    ) -> CpmmQuote | None:
        """Simulate a swap of ``amount_in`` through a specific pool.

        Phase 2d upgrade over the half-fee midpoint used for scan-time
        quotes: this computes the actual output with CPMM price impact
        at the given trade size. Use this in the Pricing Agent stage
        once the strategy has picked a candidate. Returns None if the
        pool isn't registered, isn't resolved, or has empty reserves.
        """
        self._ensure_vaults_resolved()
        pool = next((p for p in self._pools if p.name == pool_name), None)
        if pool is None or not (pool.base_vault and pool.quote_vault):
            return None
        accounts = self.rpc.get_multiple_accounts(
            [pool.base_vault, pool.quote_vault]
        )
        if len(accounts) != 2:
            return None
        base_acc, quote_acc = accounts
        if base_acc is None or quote_acc is None:
            return None
        try:
            base_raw = parse_spl_token_amount(base_acc.data)
            quote_raw = parse_spl_token_amount(quote_acc.data)
        except ValueError:
            return None
        base_tok = get_token(pool.base_symbol)
        quote_tok = get_token(pool.quote_symbol)
        base_reserve = D(base_raw) / (D(10) ** base_tok.decimals)
        quote_reserve = D(quote_raw) / (D(10) ** quote_tok.decimals)
        return cpmm_quote(
            base_reserve=base_reserve,
            quote_reserve=quote_reserve,
            fee_bps=pool.fee_bps,
            amount_in=amount_in,
            base_to_quote=base_to_quote,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_vaults_resolved(self) -> None:
        """On first call, fetch each pool account and extract vault pubkeys."""
        if self._vaults_cached:
            return
        missing = [p for p in self._pools if not (p.base_vault and p.quote_vault)]
        if not missing:
            self._vaults_cached = True
            return

        accounts = self.rpc.get_multiple_accounts([p.address for p in missing])
        resolved: list[PoolRef] = list(self._pools)
        for pool, acc in zip(missing, accounts):
            if acc is None or acc.owner != pool.program:
                logger.warning(
                    "[raydium] skipping %s — pool missing or wrong owner (got %s, expected %s)",
                    pool.name, acc.owner if acc else "None", pool.program,
                )
                resolved = [p for p in resolved if p is not pool]
                continue
            if len(acc.data) < _QUOTE_VAULT_OFFSET + 32:
                logger.warning("[raydium] skipping %s — data too short (%d bytes)", pool.name, len(acc.data))
                resolved = [p for p in resolved if p is not pool]
                continue
            base_vault = _pk_at(acc.data, _BASE_VAULT_OFFSET)
            quote_vault = _pk_at(acc.data, _QUOTE_VAULT_OFFSET)
            # Replace with a copy that has vaults populated (PoolRef is frozen).
            new_pool = PoolRef(
                name=pool.name, venue=pool.venue, pair=pool.pair,
                base_symbol=pool.base_symbol, quote_symbol=pool.quote_symbol,
                address=pool.address, program=pool.program, fee_bps=pool.fee_bps,
                base_vault=base_vault, quote_vault=quote_vault,
            )
            resolved = [new_pool if p is pool else p for p in resolved]
            logger.info("[raydium] %s vaults: base=%s… quote=%s…",
                         pool.name, base_vault[:8], quote_vault[:8])

        self._pools = resolved
        self._vaults_cached = True

    def _read_and_price(self) -> list[MarketQuote]:
        """Batch-read all vaults, then derive one MarketQuote per pool."""
        pubkeys: list[str] = []
        for p in self._pools:
            if p.base_vault and p.quote_vault:
                pubkeys.append(p.base_vault)
                pubkeys.append(p.quote_vault)
        if not pubkeys:
            return []

        accounts = self.rpc.get_multiple_accounts(pubkeys)
        # Pair them back up: every 2 accounts belong to one pool.
        now = time.time()
        out: list[MarketQuote] = []
        i = 0
        for pool in self._pools:
            if not (pool.base_vault and pool.quote_vault):
                continue
            base_acc = accounts[i]
            quote_acc = accounts[i + 1]
            i += 2
            quote = self._price_from_vaults(pool, base_acc, quote_acc, now)
            if quote is not None:
                out.append(quote)
        return out

    @staticmethod
    def _price_from_vaults(
        pool: PoolRef,
        base_acc: AccountInfo | None,
        quote_acc: AccountInfo | None,
        timestamp: float,
    ) -> MarketQuote | None:
        if base_acc is None or quote_acc is None:
            logger.debug("[raydium] %s vault read returned None", pool.name)
            return None
        try:
            base_raw = parse_spl_token_amount(base_acc.data)
            quote_raw = parse_spl_token_amount(quote_acc.data)
        except ValueError as exc:
            logger.warning("[raydium] %s vault parse failed: %s", pool.name, exc)
            return None
        if base_raw == 0 or quote_raw == 0:
            return None

        base_tok = get_token(pool.base_symbol)
        quote_tok = get_token(pool.quote_symbol)
        base_reserve = D(base_raw) / (D(10) ** base_tok.decimals)
        quote_reserve = D(quote_raw) / (D(10) ** quote_tok.decimals)
        if base_reserve <= ZERO:
            return None

        # Raw midpoint is the *pre-fee* price.  To be comparable with Jupiter
        # (which returns net-of-fee output), we apply the pool's swap fee
        # symmetrically: a trader buying 1 base pays ~raw_price / (1-fee),
        # a trader selling receives raw_price × (1-fee).  For a spot quote
        # we split the difference: effective_price = raw × (1 - fee/2).
        # This eliminates the "phantom spread" where Raydium's raw reserve
        # price appears fee_bps above Jupiter's post-fee price (discovered
        # in Phase 2c).  See docs/solana_migration_status.md.
        raw_price = quote_reserve / base_reserve
        half_fee = D(pool.fee_bps) / (D("2") * D("10000"))
        effective_price = raw_price * (D("1") - half_fee)

        # TVL ≈ 2 × quote side (x·y = k ⇒ both sides are equal notional).
        tvl_usd = quote_reserve * D("2") if pool.quote_symbol in ("USDC", "USDT") else ZERO

        return MarketQuote(
            venue=f"Raydium-{pool.pair.split('/')[0]}/{pool.pair.split('/')[1]}",
            pair=pool.pair,
            buy_price=effective_price,
            sell_price=effective_price,
            fee_bps=D(pool.fee_bps),
            fee_included=True,     # half-fee baked into price; strategy must not re-apply
            quote_timestamp=timestamp,
            liquidity_usd=tvl_usd,
            venue_type="amm",
        )
