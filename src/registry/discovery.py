"""Smart pair discovery — find the best ERC-20 pairs for arbitrage.

WHY THIS EXISTS:
  Instead of hardcoding which pairs to scan, we dynamically discover the
  best candidates by querying DexScreener's public API.  A good arb pair
  needs: (1) high volume (active market), (2) presence on 2+ DEXes on the
  same chain (price discrepancies possible), and (3) deep liquidity
  (trades won't move the price against us).

HOW IT WORKS:
  1. Search DexScreener for well-known tokens (WETH, WBTC, USDC, etc.)
  2. Group results by (pair_name, chain) — e.g., ("WETH/USDC", "arbitrum")
  3. Filter: must be on 2+ DEXes, meet volume/liquidity minimums
  4. Score: volume × dex_count × blue_chip_bonus (2x for WETH/USDC vs. unknown pairs)
  5. Return top N pairs sorted by score

SCORING LOGIC:
  score = total_24h_volume × number_of_dexes × (2.0 if blue_chip else 1.0)

  A pair with $1M volume on 3 DEXes scores 3x higher than the same pair
  on 1 DEX.  Blue chip pairs (WETH, WBTC, USDC, USDT) get a 2x bonus
  because they have more reliable pricing and deeper liquidity.

CALLED BY:
  PairRefresher._refresh() — runs every hour in a background thread.
  Results are cached in DB and used to expand the bot's scan list.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

import requests

logger = logging.getLogger(__name__)

DEXSCREENER_API = "https://api.dexscreener.com"

# Import from single source of truth.
from core.models import SUPPORTED_CHAINS

# Minimum thresholds for a pair to be interesting.
MIN_VOLUME_24H = 100_000     # $100K daily volume
MIN_LIQUIDITY = 50_000       # $50K TVL
MIN_DEX_COUNT = 2            # Must be on 2+ DEXs for arbitrage

# Token symbols we consider ERC-20 blue chips.
BLUE_CHIP_TOKENS = {
    "WETH", "ETH", "WBTC", "BTC",
    "USDC", "USDT", "DAI", "USDC.e",
    "LINK", "UNI", "AAVE", "ARB", "OP",
}

D = Decimal


@dataclass
class DiscoveredPair:
    """A pair found on multiple DEXs with arbitrage potential."""
    pair_name: str
    base_symbol: str
    quote_symbol: str
    chain: str
    dex_count: int
    total_volume_24h: float
    total_liquidity: float
    dex_names: list[str]
    base_address: str = ""
    quote_address: str = ""
    is_blue_chip: bool = False
    arbitrage_score: float = 0.0


def discover_best_pairs(
    chains: list[str] | None = None,
    search_tokens: list[str] | None = None,
    min_volume: float = MIN_VOLUME_24H,
    min_liquidity: float = MIN_LIQUIDITY,
    min_dex_count: int = MIN_DEX_COUNT,
    max_results: int = 20,
    timeout: float = 10.0,
) -> list[DiscoveredPair]:
    """Discover the best ERC-20 pairs for arbitrage across chains.

    Returns pairs sorted by arbitrage score (volume * dex_count * blue_chip_bonus).
    """
    target_chains = set(chains) if chains else SUPPORTED_CHAINS
    tokens = search_tokens or [
        "WETH", "ETH", "WBTC", "USDC", "USDT",
        "LINK", "UNI", "AAVE", "ARB",
    ]

    # Collect all pairs across tokens and chains.
    all_pairs: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    # Key: (pair_name, chain) → list of (dex, volume, liquidity, base_addr, quote_addr)

    for token in tokens:
        try:
            results = _search_dexscreener(token, timeout)
            for pair in results:
                chain = pair.get("chainId", "").lower()
                if chain not in target_chains:
                    continue

                volume = float(pair.get("volume", {}).get("h24", 0) or 0)
                liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)

                if volume < min_volume or liquidity < min_liquidity:
                    continue

                base = pair.get("baseToken", {})
                quote = pair.get("quoteToken", {})
                base_sym = _normalize(base.get("symbol", ""))
                quote_sym = _normalize(quote.get("symbol", ""))

                if not base_sym or not quote_sym:
                    continue

                pair_name = f"{base_sym}/{quote_sym}"
                dex = pair.get("dexId", "unknown")

                all_pairs[(pair_name, chain)][dex].append({
                    "volume": volume,
                    "liquidity": liquidity,
                    "base_address": base.get("address", ""),
                    "quote_address": quote.get("address", ""),
                })
        except Exception as exc:
            logger.debug("Search for '%s' failed: %s", token, exc)

    # Build scored results.
    results: list[DiscoveredPair] = []
    for (pair_name, chain), dex_data in all_pairs.items():
        if len(dex_data) < min_dex_count:
            continue

        parts = pair_name.split("/")
        if len(parts) != 2:
            continue
        base_sym, quote_sym = parts

        total_vol = sum(
            max((e["volume"] for e in entries), default=0)
            for entries in dex_data.values()
        )
        total_liq = sum(
            max((e["liquidity"] for e in entries), default=0)
            for entries in dex_data.values()
        )
        dex_names = sorted(dex_data.keys())

        # Get best addresses.
        best_entry = None
        best_vol = 0
        for entries in dex_data.values():
            for e in entries:
                if e["volume"] > best_vol:
                    best_vol = e["volume"]
                    best_entry = e

        is_blue = base_sym in BLUE_CHIP_TOKENS and quote_sym in BLUE_CHIP_TOKENS

        # Arbitrage score: volume * dex_count * blue_chip_bonus.
        score = total_vol * len(dex_data) * (2.0 if is_blue else 1.0)

        results.append(DiscoveredPair(
            pair_name=pair_name,
            base_symbol=base_sym,
            quote_symbol=quote_sym,
            chain=chain,
            dex_count=len(dex_data),
            total_volume_24h=total_vol,
            total_liquidity=total_liq,
            dex_names=dex_names,
            base_address=best_entry["base_address"] if best_entry else "",
            quote_address=best_entry["quote_address"] if best_entry else "",
            is_blue_chip=is_blue,
            arbitrage_score=score,
        ))

    # Sort by score (volume * dex_count * blue_chip), descending.
    results.sort(key=lambda p: p.arbitrage_score, reverse=True)
    return results[:max_results]


def print_discovery_report(pairs: list[DiscoveredPair]) -> None:
    """Print a human-readable discovery report."""
    print(f"\n{'='*80}")
    print(f"  PAIR DISCOVERY — {len(pairs)} pairs found")
    print(f"{'='*80}\n")
    for i, p in enumerate(pairs, 1):
        bc = " [BLUE CHIP]" if p.is_blue_chip else ""
        print(f"  {i:2d}. {p.pair_name:<16s} chain={p.chain:<10s} "
              f"DEXs={p.dex_count} vol=${p.total_volume_24h:>12,.0f} "
              f"liq=${p.total_liquidity:>12,.0f} "
              f"score={p.arbitrage_score:>14,.0f}{bc}")
        print(f"      DEXs: {', '.join(p.dex_names)}")
    print()


def _search_dexscreener(query: str, timeout: float = 10.0) -> list[dict]:
    """Search DexScreener for pairs matching a token symbol."""
    resp = requests.get(
        f"{DEXSCREENER_API}/latest/dex/search?q={query}",
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("pairs", [])


def _normalize(symbol: str) -> str:
    """Normalize a token symbol."""
    s = symbol.upper().strip()
    if s == "WETH" or s == "ETH":
        return "WETH"
    return s
