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
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal

from web3 import Web3

from config import BotConfig
import logging

from contracts import (
    AERODROME_ROUTER,
    BALANCER_POOL_IDS,
    BALANCER_VAULT,
    BALANCER_VAULT_ABI,
    CAMELOT_QUOTER,
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
from models import BPS_DIVISOR, MarketQuote
from tokens import CHAIN_TOKENS

D = Decimal
TWO = D("2")

SUPPORTED_DEX_TYPES = (
    "uniswap_v3", "sushi_v3", "pancakeswap_v3", "balancer_v2", "quickswap_v3",
    "camelot_v3", "velodrome_v2", "aerodrome",
)

# Token decimals used when converting raw uint256 amounts.
WETH_DECIMALS = 18
USDC_DECIMALS = 6

# Sanity bounds: reject quotes outside this range for WETH/USDC.
# If 1 WETH returns less than $100 or more than $100K in USDC,
# the pool is illiquid or the fee tier is wrong.
MIN_SANE_PRICE = D("100")
MAX_SANE_PRICE = D("100000")


class OnChainMarketError(Exception):
    """Raised when an on-chain query fails."""


def _validate_price(price: Decimal, dex: str, chain: str) -> Decimal:
    """Reject quotes that are obviously wrong (illiquid pool / wrong fee tier).

    Bounds [$100, $100K] for WETH/USDC cover all reasonable market conditions.
    Catches: Sushi returning $39 (wrong fee tier), PancakeSwap BSC returning
    $2 trillion (WETH decimal mismatch on BSC), pools with dust liquidity.
    """
    if price < MIN_SANE_PRICE:
        raise OnChainMarketError(
            f"{dex} on {chain} returned ${float(price):.2f} — below minimum ${float(MIN_SANE_PRICE)}"
        )
    if price > MAX_SANE_PRICE:
        raise OnChainMarketError(
            f"{dex} on {chain} returned ${float(price):.2f} — above maximum ${float(MAX_SANE_PRICE)}"
        )
    return price


class OnChainMarket:
    """Query DEX smart contracts for real per-DEX price quotes."""

    def __init__(
        self,
        config: BotConfig,
        rpc_overrides: dict[str, str] | None = None,
        liquidity_cache: LiquidityCache | None = None,
    ) -> None:
        self.config = config
        self._rpc_overrides = rpc_overrides or {}
        self.liquidity_cache = liquidity_cache or LiquidityCache()

        # Pre-build web3 instances keyed by chain, with failover URLs.
        self._w3: dict[str, Web3] = {}
        self._rpc_urls: dict[str, list[str]] = {}  # chain → list of URLs for failover
        self._rpc_index: dict[str, int] = {}  # chain → current URL index
        # Cache best fee tier per (dex_type, chain) — avoids trying all 4 tiers each scan.
        self._best_fee: dict[str, tuple[int, float]] = {}  # key → (fee, timestamp)

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
    ) -> int:
        """Try fee tiers with caching. Returns best amount_out (0 if all fail).

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

        # Use cached tier if fresh (< 60s old).
        if cached is not None and (_time.monotonic() - cached[1]) < 60.0:
            try:
                return _call_tier(cached[0])
            except Exception:
                pass  # Cached tier failed — fall through to full sweep.

        # Full sweep — try all tiers, cache the best.
        best_out = 0
        best_fee = fee_tiers[0]
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
        return best_out

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_quotes(self) -> list[MarketQuote]:
        """Fetch quotes from all DEXs in parallel using thread pool.

        Each DEX quote is an independent RPC call, so parallel fetching
        reduces total latency from sum(latencies) to max(latencies).
        """
        from tokens import resolve_token_address

        def _fetch_one(dex: object) -> MarketQuote:
            chain = dex.chain  # type: ignore[union-attr]
            dex_type = dex.dex_type  # type: ignore[union-attr]
            assert chain is not None and dex_type is not None

            base_addr = resolve_token_address(chain, self.config.base_asset)
            quote_addr = resolve_token_address(chain, self.config.quote_asset)
            if not base_addr or not quote_addr:
                raise OnChainMarketError(
                    f"Cannot resolve {self.config.pair} token addresses on {chain}."
                )

            def _do_quote() -> Decimal:
                if dex_type == "uniswap_v3":
                    return self._quote_uniswap_v3(chain, base_addr, quote_addr)
                elif dex_type == "sushi_v3":
                    return self._quote_sushi_v3(chain, base_addr, quote_addr)
                elif dex_type == "pancakeswap_v3":
                    return self._quote_pancakeswap_v3(chain, base_addr, quote_addr)
                elif dex_type == "balancer_v2":
                    return self._quote_balancer_v2(chain, base_addr, quote_addr)
                elif dex_type == "quickswap_v3":
                    return self._quote_quickswap_v3(chain, base_addr, quote_addr)
                elif dex_type == "camelot_v3":
                    return self._quote_camelot_v3(chain, base_addr, quote_addr)
                elif dex_type in ("velodrome_v2", "aerodrome"):
                    return self._quote_velodrome(chain, base_addr, quote_addr, dex_type)
                raise OnChainMarketError(f"Unknown dex_type: {dex_type}")

            # Try once, on RPC failure rotate to next endpoint and retry once.
            # Single retry balances speed vs resilience: catches transient RPC
            # issues without adding latency for persistent failures (chain down).
            try:
                mid = _do_quote()
            except OnChainMarketError:
                raise
            except Exception:
                self._rotate_rpc(chain)
                mid = _do_quote()

            mid = _validate_price(mid, dex.name, chain)  # type: ignore[union-attr]

            # Skip price impact check — doubles RPC calls per DEX and causes
            # hangs on rate-limited RPCs. The outlier filter in bot.py catches
            # most bad quotes, and the min_spread_pct rule in risk policy
            # rejects thin spreads.
            estimated_liquidity = D("0")

            # Model bid-ask spread as symmetric around mid: buy = mid + half, sell = mid - half.
            # The DEX fee tier approximates the full spread (market maker compensation).
            # In reality buy/sell may differ, but symmetric is acceptable for arb detection.
            half_spread = mid * (dex.fee_bps / BPS_DIVISOR / TWO)  # type: ignore[union-attr]
            return MarketQuote(
                dex=dex.name,  # type: ignore[union-attr]
                pair=self.config.pair,
                buy_price=mid + half_spread,
                sell_price=mid - half_spread,
                fee_bps=dex.fee_bps,  # type: ignore[union-attr]
                liquidity_usd=estimated_liquidity,
            )

        # Fetch all DEX quotes in parallel.
        # Failed DEXs are logged and skipped — one bad RPC shouldn't kill the scan.
        # Low-liquidity pairs are cached and skipped for 3 hours.
        quotes: list[MarketQuote] = []
        cache = self.liquidity_cache

        # Filter out cached low-liquidity pairs before making RPC calls.
        active_dexes = []
        for dex in self.config.dexes:
            if cache.should_skip(dex.name, dex.chain or ""):  # type: ignore[union-attr]
                _logger.debug("Cache skip: %s on %s", dex.name, dex.chain)
            else:
                active_dexes.append(dex)

        if not active_dexes:
            _logger.warning("All DEXes cached as low-liquidity — returning empty quotes")
            return quotes

        pool = ThreadPoolExecutor(max_workers=len(active_dexes))
        try:
            futures = {pool.submit(_fetch_one, dex): dex for dex in active_dexes}
            # Hard 15s deadline — prevents indefinite scan hangs.
            import concurrent.futures
            done, not_done = concurrent.futures.wait(futures, timeout=15)
            for future in not_done:
                dex = futures[future]
                _logger.warning("RPC timeout (15s): %s on %s", dex.name, dex.chain)
                future.cancel()
                cache.mark_skip(
                    dex.name, dex.chain or "",
                    "RPC call exceeded 15s deadline",
                    ttl_override=15 * 60,
                )
            for future in done:
                dex = futures[future]
                try:
                    quotes.append(future.result())
                except Exception as exc:
                    _logger.warning(
                        "Skipping %s on %s: %s",
                        dex.name, dex.chain, exc,  # type: ignore[union-attr]
                    )
                    # Use shorter TTL for transient errors (timeout, rate limit)
                    # so we retry sooner. Permanent errors (zero quotes, thin pool)
                    # use the default 3h TTL.
                    err_str = str(exc).lower()
                    is_transient = any(k in err_str for k in (
                        "timeout", "timed out", "429", "rate limit",
                        "connection", "refused", "reset",
                    ))
                    ttl = 15 * 60 if is_transient else None  # 15 min or default 3h
                    cache.mark_skip(
                        dex.name, dex.chain or "",  # type: ignore[union-attr]
                        str(exc),
                        ttl_override=ttl,
                    )
        finally:
            pool.shutdown(wait=False)  # Don't wait for hung threads

        return quotes

    # ------------------------------------------------------------------
    # Price impact estimation
    # ------------------------------------------------------------------

    def _quote_small_amount(
        self, chain: str, base: str, quote: str, dex_type: str
    ) -> Decimal:
        """Quote a tiny amount (0.01 WETH) to get the zero-impact reference price.

        Compares with the full 1 WETH quote to measure price impact.
        If 0.01 WETH → $22/unit and 1 WETH → $4/unit, the pool is thin.
        """
        # Use 1% of standard amount (0.01 WETH = 10^16 wei).
        SMALL_AMOUNT = 10 ** (WETH_DECIMALS - 2)  # 0.01 WETH

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
        # Return per-unit price (scale up by 100 since we quoted 0.01 WETH).
        return D(amount_out) * D("100") / D(10 ** USDC_DECIMALS)

    def _quote_large_amount(
        self, chain: str, base: str, quote: str, dex_type: str
    ) -> Decimal:
        """Quote 10 WETH to detect thin pools via price impact.

        If the per-unit price at 10 WETH is significantly worse than at 1 WETH,
        the pool has insufficient liquidity for real execution.
        """
        LARGE_AMOUNT = 10 * (10 ** WETH_DECIMALS)  # 10 WETH

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
            best_out = 0
            for stable in [False, True]:
                try:
                    route = (Web3.to_checksum_address(base), Web3.to_checksum_address(quote), stable, Web3.to_checksum_address(factory))
                    amounts = router.functions.getAmountsOut(LARGE_AMOUNT, [route]).call()
                    if len(amounts) >= 2 and amounts[-1] > best_out:
                        best_out = amounts[-1]
                except Exception:
                    continue
            amount_out = best_out
        else:
            return D("0")

        if amount_out == 0:
            return D("0")
        # Return per-unit price (divide by 10 since we quoted 10 WETH).
        return D(amount_out) / D("10") / D(10 ** USDC_DECIMALS)

    # ------------------------------------------------------------------
    # DEX-specific quoting
    # ------------------------------------------------------------------

    def _quote_uniswap_v3(
        self, chain: str, weth: str, usdc: str
    ) -> Decimal:
        """Get WETH/USDC mid-price from Uniswap V3 QuoterV2.

        Tries all standard Uniswap V3 fee tiers and returns the best quote
        (highest output = deepest liquidity pool for this pair).

        Fee tiers: 100 (0.01%), 500 (0.05%), 3000 (0.30%), 10000 (1.00%).
        Liquidity varies by chain: Ethereum majors concentrate in 500 bps,
        Polygon in 500, Arbitrum in 3000. We try all and pick the best.
        """
        w3 = self._w3[chain]
        quoter_addr = UNISWAP_V3_QUOTER_PER_CHAIN.get(chain, UNISWAP_V3_QUOTER_V2)
        quoter = w3.eth.contract(
            address=Web3.to_checksum_address(quoter_addr),
            abi=UNISWAP_V3_QUOTER_ABI,
        )
        amount_in = 10 ** WETH_DECIMALS
        best_out = self._try_fee_tiers(
            f"uniswap_v3:{chain}", quoter, weth, usdc,
            amount_in, (100, 500, 3000, 10000),
        )
        if best_out == 0:
            raise OnChainMarketError(
                f"Uniswap V3 returned zero for all fee tiers on {chain}."
            )
        return D(best_out) / D(10 ** USDC_DECIMALS)

    def _quote_sushi_v3(
        self, chain: str, weth: str, usdc: str
    ) -> Decimal:
        """Get WETH/USDC mid-price from SushiSwap V3 QuoterV2.

        Tries all standard fee tiers and returns the best quote.
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
        amount_in = 10 ** WETH_DECIMALS
        best_out = self._try_fee_tiers(
            f"sushi_v3:{chain}", quoter, weth, usdc,
            amount_in, (100, 500, 3000, 10000),
        )
        if best_out == 0:
            raise OnChainMarketError(
                f"SushiSwap V3 returned zero for all fee tiers on {chain}."
            )
        return D(best_out) / D(10 ** USDC_DECIMALS)

    def _quote_pancakeswap_v3(
        self, chain: str, weth: str, usdc: str
    ) -> Decimal:
        """Get WETH/USDC mid-price from PancakeSwap V3 QuoterV2.

        PancakeSwap V3 is a Uniswap V3 fork — same QuoterV2 interface,
        different contract addresses.  This is the video's recommended
        second DEX alongside Uniswap.

        Tries multiple fee tiers and returns the best quote (most output),
        since different pairs have liquidity concentrated in different tiers.
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
        amount_in = 10 ** WETH_DECIMALS
        best_out = self._try_fee_tiers(
            f"pancakeswap_v3:{chain}", quoter, weth, usdc,
            amount_in, (100, 500, 2500, 10000),
        )
        if best_out == 0:
            raise OnChainMarketError(
                f"PancakeSwap V3 returned zero for all fee tiers on {chain}."
            )

        return D(best_out) / D(10 ** USDC_DECIMALS)

    def _quote_balancer_v2(
        self, chain: str, weth: str, usdc: str
    ) -> Decimal:
        """Get WETH/USDC mid-price from Balancer V2 Vault queryBatchSwap."""
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

        weth_cs = Web3.to_checksum_address(weth)
        usdc_cs = Web3.to_checksum_address(usdc)

        # Assets must be sorted by address for Balancer.
        assets = sorted([weth_cs, usdc_cs])
        weth_index = assets.index(weth_cs)
        usdc_index = assets.index(usdc_cs)

        amount_in = 10 ** WETH_DECIMALS  # 1 WETH

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
        return D(amount_out) / D(10 ** USDC_DECIMALS)

    def _quote_quickswap_v3(
        self, chain: str, weth: str, usdc: str
    ) -> Decimal:
        """Get WETH/USDC mid-price from QuickSwap V3 (Algebra) Quoter.

        QuickSwap uses Algebra protocol — different interface from Uniswap V3:
        no fee parameter (auto-detected from pool), flat args (not tuple).
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
        amount_in = 10 ** WETH_DECIMALS

        result = quoter.functions.quoteExactInputSingle(
            Web3.to_checksum_address(weth),
            Web3.to_checksum_address(usdc),
            amount_in,
            0,  # limitSqrtPrice = 0 means no limit
        ).call()

        amount_out = result[0]
        if amount_out == 0:
            raise OnChainMarketError(
                f"QuickSwap returned zero on {chain}."
            )
        return D(amount_out) / D(10 ** USDC_DECIMALS)

    def _quote_camelot_v3(
        self, chain: str, weth: str, usdc: str
    ) -> Decimal:
        """Get mid-price from Camelot V3 (Algebra) Quoter on Arbitrum.

        Same interface as QuickSwap — Algebra protocol, no fee parameter.
        """
        quoter_addr = CAMELOT_QUOTER.get(chain)
        if quoter_addr is None:
            raise OnChainMarketError(
                f"No Camelot quoter address for chain '{chain}'."
            )

        w3 = self._w3[chain]
        quoter = w3.eth.contract(
            address=Web3.to_checksum_address(quoter_addr),
            abi=QUICKSWAP_QUOTER_ABI,  # Same Algebra interface
        )
        amount_in = 10 ** WETH_DECIMALS

        result = quoter.functions.quoteExactInputSingle(
            Web3.to_checksum_address(weth),
            Web3.to_checksum_address(usdc),
            amount_in,
            0,
        ).call()

        amount_out = result[0]
        if amount_out == 0:
            raise OnChainMarketError(
                f"Camelot returned zero on {chain}."
            )
        return D(amount_out) / D(10 ** USDC_DECIMALS)

    def _quote_velodrome(
        self, chain: str, weth: str, usdc: str, dex_type: str
    ) -> Decimal:
        """Get mid-price from Velodrome V2 (Optimism) or Aerodrome (Base).

        Uses getAmountsOut with a Route struct. Tries both volatile and stable
        pool types, returns the best quote.
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
        amount_in = 10 ** WETH_DECIMALS

        best_out = 0
        # Try both volatile (False) and stable (True) pool types.
        for stable in [False, True]:
            try:
                route = (
                    Web3.to_checksum_address(weth),
                    Web3.to_checksum_address(usdc),
                    stable,
                    Web3.to_checksum_address(factory),
                )
                amounts = router.functions.getAmountsOut(amount_in, [route]).call()
                if len(amounts) >= 2 and amounts[-1] > best_out:
                    best_out = amounts[-1]
            except Exception:
                continue

        if best_out == 0:
            raise OnChainMarketError(
                f"{'Aerodrome' if dex_type == 'aerodrome' else 'Velodrome'} "
                f"returned zero on {chain}."
            )
        return D(best_out) / D(10 ** USDC_DECIMALS)
