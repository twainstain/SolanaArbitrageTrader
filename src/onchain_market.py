"""OnChainMarket — fetches real per-DEX quotes via web3.py RPC calls.

Queries Uniswap V3 QuoterV2, SushiSwap V3 QuoterV2, and (optionally)
Balancer V2 Vault ``queryBatchSwap`` on the configured chain.  Uses public
RPC endpoints by default.

Each DEX config entry must specify ``chain`` and ``dex_type`` (one of
``uniswap_v3``, ``sushi_v3``, ``pancakeswap_v3``, ``balancer_v2``).

Usage::

    PYTHONPATH=src python -m main \\
        --config config/onchain_config.json --onchain --dry-run --no-sleep
"""

from __future__ import annotations

import time as _time
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from decimal import Decimal

from web3 import Web3

from config import BotConfig, PairConfig
import logging

from contracts import (
    AERODROME_ROUTER,
    ALGEBRA_V2_QUOTER_ABI,
    BALANCER_POOL_IDS,
    BALANCER_VAULT,
    BALANCER_VAULT_ABI,
    CAMELOT_QUOTER,
    CURVE_POOL_ABI,
    CURVE_POOLS,
    CURVE_TOKEN_INDEX,
    PANCAKE_V3_QUOTER,
    PANCAKE_V3_QUOTER_ABI,
    PUBLIC_RPC_URLS,
    QUICKSWAP_QUOTER,
    QUICKSWAP_QUOTER_ABI,
    SUSHI_V3_QUOTER,
    SUSHI_V3_QUOTER_ABI,
    UNISWAP_FEE_TIERS,
    UNISWAP_V3_QUOTER_ABI,
    UNISWAP_V3_QUOTER_PER_CHAIN,
    UNISWAP_V3_QUOTER_V2,
    TRADERJOE_LB_QUOTER,
    TRADERJOE_LB_QUOTER_ABI,
    VELO_FACTORY,
    VELO_ROUTER_ABI,
    VELODROME_ROUTER,
)

_logger = logging.getLogger(__name__)

# Backup public RPCs for failover (free, rate-limited).
BACKUP_RPC_URLS: dict[str, list[str]] = {
    "ethereum": ["https://eth.llamarpc.com", "https://rpc.ankr.com/eth", "https://1rpc.io/eth"],
    "arbitrum": ["https://arb1.arbitrum.io/rpc", "https://rpc.ankr.com/arbitrum", "https://1rpc.io/arb"],
    "base": ["https://mainnet.base.org", "https://base.llamarpc.com", "https://1rpc.io/base"],
    "polygon": ["https://1rpc.io/matic", "https://polygon-bor-rpc.publicnode.com", "https://polygon.drpc.org"],
    "optimism": ["https://mainnet.optimism.io", "https://rpc.ankr.com/optimism", "https://1rpc.io/op"],
    "bsc": ["https://bsc-dataseed.binance.org", "https://bsc-dataseed1.defibit.io"],
}
from data.liquidity_cache import LiquidityCache
from models import BPS_DIVISOR, ZERO, MarketQuote
from tokens import CHAIN_TOKENS, token_decimals

D = Decimal
TWO = D("2")

SUPPORTED_DEX_TYPES = (
    "uniswap_v3", "sushi_v3", "pancakeswap_v3", "balancer_v2", "quickswap_v3",
    "camelot_v3", "velodrome_v2", "aerodrome", "curve", "traderjoe_lb",
)

STABLE_SYMBOLS = frozenset({"USDC", "USDT", "DAI"})
MAJOR_SYMBOLS = frozenset({"WETH", "ETH", "WBTC", "BTC", "WBNB", "BNB", "ARB", "OP", "LINK", "AAVE", "UNI", "CRV", "GMX"})


class OnChainMarketError(Exception):
    """Raised when an on-chain query fails."""


def _validate_price(
    price: Decimal,
    dex: str,
    chain: str,
    pair_name: str,
    base_symbol: str,
    quote_symbol: str,
) -> Decimal:
    """Reject quotes that are obviously wrong for the pair being scanned.

    These bounds catch decimal/unit bugs (e.g., BSC returning $2.3 quadrillion
    for WETH because of a wei/token confusion) and broken oracle feeds.
    The tiers are intentionally wide — they're safety nets, not precision filters.
    Fine-grained quality is handled by the outlier filter and liquidity estimation.
    """
    if price <= 0:
        raise OnChainMarketError(f"{dex} on {chain} returned non-positive quote for {pair_name}.")

    base = base_symbol.upper()
    quote = quote_symbol.upper()

    # Stable/stable (e.g., USDC/USDT): should trade within 0.5–2.0.
    # In practice peg stays 0.99–1.01, but we allow wide range to
    # accommodate depeg events (e.g., USDC in Mar 2023 hit ~$0.87).
    # Anything outside 0.5–2.0 is a unit error, not a real depeg.
    if base in STABLE_SYMBOLS and quote in STABLE_SYMBOLS:
        if price < D("0.5") or price > D("2"):
            raise OnChainMarketError(
                f"{dex} on {chain} returned {price} for {pair_name} — outside stable-pair bounds."
            )
        return price

    # Major/stable (e.g., WETH/USDC): no token should be worth >$1M.
    # BTC ATH ~$100K, ETH ATH ~$5K — $1M gives 10-200x headroom.
    if quote in STABLE_SYMBOLS and base in MAJOR_SYMBOLS:
        if price > D("1000000"):
            raise OnChainMarketError(
                f"{dex} on {chain} returned {price} for {pair_name} — above major/stable bounds."
            )
        return price

    # Generic fallback: $1T catches raw wei values leaking through
    # (e.g., returning amount_out without dividing by 10^decimals).
    if price > D("1000000000000"):
        raise OnChainMarketError(
            f"{dex} on {chain} returned {price} for {pair_name} — above generic sanity bounds."
        )
    return price


@dataclass(frozen=True)
class _PairDef:
    pair_name: str
    base_asset: str
    quote_asset: str
    trade_size: Decimal
    base_address: str | None = None
    quote_address: str | None = None
    pair_chain: str | None = None


