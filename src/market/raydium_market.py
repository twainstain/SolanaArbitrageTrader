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
from decimal import Decimal
from typing import Iterable

from core.models import ZERO, MarketQuote
from core.pools import PoolRef, pools_for_venue
from core.tokens import get_token
from market.solana_rpc import AccountInfo, SolanaRPC, parse_spl_token_amount

logger = logging.getLogger(__name__)

D = Decimal

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
