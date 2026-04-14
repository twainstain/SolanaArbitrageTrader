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

from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal

from web3 import Web3

from config import BotConfig
import logging

from contracts import (
    BALANCER_POOL_IDS,
    BALANCER_VAULT,
    BALANCER_VAULT_ABI,
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

SUPPORTED_DEX_TYPES = ("uniswap_v3", "sushi_v3", "pancakeswap_v3", "balancer_v2", "quickswap_v3")

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
                self._w3[chain] = Web3(Web3.HTTPProvider(urls[0]))

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
        self._w3[chain] = Web3(Web3.HTTPProvider(new_url))
        _logger.info("RPC failover for %s → %s", chain, new_url[:50])

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

            # --- Thin pool detection: quote 10 WETH and compare per-unit price ---
            # If quoting 10x the amount gives a significantly worse per-unit price,
            # the pool has thin liquidity and the 1 WETH quote is misleading.
            # This catches SushiSwap Optimism returning $2152 when the real market
            # is $2364 — the pool has so little liquidity that even 1 WETH moves it.
            estimated_liquidity = D("0")
            try:
                large_mid = self._quote_large_amount(chain, base_addr, quote_addr, dex_type)
                if large_mid > D("0") and mid > D("0"):
                    # Per-unit price impact: how much worse is the 10x quote?
                    impact_pct = abs(mid - large_mid) / mid * D("100")
                    if impact_pct > D("5"):
                        # >5% price impact at 10 WETH = very thin pool. Reject.
                        msg = (
                            f"{dex.name} on {chain}: thin pool — "  # type: ignore[union-attr]
                            f"1 WETH=${float(mid):.0f}, 10 WETH=${float(large_mid):.0f} "
                            f"({float(impact_pct):.1f}% impact)"
                        )
                        _logger.warning("Thin pool: %s", msg)
                        raise OnChainMarketError(msg)
                    elif impact_pct > D("2"):
                        # 2-5% impact = moderate liquidity
                        estimated_liquidity = mid * D("500") / max(impact_pct, D("1"))
                    else:
                        # <2% impact = deep pool
                        estimated_liquidity = D("10000000")  # $10M+
            except OnChainMarketError as thin_err:
                # Thin pool → reject this DEX, cache for 3h.
                # Cache it via the normal error path in get_quotes().
                raise
            except Exception:
                pass  # RPC error on large quote — skip check, don't block

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

        with ThreadPoolExecutor(max_workers=len(active_dexes)) as pool:
            futures = {pool.submit(_fetch_one, dex): dex for dex in active_dexes}
            for future in as_completed(futures):
                dex = futures[future]
                try:
                    quotes.append(future.result())
                except Exception as exc:
                    _logger.warning(
                        "Skipping %s on %s: %s",
                        dex.name, dex.chain, exc,  # type: ignore[union-attr]
                    )
                    # Cache zero-quote / error pairs so we don't retry every scan.
                    cache.mark_skip(
                        dex.name, dex.chain or "",  # type: ignore[union-attr]
                        str(exc),
                    )

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

        best_out = 0
        for fee in (100, 500, 3000, 10000):
            try:
                result = quoter.functions.quoteExactInputSingle(
                    (
                        Web3.to_checksum_address(weth),
                        Web3.to_checksum_address(usdc),
                        amount_in,
                        fee,
                        0,
                    )
                ).call()
                if result[0] > best_out:
                    best_out = result[0]
            except Exception:
                continue

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

        best_out = 0
        for fee in (100, 500, 3000, 10000):
            try:
                result = quoter.functions.quoteExactInputSingle(
                    (
                        Web3.to_checksum_address(weth),
                        Web3.to_checksum_address(usdc),
                        amount_in,
                        fee,
                        0,
                    )
                ).call()
                if result[0] > best_out:
                    best_out = result[0]
            except Exception:
                continue

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

        # PancakeSwap V3 fee tiers — try all and take the best quote.
        # Liquidity varies by tier; 500 (0.05%) is often deepest for majors.
        fee_tiers = [100, 500, 2500, 10000]
        best_out = 0

        for fee in fee_tiers:
            try:
                result = quoter.functions.quoteExactInputSingle(
                    (
                        Web3.to_checksum_address(weth),
                        Web3.to_checksum_address(usdc),
                        amount_in,
                        fee,
                        0,
                    )
                ).call()
                amount_out = result[0]
                if amount_out > best_out:
                    best_out = amount_out
            except Exception:
                continue

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
