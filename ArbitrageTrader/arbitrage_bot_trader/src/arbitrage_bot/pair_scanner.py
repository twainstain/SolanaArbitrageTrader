"""Pair discovery tool — find high-volume ERC-20 pairs across DEXs.

The video recommends using DexScreener, BirdEye, GMGN, and Etherscan
to research token pairs.  This tool queries the DexScreener API to find
pairs that are:
  - traded on multiple DEXs (arbitrage potential)
  - high volume (liquid enough for flash-loan arb)
  - on the target chain

DexScreener API: https://docs.dexscreener.com/api/reference
  - No API key required
  - /search?q=QUERY  — search by symbol (up to ~30 results)
  - /tokens/{addresses} — search by contract address (more precise)

Usage::

    # Search by symbol
    PYTHONPATH=src python -m arbitrage_bot.pair_scanner --token WETH --chain ethereum

    # Search by contract address (more precise)
    PYTHONPATH=src python -m arbitrage_bot.pair_scanner \\
        --address 0xC02aaA39b223FE8D0A0e5c4F27eAD9083C756Cc2 --chain ethereum

    # Scan all 3 recommended pairs
    PYTHONPATH=src python -m arbitrage_bot.pair_scanner --recommended --chain ethereum
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass

import requests

from arbitrage_bot.env import load_env
from arbitrage_bot.tokens import CHAIN_TOKENS

DEXSCREENER_BASE_URL = "https://api.dexscreener.com"

# Map our chain names to DexScreener chain IDs.
CHAIN_MAP = {
    "ethereum": "ethereum",
    "bsc": "bsc",
    "arbitrum": "arbitrum",
    "base": "base",
}

# Well-known pool addresses for the 3 recommended pairs across DEXs.
# The /pairs/{chain}/{addresses} endpoint gives the most reliable results.
RECOMMENDED_POOL_ADDRESSES: dict[str, list[str]] = {
    "ethereum": [
        # WETH/USDC pools
        "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640",  # Uniswap V3 0.05%
        "0x8ad599c3A0ff1De082011EFDDc58f1908eb6e6D8",  # Uniswap V3 0.30%
        "0x35644fb61afbc458bf92b15add6abc1996be5014",  # Sushi V3
        # WETH/USDT pools
        "0x11b815efB8f581194ae5486326A680323dB6E0aA",  # Uniswap V3 0.05%
        "0x4e68Ccd3E89f51C3074ca5072bbAC773960dFa36",  # Uniswap V3 0.30%
        # WBTC/USDC pools
        "0x99ac8cA7087fA4A2A1FB6357269965A2014ABc35",  # Uniswap V3 0.30%
    ],
    "arbitrum": [
        "0xC6962004f452bE9203591991D15f6b388e09E8D0",  # Uniswap V3 WETH/USDC
        "0xC31E54c7a869B9FcBEcc14363CF510d1c41fa443",  # Uniswap V3 WETH/USDT
    ],
    "base": [
        "0xd0b53D9277642d899DF5C87A3966A349A798F224",  # Uniswap V3 WETH/USDC
    ],
}


@dataclass
class PairInfo:
    """A trading pair found on DexScreener."""
    chain: str
    dex: str
    pair_address: str
    base_token: str
    base_address: str
    quote_token: str
    quote_address: str
    price_usd: float
    volume_24h: float
    liquidity_usd: float
    url: str


class PairScannerError(Exception):
    pass


def search_pairs_by_symbol(
    query: str,
    chain: str | None = None,
    min_volume: float = 0,
    min_liquidity: float = 0,
    timeout: float = 10.0,
) -> list[PairInfo]:
    """Search DexScreener by token symbol (e.g. "WETH")."""
    url = f"{DEXSCREENER_BASE_URL}/latest/dex/search?q={query}"
    return _fetch_and_filter(url, chain, min_volume, min_liquidity, timeout)


def search_pairs_by_pool_addresses(
    pool_addresses: list[str],
    chain: str,
    min_volume: float = 0,
    timeout: float = 10.0,
) -> list[PairInfo]:
    """Query DexScreener for specific pool addresses on a chain.

    This is the most reliable search method — returns exact pool data.
    DexScreener: GET /latest/dex/pairs/{chain}/{addr1,addr2,...}
    """
    if not pool_addresses:
        return []
    ds_chain = CHAIN_MAP.get(chain, chain)
    addr_str = ",".join(pool_addresses)
    url = f"{DEXSCREENER_BASE_URL}/latest/dex/pairs/{ds_chain}/{addr_str}"
    return _fetch_and_filter(url, chain, min_volume, 0, timeout)


def search_pairs_by_address(
    address: str,
    chain: str | None = None,
    min_volume: float = 0,
    min_liquidity: float = 0,
    timeout: float = 10.0,
) -> list[PairInfo]:
    """Search DexScreener by token contract address (more precise than symbol)."""
    # DexScreener token endpoint requires chain prefix: /tokens/v1/{chain}/{address}
    # If no chain specified, use the search endpoint with the address as query.
    if chain:
        ds_chain = CHAIN_MAP.get(chain, chain)
        url = f"{DEXSCREENER_BASE_URL}/tokens/v1/{ds_chain}/{address}"
    else:
        url = f"{DEXSCREENER_BASE_URL}/latest/dex/search?q={address}"
    return _fetch_and_filter(url, chain, min_volume, min_liquidity, timeout)


def _fetch_and_filter(
    url: str,
    chain: str | None,
    min_volume: float,
    min_liquidity: float,
    timeout: float,
) -> list[PairInfo]:
    """Fetch pairs from a DexScreener URL and apply filters."""
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    # DexScreener returns "pairs" at top level or nested.
    pairs_raw = data if isinstance(data, list) else data.get("pairs", [])
    if not pairs_raw:
        return []

    results: list[PairInfo] = []
    for p in pairs_raw:
        p_chain = p.get("chainId", "")
        if chain and p_chain != CHAIN_MAP.get(chain, chain):
            continue

        vol = float(p.get("volume", {}).get("h24", 0) or 0)
        liq = float(p.get("liquidity", {}).get("usd", 0) or 0)
        if vol < min_volume or liq < min_liquidity:
            continue

        results.append(PairInfo(
            chain=p_chain,
            dex=p.get("dexId", "unknown"),
            pair_address=p.get("pairAddress", ""),
            base_token=p.get("baseToken", {}).get("symbol", "?"),
            base_address=p.get("baseToken", {}).get("address", ""),
            quote_token=p.get("quoteToken", {}).get("symbol", "?"),
            quote_address=p.get("quoteToken", {}).get("address", ""),
            price_usd=float(p.get("priceUsd", 0) or 0),
            volume_24h=vol,
            liquidity_usd=liq,
            url=p.get("url", ""),
        ))

    results.sort(key=lambda x: x.volume_24h, reverse=True)
    return results


def find_cross_dex_pairs(
    query: str | None = None,
    address: str | None = None,
    chain: str | None = None,
    min_volume: float = 100_000,
    timeout: float = 10.0,
) -> dict[str, list[PairInfo]]:
    """Find tokens traded on multiple DEXs — arbitrage candidates.

    Returns a dict keyed by "BASE/QUOTE" with list of PairInfo per DEX.
    Only includes pairs present on 2+ DEXs.
    """
    if address:
        all_pairs = search_pairs_by_address(address, chain=chain, min_volume=min_volume, timeout=timeout)
    elif query:
        all_pairs = search_pairs_by_symbol(query, chain=chain, min_volume=min_volume, timeout=timeout)
    else:
        return {}

    # Group by normalized base/quote symbol pair.
    # Normalize: uppercase, handle common aliases.
    grouped: dict[str, list[PairInfo]] = {}
    for p in all_pairs:
        base = _normalize_symbol(p.base_token)
        quote = _normalize_symbol(p.quote_token)
        key = f"{base}/{quote}"
        grouped.setdefault(key, []).append(p)

    # Keep only pairs on 2+ DEXs (arbitrage requires at least two venues).
    multi_dex = {}
    for key, pairs in grouped.items():
        dex_names = {p.dex for p in pairs}
        if len(dex_names) >= 2:
            multi_dex[key] = pairs

    return multi_dex


def scan_recommended_pairs(
    chain: str = "ethereum",
    min_volume: float = 50_000,
    timeout: float = 10.0,
) -> dict[str, list[PairInfo]]:
    """Scan the 3 recommended pairs (WETH/USDC, WETH/USDT, WBTC/USDC) across DEXs.

    Uses known pool addresses for precise, reliable results from DexScreener.
    Falls back to symbol search if no pool addresses are configured for the chain.
    """
    pool_addrs = RECOMMENDED_POOL_ADDRESSES.get(chain, [])

    if pool_addrs:
        # Primary: query by known pool addresses (most reliable).
        all_pairs = search_pairs_by_pool_addresses(
            pool_addrs, chain=chain, min_volume=min_volume, timeout=timeout
        )
    else:
        # Fallback: symbol search.
        all_pairs = search_pairs_by_symbol(
            "WETH", chain=chain, min_volume=min_volume, timeout=timeout
        )

    # Group by normalized pair name.
    grouped: dict[str, list[PairInfo]] = {}
    for p in all_pairs:
        base = _normalize_symbol(p.base_token)
        quote = _normalize_symbol(p.quote_token)
        key = f"{base}/{quote}"
        grouped.setdefault(key, []).append(p)

    # Filter to our 3 recommended pairs.
    recommended = {"WETH/USDC", "WETH/USDT", "WBTC/USDC"}
    return {k: v for k, v in grouped.items() if k in recommended}


def _normalize_symbol(sym: str) -> str:
    """Normalize token symbols for grouping."""
    s = sym.upper().strip()
    # Common aliases.
    if s in ("WETH", "ETH"):
        return "WETH"
    if s in ("WBTC", "BTC"):
        return "WBTC"
    return s


def discover_pairs_for_bot(
    chain: str = "ethereum",
    min_volume: float = 50_000,
    search_tokens: list[str] | None = None,
    trade_size_map: dict[str, float] | None = None,
    max_pairs: int = 10,
    timeout: float = 10.0,
) -> list["PairConfig"]:
    """Discover live cross-DEX pairs and return them as PairConfig objects.

    This does TRUE open-ended discovery — searches DexScreener for tokens
    traded on 2+ DEXs and returns whatever it finds (not a hardcoded list).

    Args:
        chain: Chain to scan.
        min_volume: Minimum 24h volume in USD.
        search_tokens: Token symbols to search. Default: broad list of popular tokens.
        trade_size_map: Override trade sizes per pair (e.g. {"WBTC/USDC": 0.05}).
        max_pairs: Maximum number of pairs to return (sorted by volume).

    Returns:
        List of PairConfig ready to pass to ArbitrageBot(pairs=...).
    """
    from arbitrage_bot.config import PairConfig
    from arbitrage_bot.log import get_logger

    _logger = get_logger(__name__)

    # Default: search a broad set of popular tokens — not just WETH/WBTC.
    tokens = search_tokens or [
        "ETH", "WETH", "WBTC", "BTC",
        "USDC", "USDT", "DAI",
        "LINK", "UNI", "AAVE", "ARB", "OP",
        "PEPE", "SHIB", "DOGE",
    ]

    default_sizes: dict[str, float] = {
        "WBTC/USDC": 0.05, "WBTC/USDT": 0.05,
    }
    sizes = trade_size_map or default_sizes

    chain_label = chain or "all chains"
    _logger.info("Discovering live cross-DEX pairs on %s (searching %d tokens, min vol $%.0f)...",
                 chain_label, len(tokens), min_volume)

    # Search DexScreener for each token and collect all cross-DEX pairs.
    all_cross_dex: dict[str, list[PairInfo]] = {}

    for token in tokens:
        try:
            pairs = find_cross_dex_pairs(
                query=token, chain=chain, min_volume=min_volume, timeout=timeout
            )
            for key, pair_list in pairs.items():
                if key not in all_cross_dex:
                    all_cross_dex[key] = pair_list
                else:
                    # Merge: add pairs from new DEXs.
                    existing_addrs = {p.pair_address for p in all_cross_dex[key]}
                    for p in pair_list:
                        if p.pair_address not in existing_addrs:
                            all_cross_dex[key].append(p)
        except Exception as exc:
            _logger.debug("Search for '%s' failed: %s", token, exc)

    # Also search by known pool addresses for the recommended pairs.
    pool_addrs = RECOMMENDED_POOL_ADDRESSES.get(chain, [])
    if pool_addrs:
        try:
            pool_pairs = search_pairs_by_pool_addresses(
                pool_addrs, chain=chain, min_volume=min_volume, timeout=timeout
            )
            grouped: dict[str, list[PairInfo]] = defaultdict(list)
            for p in pool_pairs:
                base = _normalize_symbol(p.base_token)
                quote = _normalize_symbol(p.quote_token)
                grouped[f"{base}/{quote}"].append(p)
            for key, pair_list in grouped.items():
                if key not in all_cross_dex:
                    all_cross_dex[key] = pair_list
                else:
                    existing_addrs = {p.pair_address for p in all_cross_dex[key]}
                    for p in pair_list:
                        if p.pair_address not in existing_addrs:
                            all_cross_dex[key].append(p)
        except Exception as exc:
            _logger.debug("Pool address search failed: %s", exc)

    if not all_cross_dex:
        _logger.info("No cross-DEX pairs discovered.")
        return []

    # Sort by total volume and take top N.
    sorted_pairs = sorted(
        all_cross_dex.items(),
        key=lambda x: sum(p.volume_24h for p in x[1]),
        reverse=True,
    )[:max_pairs]

    from arbitrage_bot.log import log_discovery, log_discovery_detail

    result: list[PairConfig] = []
    for pair_name, pairs in sorted_pairs:
        parts = pair_name.split("/")
        if len(parts) != 2:
            continue
        base_asset, quote_asset = parts
        total_vol = sum(p.volume_24h for p in pairs)
        dex_names = sorted({p.dex for p in pairs})
        dex_count = len(dex_names)
        pair_chains = sorted({p.chain for p in pairs})
        trade_size = sizes.get(pair_name, 1.0)

        # Carry token addresses from DexScreener so the market source can
        # price ANY token — not just the hardcoded ones in our registry.
        # Use the highest-volume pair's addresses as the reference.
        best = max(pairs, key=lambda p: p.volume_24h)

        result.append(PairConfig(
            pair=pair_name,
            base_asset=base_asset,
            quote_asset=quote_asset,
            trade_size=trade_size,
            base_address=best.base_address,
            quote_address=best.quote_address,
            chain=best.chain,
        ))
        _logger.info(
            "  [LIVE] %s — %d DEXs (%s), $%.0f 24h vol, chains=%s",
            pair_name, dex_count, ", ".join(dex_names), total_vol, pair_chains,
        )
        log_discovery_detail(
            _logger, pair_name, dex_count, total_vol, dex_names, pair_chains,
        )

    _logger.info("Discovered %d live cross-DEX pair(s).", len(result))
    log_discovery(_logger, result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan DexScreener for high-volume pairs traded on multiple DEXs."
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Token symbol to search (e.g. WETH, PEPE).",
    )
    parser.add_argument(
        "--address",
        default=None,
        help="Token contract address (more precise than --token).",
    )
    parser.add_argument(
        "--recommended",
        action="store_true",
        help="Scan all 3 recommended pairs (WETH/USDC, WETH/USDT, WBTC/USDC).",
    )
    parser.add_argument(
        "--chain",
        default=None,
        choices=["ethereum", "bsc", "arbitrum", "base"],
        help="Filter to a specific chain.",
    )
    parser.add_argument(
        "--min-volume",
        type=float,
        default=50_000,
        help="Minimum 24h volume in USD (default: 50000).",
    )
    return parser


def main() -> None:
    load_env()
    args = build_parser().parse_args()

    if args.recommended:
        chain = args.chain or "ethereum"
        print(f"Scanning recommended pairs on {chain} (min volume: ${args.min_volume:,.0f})...\n")
        cross_dex = scan_recommended_pairs(chain=chain, min_volume=args.min_volume)
    elif args.address:
        print(f"Scanning address {args.address}"
              f"{f' on {args.chain}' if args.chain else ''}"
              f" (min volume: ${args.min_volume:,.0f})...\n")
        cross_dex = find_cross_dex_pairs(address=args.address, chain=args.chain, min_volume=args.min_volume)
    elif args.token:
        print(f"Scanning '{args.token}'"
              f"{f' on {args.chain}' if args.chain else ''}"
              f" (min volume: ${args.min_volume:,.0f})...\n")
        cross_dex = find_cross_dex_pairs(query=args.token, chain=args.chain, min_volume=args.min_volume)
    else:
        print("Specify --token, --address, or --recommended. Use --help for details.")
        return

    if not cross_dex:
        print("No cross-DEX pairs found matching criteria.")
        return

    for pair_name, pairs in sorted(cross_dex.items(), key=lambda x: -sum(p.volume_24h for p in x[1])):
        total_vol = sum(p.volume_24h for p in pairs)
        dex_count = len({p.dex for p in pairs})
        print(f"--- {pair_name} ({dex_count} DEXs, total 24h vol: ${total_vol:,.0f}) ---")
        for p in pairs[:10]:
            print(
                f"  {p.dex:<20} "
                f"${p.price_usd:>12,.4f}  "
                f"vol=${p.volume_24h:>14,.0f}  "
                f"liq=${p.liquidity_usd:>14,.0f}  "
                f"{p.chain}"
            )
        print()


if __name__ == "__main__":
    main()