class OnChainMarket:
    """Query DEX smart contracts for real per-DEX price quotes."""

    def __init__(
        self,
        config: BotConfig,
        rpc_overrides: dict[str, str] | None = None,
        liquidity_cache: LiquidityCache | None = None,
        pairs: list[PairConfig] | None = None,
        diagnostics: "QuoteDiagnostics | None" = None,
    ) -> None:
        self.config = config
        self._rpc_overrides = rpc_overrides or {}
        self.liquidity_cache = liquidity_cache or LiquidityCache()
        self.diagnostics = diagnostics
        self._pairs: list[_PairDef] = []

        if pairs is not None:
            for p in pairs:
                self._pairs.append(_PairDef(
                    pair_name=p.pair,
                    base_asset=p.base_asset,
                    quote_asset=p.quote_asset,
                    trade_size=p.trade_size,
                    base_address=p.base_address,
                    quote_address=p.quote_address,
                    pair_chain=p.chain,
                ))
        else:
            self._pairs.append(_PairDef(
                pair_name=config.pair,
                base_asset=config.base_asset,
                quote_asset=config.quote_asset,
                trade_size=config.trade_size,
            ))
            if config.extra_pairs:
                for p in config.extra_pairs:
                    self._pairs.append(_PairDef(
                        pair_name=p.pair,
                        base_asset=p.base_asset,
                        quote_asset=p.quote_asset,
                        trade_size=p.trade_size,
                        base_address=p.base_address,
                        quote_address=p.quote_address,
                        pair_chain=p.chain,
                    ))

        # Pre-build web3 instances keyed by chain, with failover URLs.
        self._w3: dict[str, Web3] = {}
        self._rpc_urls: dict[str, list[str]] = {}  # chain → list of URLs for failover
        self._rpc_index: dict[str, int] = {}  # chain → current URL index
        # Cache best fee tier per (dex_type, chain) — avoids trying all 4 tiers each scan.
        self._best_fee: dict[str, tuple[int, float]] = {}  # key → (fee, timestamp)
        # Cache liquidity estimates — avoids extra RPC calls for _estimate_liquidity_usd.
        # key: "dex_type:chain:base:quote" → (tvl_decimal, timestamp)
        self._tvl_cache: dict[str, tuple[Decimal, float]] = {}
        self._TVL_CACHE_TTL = 300.0  # 5 minutes
        # Persistent thread pool for RPC calls — avoids creating/destroying
        # threads every scan cycle.  Sized generously so all DEX+pair combos
        # can run in parallel (the original code created a pool per scan with
        # max_workers=len(active_requests)).
        n_pairs = 1 + len(config.extra_pairs or [])
        self._pool = ThreadPoolExecutor(max_workers=max(len(config.dexes) * n_pairs, 32))

        for dex in config.dexes:
            chain = dex.chain
            if chain is None:
                raise OnChainMarketError(
                    f"DEX '{dex.name}': on-chain mode requires a 'chain' field."
                )
            if chain not in PUBLIC_RPC_URLS and chain not in self._rpc_overrides:
                raise OnChainMarketError(
                    f"No RPC URL for chain '{chain}'.  "
                    f"Supported: {sorted(PUBLIC_RPC_URLS)}."
                )
            dex_type = dex.dex_type
            if dex_type is None:
                raise OnChainMarketError(
                    f"DEX '{dex.name}': on-chain mode requires a 'dex_type' field."
                )
            if dex_type not in SUPPORTED_DEX_TYPES:
                raise OnChainMarketError(
                    f"DEX '{dex.name}': unsupported dex_type '{dex_type}'.  "
                    f"Supported: {SUPPORTED_DEX_TYPES}."
                )
            if chain not in self._w3:
                # Build URL list: override first, then backups.
                urls = []
                override = self._rpc_overrides.get(chain)
                if override:
                    urls.append(override)
                urls.extend(BACKUP_RPC_URLS.get(chain, []))
                if not urls:
                    urls.append(PUBLIC_RPC_URLS[chain])
                self._rpc_urls[chain] = urls
                self._rpc_index[chain] = 0
                self._w3[chain] = Web3(Web3.HTTPProvider(urls[0], request_kwargs={"timeout": 8}))

    def _resolve_pair_address(
        self,
        pair_def: _PairDef,
        chain: str,
        symbol: str,
        discovered_address: str | None,
    ) -> str | None:
        from tokens import resolve_token_address

        if pair_def.pair_chain and pair_def.pair_chain != chain:
            return None
        if discovered_address and (pair_def.pair_chain is None or pair_def.pair_chain == chain):
            return discovered_address
        return resolve_token_address(chain, symbol)

    @staticmethod
    def _amount_in_for_symbol(symbol: str) -> int:
        return 10 ** token_decimals(symbol)

    @staticmethod
    def _price_from_amount_out(amount_out: int, quote_symbol: str) -> Decimal:
        return D(amount_out) / D(10 ** token_decimals(quote_symbol))

    def _rotate_rpc(self, chain: str) -> None:
        """Rotate to the next RPC endpoint for a chain after a failure.

        Uses round-robin (not random) to ensure even distribution across
        endpoints. If all RPCs fail, wraps back to index 0 (primary).
        """
        urls = self._rpc_urls.get(chain, [])
        if len(urls) <= 1:
            return
        idx = (self._rpc_index.get(chain, 0) + 1) % len(urls)
        self._rpc_index[chain] = idx
        new_url = urls[idx]
        self._w3[chain] = Web3(Web3.HTTPProvider(new_url, request_kwargs={"timeout": 8}))
        _logger.info("RPC failover for %s → %s", chain, new_url[:50])

    def _try_fee_tiers(
        self, cache_key: str, quoter, weth: str, usdc: str,
        amount_in: int, fee_tiers: tuple[int, ...],
    ) -> tuple[int, int]:
        """Try fee tiers with caching.

        Returns ``(best_amount_out, winning_fee_tier)``.  Returns ``(0, 0)``
        if all tiers fail.  The winning fee tier is in raw pool units
        (e.g. 500 = 5 bps, 3000 = 30 bps).

        On first call (or every 60s), tries all tiers and caches the best.
        On subsequent calls, tries only the cached tier (1 RPC instead of 4).
        """
        cached = self._best_fee.get(cache_key)
        weth_cs = Web3.to_checksum_address(weth)
        usdc_cs = Web3.to_checksum_address(usdc)

        def _call_tier(fee: int) -> int:
            result = quoter.functions.quoteExactInputSingle(
                (weth_cs, usdc_cs, amount_in, fee, 0)
            ).call()
            return result[0]

        # Use cached tier if fresh (< 5 min).  Fee tiers are immutable in V3
        # pools, so the "best" tier only changes if liquidity migrates between
        # pools — rare enough that 5-minute staleness is acceptable.
        # Saves 3 RPC calls per DEX per 5-minute window vs 60s TTL.
        if cached is not None and (_time.monotonic() - cached[1]) < 300.0:
            try:
                return _call_tier(cached[0]), cached[0]
            except Exception:
                pass  # Cached tier failed — fall through to full sweep.

        # Full sweep — try all tiers, cache the best.
        best_out = 0
        best_fee = 0
        for fee in fee_tiers:
            try:
                out = _call_tier(fee)
                if out > best_out:
                    best_out = out
                    best_fee = fee
            except Exception:
                continue
        if best_out > 0:
            self._best_fee[cache_key] = (best_fee, _time.monotonic())
        return best_out, best_fee

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_quotes(self) -> list[MarketQuote]:
        """Fetch quotes from all DEXs in parallel using thread pool.

        Each DEX quote is an independent RPC call, so parallel fetching
        reduces total latency from sum(latencies) to max(latencies).
        """
        def _fetch_one(dex: object, pair_def: _PairDef) -> MarketQuote:
            chain = dex.chain  # type: ignore[union-attr]
            dex_type = dex.dex_type  # type: ignore[union-attr]
            assert chain is not None and dex_type is not None

            base_addr = self._resolve_pair_address(
                pair_def, chain, pair_def.base_asset, pair_def.base_address,
            )
            quote_addr = self._resolve_pair_address(
                pair_def, chain, pair_def.quote_asset, pair_def.quote_address,
            )
            if not base_addr or not quote_addr:
                raise OnChainMarketError(
                    f"Cannot resolve {pair_def.pair_name} token addresses on {chain}."
                )

            def _do_quote() -> tuple[Decimal, Decimal]:
                if dex_type == "uniswap_v3":
                    return self._quote_uniswap_v3(
                        chain, base_addr, quote_addr, pair_def.base_asset, pair_def.quote_asset,
                    )
                elif dex_type == "sushi_v3":
                    return self._quote_sushi_v3(
                        chain, base_addr, quote_addr, pair_def.base_asset, pair_def.quote_asset,
                    )
                elif dex_type == "pancakeswap_v3":
                    return self._quote_pancakeswap_v3(
                        chain, base_addr, quote_addr, pair_def.base_asset, pair_def.quote_asset,
                    )
                elif dex_type == "balancer_v2":
                    return self._quote_balancer_v2(
                        chain, base_addr, quote_addr, pair_def.base_asset, pair_def.quote_asset,
                    )
                elif dex_type == "quickswap_v3":
                    return self._quote_quickswap_v3(
                        chain, base_addr, quote_addr, pair_def.base_asset, pair_def.quote_asset,
                    )
                elif dex_type == "camelot_v3":
                    return self._quote_camelot_v3(
                        chain, base_addr, quote_addr, pair_def.base_asset, pair_def.quote_asset,
                    )
                elif dex_type in ("velodrome_v2", "aerodrome"):
                    return self._quote_velodrome(
                        chain, base_addr, quote_addr, dex_type, pair_def.base_asset, pair_def.quote_asset,
                    )
                elif dex_type == "curve":
                    return self._quote_curve(
                        chain, pair_def.base_asset, pair_def.quote_asset,
                    )
                elif dex_type == "traderjoe_lb":
                    return self._quote_traderjoe_lb(
                        chain, base_addr, quote_addr, pair_def.base_asset, pair_def.quote_asset,
                    )
                raise OnChainMarketError(f"Unknown dex_type: {dex_type}")

            # Try once, on RPC failure rotate to next endpoint and retry once.
            try:
                mid, actual_fee_bps = _do_quote()
            except OnChainMarketError as e:
                # If quote returned zero, try bridged stablecoin as fallback.
                # Many Optimism/Arbitrum pools still use USDC.e/USDbC instead
                # of native USDC.
                if "returned zero" in str(e) and quote_addr:
                    from tokens import CHAIN_TOKENS
                    tokens = CHAIN_TOKENS.get(chain)
                    fallback = getattr(tokens, "usdc_e", None) if tokens else None
                    if fallback and fallback != quote_addr:
                        _logger.info(
                            "Trying USDC.e fallback for %s on %s",
                            dex.name, chain,  # type: ignore[union-attr]
                        )
                        quote_addr = fallback
                        mid, actual_fee_bps = _do_quote()
                    else:
                        raise
                else:
                    raise
            except Exception:
                self._rotate_rpc(chain)
                mid, actual_fee_bps = _do_quote()

            mid = _validate_price(
                mid,
                dex.name,  # type: ignore[union-attr]
                chain,
                pair_def.pair_name,
                pair_def.base_asset,
                pair_def.quote_asset,
            )

            # Estimate pool liquidity from price impact.
            try:
                estimated_tvl = self._estimate_liquidity_usd(
                    chain, base_addr, quote_addr, dex_type,
                    pair_def.base_asset, pair_def.quote_asset, mid,
                )
            except Exception:
                estimated_tvl = D("0")

            # On-chain quoters return output AFTER the pool fee is deducted.
            # Set fee_included=True so strategy.evaluate_pair skips its own
            # fee adjustment.  fee_bps carries the actual pool fee tier for
            # display/logging (not for further deduction).
            return MarketQuote(
                dex=dex.name,  # type: ignore[union-attr]
                pair=pair_def.pair_name,
                buy_price=mid,
                sell_price=mid,
                fee_bps=actual_fee_bps,
                fee_included=True,
                liquidity_usd=estimated_tvl,
            )

        # Fetch all DEX quotes in parallel.
        # Failed DEXs are logged and skipped — one bad RPC shouldn't kill the scan.
        # Low-liquidity pairs are cached and skipped for 3 hours.
        quotes: list[MarketQuote] = []
        cache = self.liquidity_cache

        # Filter out cached low-liquidity pairs before making RPC calls.
        active_requests: list[tuple[object, _PairDef, str, str]] = []
        for pair_def in self._pairs:
            for dex in self.config.dexes:
                chain = dex.chain or ""  # type: ignore[union-attr]
                cache_dex = f"{dex.name}:{pair_def.pair_name}"  # type: ignore[union-attr]
                if cache.should_skip(cache_dex, chain):
                    _logger.debug("Cache skip: %s on %s", cache_dex, chain)
                    if self.diagnostics:
                        from observability.quote_diagnostics import QuoteOutcome
                        self.diagnostics.record(
                            dex.name, chain, pair_def.pair_name, QuoteOutcome.CACHED_SKIP,  # type: ignore[union-attr]
                        )
                    continue
                active_requests.append((dex, pair_def, cache_dex, chain))

        if not active_requests:
            _logger.warning("All DEXes cached as low-liquidity — returning empty quotes")
            return quotes

        futures = {
            self._pool.submit(_fetch_one, dex, pair_def): (dex, pair_def, cache_dex, chain)
            for dex, pair_def, cache_dex, chain in active_requests
        }
        # Hard 15s deadline on ALL RPC calls.  Added after production
        # incidents where web3.py eth_call ignored the HTTP timeout
        # (8s per request_kwargs) and blocked the entire thread pool
        # indefinitely.  15s = enough for 2 retries on slow chains
        # (Alchemy P99 ~2s), short enough to keep scan cadence <30s.
        # See commit 0d8e09b ("fix: 15s hard deadline with pool.shutdown").
        done, not_done = concurrent.futures.wait(futures, timeout=15)
        for future in not_done:
            dex, pair_def, cache_dex, chain = futures[future]
            _logger.warning("RPC timeout (15s): %s on %s for %s", dex.name, dex.chain, pair_def.pair_name)
            future.cancel()
            cache.mark_skip(
                cache_dex, chain,
                "RPC call exceeded 15s deadline",
                ttl_override=15 * 60,
            )
            if self.diagnostics:
                from observability.quote_diagnostics import QuoteOutcome
                self.diagnostics.record(
                    dex.name, chain, pair_def.pair_name, QuoteOutcome.TIMEOUT,  # type: ignore[union-attr]
                )
        for future in done:
            dex, pair_def, cache_dex, chain = futures[future]
            try:
                quotes.append(future.result())
                if self.diagnostics:
                    from observability.quote_diagnostics import QuoteOutcome
                    self.diagnostics.record(
                        dex.name, chain, pair_def.pair_name, QuoteOutcome.SUCCESS,  # type: ignore[union-attr]
                    )
            except Exception as exc:
                _logger.warning(
                    "Skipping %s on %s for %s: %s",
                    dex.name, dex.chain, pair_def.pair_name, exc,  # type: ignore[union-attr]
                )
                err_str = str(exc).lower()
                is_transient = any(k in err_str for k in (
                    "timeout", "timed out", "429", "rate limit",
                    "connection", "refused", "reset",
                ))
                ttl = 15 * 60 if is_transient else None
                cache.mark_skip(
                    cache_dex, chain,
                    str(exc),
                    ttl_override=ttl,
                )
                if self.diagnostics:
                    from observability.quote_diagnostics import QuoteOutcome
                    outcome = QuoteOutcome.ZERO if "zero" in err_str else QuoteOutcome.ERROR
                    self.diagnostics.record(
                        dex.name, chain, pair_def.pair_name, outcome,  # type: ignore[union-attr]
                        error_msg=str(exc)[:200],
                    )

        return quotes

    # ------------------------------------------------------------------
    # Liquidity estimation
    # ------------------------------------------------------------------

    # Sentinel value for pools where small and normal quotes return the
    # same price (zero impact).  $100M is well above the scanner's $1M
    # filter threshold, so these pools always pass.  We use a sentinel
    # instead of infinity because Decimal("inf") causes issues downstream.
    _DEEP_POOL_TVL = D("100000000")  # $100M

    def _estimate_liquidity_usd(
        self,
        chain: str,
        base: str,
        quote: str,
        dex_type: str,
        base_symbol: str,
        quote_symbol: str,
        normal_price: Decimal,
    ) -> Decimal:
        """Estimate pool TVL from price impact between small and normal quotes.

        Why this approach instead of reading pool reserves on-chain:
          - Works uniformly across V3, Algebra, and Solidly AMMs
          - No need to know pool addresses (quoters handle routing)
          - Adds only 1 extra RPC call (the small-amount quote)
          - Gracefully degrades (returns 0 on failure → no filter triggered)

        Math (constant-product AMM approximation):
          impact = |small_price - normal_price| / small_price
          tvl ≈ trade_size_usd / (2 * impact)

        Why this formula works: in a constant-product pool (x * y = k),
        price impact for a trade of size Δx in a pool of depth L is
        approximately Δx / L.  Rearranging: L ≈ Δx / impact.  The factor
        of 2 accounts for both sides of the pool.

        Example: 0.01 WETH → $2344/WETH, 1 WETH → $2292/WETH → 2.2% impact
          tvl ≈ $2292 / (2 * 0.022) ≈ $52K — thin pool, rejected by scanner.

        Returns ``D("0")`` on failure (caller treats as "unknown, don't filter").
        This is intentional: we'd rather let an unverifiable quote through
        than block all quotes when the small-amount RPC call fails.
        """
        _ZERO = D("0")
        if normal_price <= _ZERO:
            return _ZERO

        # _quote_small_amount only supports these DEX types — all others
        # return D("0") immediately.  Skip the function call entirely.
        _SUPPORTED_LIQUIDITY = ("uniswap_v3", "sushi_v3", "pancakeswap_v3", "quickswap_v3")
        if dex_type not in _SUPPORTED_LIQUIDITY:
            return _ZERO

        # Check TVL cache — avoids extra RPC calls for liquidity estimation.
        # Deep pools (>$1M) use a longer TTL (30 min) since they don't dry up
        # suddenly. Thin pools use the default 5 min so we re-check sooner.
        tvl_key = f"{dex_type}:{chain}:{base}:{quote}".lower()
        cached = self._tvl_cache.get(tvl_key)
        if cached is not None:
            cached_tvl, cached_ts = cached
            ttl = 1800.0 if cached_tvl >= D("1000000") else self._TVL_CACHE_TTL
            if (_time.monotonic() - cached_ts) < ttl:
                return cached_tvl

        try:
            small_price = self._quote_small_amount(
                chain, base, quote, dex_type, base_symbol, quote_symbol,
            )
        except Exception:
            return _ZERO
        if small_price <= _ZERO:
            return _ZERO

        impact = abs(small_price - normal_price) / small_price
        if impact <= _ZERO:
            self._tvl_cache[tvl_key] = (self._DEEP_POOL_TVL, _time.monotonic())
            return self._DEEP_POOL_TVL

        trade_size_usd = normal_price  # 1 unit of base at this price
        tvl = trade_size_usd / (TWO * impact)
        # Cap at $10B to avoid absurd estimates from near-zero impact.
        tvl = min(tvl, D("10000000000"))
        self._tvl_cache[tvl_key] = (tvl, _time.monotonic())
        return tvl

    # ------------------------------------------------------------------
    # Price impact estimation
    # ------------------------------------------------------------------

    def _quote_small_amount(
        self, chain: str, base: str, quote: str, dex_type: str, base_symbol: str, quote_symbol: str
    ) -> Decimal:
        """Quote a tiny amount (0.01 WETH) to get the zero-impact reference price.

        Compares with the full 1 WETH quote to measure price impact.
        If 0.01 WETH → $22/unit and 1 WETH → $4/unit, the pool is thin.
        """
        # Use 1% of the base token's standard unit amount.
        SMALL_AMOUNT = self._amount_in_for_symbol(base_symbol) // 100

        w3 = self._w3[chain]
        quoter_addr = UNISWAP_V3_QUOTER_PER_CHAIN.get(chain, UNISWAP_V3_QUOTER_V2)

        if dex_type == "quickswap_v3":
            qaddr = QUICKSWAP_QUOTER.get(chain)
            if not qaddr:
                return D("0")
            quoter = w3.eth.contract(
                address=Web3.to_checksum_address(qaddr), abi=QUICKSWAP_QUOTER_ABI,
            )
            result = quoter.functions.quoteExactInputSingle(
                Web3.to_checksum_address(base), Web3.to_checksum_address(quote),
                SMALL_AMOUNT, 0,
            ).call()
            amount_out = result[0]
        elif dex_type in ("uniswap_v3", "sushi_v3", "pancakeswap_v3"):
            if dex_type == "sushi_v3":
                qaddr = SUSHI_V3_QUOTER.get(chain)
            elif dex_type == "pancakeswap_v3":
                qaddr = PANCAKE_V3_QUOTER.get(chain)
            else:
                qaddr = quoter_addr
            if not qaddr:
                return D("0")
            quoter = w3.eth.contract(
                address=Web3.to_checksum_address(qaddr),
                abi=UNISWAP_V3_QUOTER_ABI,
            )
            # Reuse the cached best fee tier from the main quote (set by
            # _try_fee_tiers).  This avoids sweeping all 4 tiers again,
            # saving 3 RPC calls per DEX per liquidity estimation.
            fee_cache_key = f"{dex_type}:{chain}:{base_symbol}/{quote_symbol}"
            cached_fee = self._best_fee.get(fee_cache_key)
            if cached_fee is not None:
                fee_tier = cached_fee[0]
                try:
                    result = quoter.functions.quoteExactInputSingle((
                        Web3.to_checksum_address(base),
                        Web3.to_checksum_address(quote),
                        SMALL_AMOUNT, fee_tier, 0,
                    )).call()
                    amount_out = result[0]
                except Exception:
                    amount_out = 0
            else:
                # No cached tier yet — fall back to full sweep.
                best_out = 0
                for fee in (100, 500, 3000, 10000):
                    try:
                        result = quoter.functions.quoteExactInputSingle((
                            Web3.to_checksum_address(base),
                            Web3.to_checksum_address(quote),
                            SMALL_AMOUNT, fee, 0,
                        )).call()
                        if result[0] > best_out:
                            best_out = result[0]
                    except Exception:
                        continue
                amount_out = best_out
        else:
            return D("0")

        if amount_out == 0:
            return D("0")
        # Return per-unit price (scale up by 100 since we quoted 1% of a token).
        return self._price_from_amount_out(amount_out, quote_symbol) * D("100")

    def _quote_large_amount(
        self, chain: str, base: str, quote: str, dex_type: str, base_symbol: str, quote_symbol: str
    ) -> Decimal:
        """Quote 10 WETH to detect thin pools via price impact.

        If the per-unit price at 10 WETH is significantly worse than at 1 WETH,
        the pool has insufficient liquidity for real execution.
        """
        LARGE_AMOUNT = 10 * self._amount_in_for_symbol(base_symbol)

        w3 = self._w3[chain]

        if dex_type == "quickswap_v3":
            qaddr = QUICKSWAP_QUOTER.get(chain)
            if not qaddr:
                return D("0")
            quoter = w3.eth.contract(
                address=Web3.to_checksum_address(qaddr), abi=QUICKSWAP_QUOTER_ABI,
            )
            result = quoter.functions.quoteExactInputSingle(
                Web3.to_checksum_address(base), Web3.to_checksum_address(quote),
                LARGE_AMOUNT, 0,
            ).call()
            amount_out = result[0]
        elif dex_type in ("uniswap_v3", "sushi_v3", "pancakeswap_v3"):
            if dex_type == "sushi_v3":
                qaddr = SUSHI_V3_QUOTER.get(chain)
            elif dex_type == "pancakeswap_v3":
                qaddr = PANCAKE_V3_QUOTER.get(chain)
            else:
                qaddr = UNISWAP_V3_QUOTER_PER_CHAIN.get(chain, UNISWAP_V3_QUOTER_V2)
            if not qaddr:
                return D("0")
            quoter = w3.eth.contract(
                address=Web3.to_checksum_address(qaddr),
                abi=UNISWAP_V3_QUOTER_ABI if dex_type != "sushi_v3" else SUSHI_V3_QUOTER_ABI,
            )
            best_out = 0
            for fee in (100, 500, 3000, 10000):
                try:
                    result = quoter.functions.quoteExactInputSingle((
                        Web3.to_checksum_address(base),
                        Web3.to_checksum_address(quote),
                        LARGE_AMOUNT, fee, 0,
                    )).call()
                    if result[0] > best_out:
                        best_out = result[0]
                except Exception:
                    continue
            amount_out = best_out
        elif dex_type == "camelot_v3":
            qaddr = CAMELOT_QUOTER.get(chain)
            if not qaddr:
                return D("0")
            quoter = w3.eth.contract(
                address=Web3.to_checksum_address(qaddr), abi=QUICKSWAP_QUOTER_ABI,
            )
            result = quoter.functions.quoteExactInputSingle(
                Web3.to_checksum_address(base), Web3.to_checksum_address(quote),
                LARGE_AMOUNT, 0,
            ).call()
            amount_out = result[0]
        elif dex_type in ("velodrome_v2", "aerodrome"):
            router_addr = (AERODROME_ROUTER if dex_type == "aerodrome" else VELODROME_ROUTER).get(chain)
            factory = VELO_FACTORY.get(chain)
            if not router_addr or not factory:
                return D("0")
            router = w3.eth.contract(
                address=Web3.to_checksum_address(router_addr), abi=VELO_ROUTER_ABI,
            )
            best_out, _ = self._velo_best_route(router, LARGE_AMOUNT, base, quote, factory)
            if best_out == 0 and quote_symbol.upper() in ("USDC", "USDT"):
                from tokens import bridged_usdc_address
                bridged = bridged_usdc_address(chain)
                if bridged and bridged.lower() != quote.lower():
                    best_out, _ = self._velo_best_route(router, LARGE_AMOUNT, base, bridged, factory)
            amount_out = best_out
        else:
            return D("0")

        if amount_out == 0:
            return D("0")
        # Return per-unit price (divide by 10 since we quoted 10 units).
        return self._price_from_amount_out(amount_out, quote_symbol) / D("10")

    # ------------------------------------------------------------------
    # DEX-specific quoting
    # ------------------------------------------------------------------

    def _quote_uniswap_v3(
        self, chain: str, base: str, quote: str, base_symbol: str, quote_symbol: str
    ) -> tuple[Decimal, Decimal]:
        """Get WETH/USDC price from Uniswap V3 QuoterV2.

        Returns ``(price, actual_fee_bps)``.  The price already has the pool
        fee deducted (the quoter simulates the real swap).

        Fee tiers: 100 (1 bps), 500 (5 bps), 3000 (30 bps), 10000 (100 bps).
        """
        w3 = self._w3[chain]
        quoter_addr = UNISWAP_V3_QUOTER_PER_CHAIN.get(chain, UNISWAP_V3_QUOTER_V2)
        quoter = w3.eth.contract(
            address=Web3.to_checksum_address(quoter_addr),
            abi=UNISWAP_V3_QUOTER_ABI,
        )
        amount_in = self._amount_in_for_symbol(base_symbol)
        best_out, fee_tier = self._try_fee_tiers(
            f"uniswap_v3:{chain}:{base_symbol}/{quote_symbol}", quoter, base, quote,
            amount_in, (100, 500, 3000, 10000),
        )
        if best_out == 0:
            raise OnChainMarketError(
                f"Uniswap V3 returned zero for all fee tiers on {chain}."
            )
        actual_fee_bps = D(fee_tier) / D("100")
        return self._price_from_amount_out(best_out, quote_symbol), actual_fee_bps

    def _quote_sushi_v3(
        self, chain: str, base: str, quote: str, base_symbol: str, quote_symbol: str
    ) -> tuple[Decimal, Decimal]:
        """Get WETH/USDC price from SushiSwap V3 QuoterV2.

        Returns ``(price, actual_fee_bps)``.
        """
        quoter_addr = SUSHI_V3_QUOTER.get(chain)
        if quoter_addr is None:
            raise OnChainMarketError(
                f"No SushiSwap V3 quoter address for chain '{chain}'."
            )

        w3 = self._w3[chain]
        quoter = w3.eth.contract(
            address=Web3.to_checksum_address(quoter_addr),
            abi=SUSHI_V3_QUOTER_ABI,
        )
        amount_in = self._amount_in_for_symbol(base_symbol)
        best_out, fee_tier = self._try_fee_tiers(
            f"sushi_v3:{chain}:{base_symbol}/{quote_symbol}", quoter, base, quote,
            amount_in, (100, 500, 3000, 10000),
        )
        if best_out == 0:
            raise OnChainMarketError(
                f"SushiSwap V3 returned zero for all fee tiers on {chain}."
            )
        actual_fee_bps = D(fee_tier) / D("100")
        return self._price_from_amount_out(best_out, quote_symbol), actual_fee_bps

    def _quote_pancakeswap_v3(
        self, chain: str, base: str, quote: str, base_symbol: str, quote_symbol: str
    ) -> tuple[Decimal, Decimal]:
        """Get WETH/USDC price from PancakeSwap V3 QuoterV2.

        Returns ``(price, actual_fee_bps)``.
        """
        quoter_addr = PANCAKE_V3_QUOTER.get(chain)
        if quoter_addr is None:
            raise OnChainMarketError(
                f"No PancakeSwap V3 quoter address for chain '{chain}'."
            )

        w3 = self._w3[chain]
        quoter = w3.eth.contract(
            address=Web3.to_checksum_address(quoter_addr),
            abi=PANCAKE_V3_QUOTER_ABI,
        )
        amount_in = self._amount_in_for_symbol(base_symbol)
        best_out, fee_tier = self._try_fee_tiers(
            f"pancakeswap_v3:{chain}:{base_symbol}/{quote_symbol}", quoter, base, quote,
            amount_in, (100, 500, 2500, 10000),
        )
        if best_out == 0:
            raise OnChainMarketError(
                f"PancakeSwap V3 returned zero for all fee tiers on {chain}."
            )
        actual_fee_bps = D(fee_tier) / D("100")
        return self._price_from_amount_out(best_out, quote_symbol), actual_fee_bps

    def _quote_balancer_v2(
        self, chain: str, base: str, quote: str, base_symbol: str, quote_symbol: str
    ) -> tuple[Decimal, Decimal]:
        """Get WETH/USDC price from Balancer V2 Vault queryBatchSwap.

        Returns ``(price, fee_bps)``.  Balancer fees are dynamic per pool;
        the returned fee_bps is an estimate (typically 10-30 bps).
        """
        pool_id = BALANCER_POOL_IDS.get(chain)
        if pool_id is None:
            raise OnChainMarketError(
                f"No Balancer pool ID configured for chain '{chain}'."
            )

        w3 = self._w3[chain]
        vault = w3.eth.contract(
            address=Web3.to_checksum_address(BALANCER_VAULT),
            abi=BALANCER_VAULT_ABI,
        )

        weth_cs = Web3.to_checksum_address(base)
        usdc_cs = Web3.to_checksum_address(quote)

        # Assets must be sorted by address for Balancer.
        assets = sorted([weth_cs, usdc_cs])
        weth_index = assets.index(weth_cs)
        usdc_index = assets.index(usdc_cs)

        amount_in = self._amount_in_for_symbol(base_symbol)

        # kind=0 is GIVEN_IN
        result = vault.functions.queryBatchSwap(
            0,  # SwapKind.GIVEN_IN
            [
                (
                    bytes.fromhex(pool_id[2:]) if pool_id.startswith("0x") else bytes.fromhex(pool_id),
                    weth_index,
                    usdc_index,
                    amount_in,
                    b"",
                )
            ],
            assets,
            (
                "0x0000000000000000000000000000000000000000",  # sender
                False,
                "0x0000000000000000000000000000000000000000",  # recipient
                False,
            ),
        ).call()

        # result is int256[] — deltas per asset.  Positive = tokens going IN,
        # negative = tokens coming OUT.  The USDC delta should be negative.
        usdc_delta = result[usdc_index]
        if usdc_delta >= 0:
            raise OnChainMarketError(
                f"Balancer queryBatchSwap returned non-negative USDC delta: {usdc_delta}"
            )
        amount_out = abs(usdc_delta)
        return self._price_from_amount_out(amount_out, quote_symbol), D("10")  # ~10 bps typical

    def _quote_quickswap_v3(
        self, chain: str, base: str, quote: str, base_symbol: str, quote_symbol: str
    ) -> tuple[Decimal, Decimal]:
        """Get WETH/USDC price from QuickSwap V3 (Algebra) Quoter.

        Returns ``(price, fee_bps)``.  Algebra uses dynamic fees; we report
        an estimate.
        """
        quoter_addr = QUICKSWAP_QUOTER.get(chain)
        if quoter_addr is None:
            raise OnChainMarketError(
                f"No QuickSwap quoter address for chain '{chain}'."
            )

        w3 = self._w3[chain]
        quoter = w3.eth.contract(
            address=Web3.to_checksum_address(quoter_addr),
            abi=QUICKSWAP_QUOTER_ABI,
        )
        amount_in = self._amount_in_for_symbol(base_symbol)

        result = quoter.functions.quoteExactInputSingle(
            Web3.to_checksum_address(base),
            Web3.to_checksum_address(quote),
            amount_in,
            0,  # limitSqrtPrice = 0 means no limit
        ).call()

        amount_out = result[0]
        if amount_out == 0:
            raise OnChainMarketError(
                f"QuickSwap returned zero on {chain}."
            )
        return self._price_from_amount_out(amount_out, quote_symbol), D("15")  # ~15 bps typical

    def _quote_camelot_v3(
        self, chain: str, base: str, quote: str, base_symbol: str, quote_symbol: str
    ) -> tuple[Decimal, Decimal]:
        """Get price from Camelot V3 (Algebra) Quoter on Arbitrum.

        Returns ``(price, fee_bps)``.  Algebra dynamic fee; estimate reported.
        Tries Algebra V1 ABI (individual args) first, then V2 (struct args).
        """
        quoter_addr = CAMELOT_QUOTER.get(chain)
        if quoter_addr is None:
            raise OnChainMarketError(
                f"No Camelot quoter address for chain '{chain}'."
            )

        w3 = self._w3[chain]
        amount_in = self._amount_in_for_symbol(base_symbol)
        base_cs = Web3.to_checksum_address(base)
        quote_cs = Web3.to_checksum_address(quote)

        # Try Algebra V1 ABI (individual args — QuickSwap style).
        try:
            quoter_v1 = w3.eth.contract(
                address=Web3.to_checksum_address(quoter_addr),
                abi=QUICKSWAP_QUOTER_ABI,
            )
            result = quoter_v1.functions.quoteExactInputSingle(
                base_cs, quote_cs, amount_in, 0,
            ).call()
            amount_out = result[0]
            if amount_out > 0:
                return self._price_from_amount_out(amount_out, quote_symbol), D("15")
        except Exception:
            pass

        # Try Algebra V2 ABI (struct args — newer Camelot deployments).
        try:
            quoter_v2 = w3.eth.contract(
                address=Web3.to_checksum_address(quoter_addr),
                abi=ALGEBRA_V2_QUOTER_ABI,
            )
            result = quoter_v2.functions.quoteExactInputSingle(
                (base_cs, quote_cs, amount_in, 0),
            ).call()
            amount_out = result[0]
            if amount_out > 0:
                return self._price_from_amount_out(amount_out, quote_symbol), D("15")
        except Exception:
            pass

        raise OnChainMarketError(f"Camelot returned zero on {chain} (tried V1 + V2 ABI).")

    @staticmethod
    def _velo_best_route(
        router, amount_in: int, base: str, quote: str, factory: str,
    ) -> tuple[int, bool]:
        """Try volatile + stable Solidly routes, return (best_out, best_stable)."""
        best_out = 0
        best_stable = False
        for stable in [False, True]:
            try:
                route = (
                    Web3.to_checksum_address(base),
                    Web3.to_checksum_address(quote),
                    stable,
                    Web3.to_checksum_address(factory),
                )
                amounts = router.functions.getAmountsOut(amount_in, [route]).call()
                if len(amounts) >= 2 and amounts[-1] > best_out:
                    best_out = amounts[-1]
                    best_stable = stable
            except Exception:
                continue
        return best_out, best_stable

    def _quote_velodrome(
        self, chain: str, base: str, quote: str, dex_type: str, base_symbol: str, quote_symbol: str
    ) -> tuple[Decimal, Decimal]:
        """Get price from Velodrome V2 (Optimism) or Aerodrome (Base).

        Returns ``(price, fee_bps)``.  Uses getAmountsOut with a Route struct.
        Tries both volatile and stable pool types, returns the best.
        Falls back to bridged USDC (USDC.e / USDbC) if native USDC returns zero.
        """
        if dex_type == "aerodrome":
            router_addr = AERODROME_ROUTER.get(chain)
        else:
            router_addr = VELODROME_ROUTER.get(chain)
        if router_addr is None:
            raise OnChainMarketError(
                f"No {'Aerodrome' if dex_type == 'aerodrome' else 'Velodrome'} "
                f"router for chain '{chain}'."
            )

        factory = VELO_FACTORY.get(chain)
        if factory is None:
            raise OnChainMarketError(f"No Velo factory for chain '{chain}'.")

        w3 = self._w3[chain]
        router = w3.eth.contract(
            address=Web3.to_checksum_address(router_addr),
            abi=VELO_ROUTER_ABI,
        )
        amount_in = self._amount_in_for_symbol(base_symbol)

        best_out, best_stable = self._velo_best_route(
            router, amount_in, base, quote, factory,
        )

        # Fallback: try bridged USDC if native returned zero.
        if best_out == 0 and quote_symbol.upper() in ("USDC", "USDT"):
            from tokens import bridged_usdc_address
            bridged = bridged_usdc_address(chain)
            if bridged and bridged.lower() != quote.lower():
                best_out, best_stable = self._velo_best_route(
                    router, amount_in, base, bridged, factory,
                )

        dex_label = "Aerodrome" if dex_type == "aerodrome" else "Velodrome"
        if best_out == 0:
            raise OnChainMarketError(f"{dex_label} returned zero on {chain}.")

        fee_bps = D("2") if best_stable else D("20")
        return self._price_from_amount_out(best_out, quote_symbol), fee_bps

    def _quote_curve(
        self, chain: str, base_symbol: str, quote_symbol: str
    ) -> tuple[Decimal, Decimal]:
        """Get price from Curve StableSwap pool via get_dy.

        Returns ``(price, fee_bps)``.  Curve pools charge ~1-4 bps on
        stablecoin swaps.  get_dy returns the output amount after fees.
        """
        pair_key = f"{base_symbol}/{quote_symbol}"
        reverse_key = f"{quote_symbol}/{base_symbol}"
        chain_pools = CURVE_POOLS.get(chain, {})
        pool_addr = chain_pools.get(pair_key) or chain_pools.get(reverse_key)
        if pool_addr is None:
            raise OnChainMarketError(
                f"No Curve pool for {pair_key} on {chain}."
            )

        indices = CURVE_TOKEN_INDEX.get(pool_addr, {})
        i = indices.get(base_symbol)
        j = indices.get(quote_symbol)
        if i is None or j is None:
            raise OnChainMarketError(
                f"Cannot resolve Curve token indices for {pair_key} in pool {pool_addr}."
            )

        w3 = self._w3[chain]
        pool = w3.eth.contract(
            address=Web3.to_checksum_address(pool_addr),
            abi=CURVE_POOL_ABI,
        )

        amount_in = self._amount_in_for_symbol(base_symbol)
        amount_out = pool.functions.get_dy(i, j, amount_in).call()
        if amount_out == 0:
            raise OnChainMarketError(f"Curve get_dy returned zero on {chain}.")

        return self._price_from_amount_out(amount_out, quote_symbol), D("4")  # ~4 bps typical

    def _quote_traderjoe_lb(
        self, chain: str, base: str, quote: str, base_symbol: str, quote_symbol: str
    ) -> tuple[Decimal, Decimal]:
        """Get price from TraderJoe V2.1 LBQuoter (Liquidity Book).

        Returns ``(price, fee_bps)``.  Uses findBestPathFromAmountIn which
        searches across bin steps for the best route.
        """
        quoter_addr = TRADERJOE_LB_QUOTER.get(chain)
        if quoter_addr is None:
            raise OnChainMarketError(
                f"No TraderJoe LB quoter for chain '{chain}'."
            )

        w3 = self._w3[chain]
        quoter = w3.eth.contract(
            address=Web3.to_checksum_address(quoter_addr),
            abi=TRADERJOE_LB_QUOTER_ABI,
        )
        amount_in = self._amount_in_for_symbol(base_symbol)

        result = quoter.functions.findBestPathFromAmountIn(
            [Web3.to_checksum_address(base), Web3.to_checksum_address(quote)],
            amount_in,
        ).call()

        # result is a Quote struct; amounts[-1] is the final output amount.
        amounts = result[4] if len(result) > 4 else []
        if not amounts or amounts[-1] == 0:
            raise OnChainMarketError(
                f"TraderJoe LB returned zero on {chain}."
            )

        return self._price_from_amount_out(amounts[-1], quote_symbol), D("15")  # ~15 bps typical
