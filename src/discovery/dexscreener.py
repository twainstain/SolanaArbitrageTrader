"""Solana pair discovery via DexScreener.

Ported from the EVM repo's `src/registry/discovery.py`, tightened for
Solana's single-chain reality (no cross-chain dimension), adapted blue-
chip list, and designed to emit output that drops directly into
`config/prod_scan.json`'s `extra_pairs`.

Scoring
-------

    score = total_24h_volume × dex_count × (2.0 if blue_chip else 1.0)

A pair with $1M daily volume on 3 DEXes scores 3× higher than the same
pair on 1 DEX. Blue-chip pairs (SOL/mSOL/jitoSOL/bSOL/USDC/USDT) get a
2× bonus because their pricing is deeper and the venues we already
support (Jupiter/Raydium/Orca) actually cover them.

Thresholds
----------

DexScreener's free API is lenient but rate-limited. We filter each
response with:

  - volume_24h   >= MIN_VOLUME_24H  ($100K by default — active market)
  - liquidity    >= MIN_LIQUIDITY  ($50K  by default — deep enough for 1 SOL)
  - dex_count    >= MIN_DEX_COUNT   (2    — arbitrage needs ≥ 2 DEXes)

Callers can override every threshold.

Not wired in
------------

This module is exported for the CLI script and future schedulers. It
does NOT currently get called from the running scanner loop — pair
additions go through `config/prod_scan.json` after operator review, on
purpose. If we eventually want hourly auto-expansion (like EVM's
`PairRefresher`), wrap `discover_solana_pairs` in a background thread
and persist into the `pairs` table.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEXSCREENER_API = "https://api.dexscreener.com"
SOLANA_CHAIN_ID = "solana"

# Minimum thresholds for a pair to qualify.
MIN_VOLUME_24H = 100_000
MIN_LIQUIDITY = 50_000
MIN_DEX_COUNT = 2

# Tokens considered "blue chip" on Solana — they all have deep pools,
# stable price sources, and are directly tradable through at least one
# of the venues we've already wired (Jupiter / Raydium / Orca).
BLUE_CHIP_TOKENS = {
    "SOL", "WSOL",                 # native
    "USDC", "USDT",                 # stables
    "MSOL", "JITOSOL", "BSOL",      # LSTs
    "JUP", "RAY", "ORCA",           # venue governance tokens (deep pools)
    "BONK", "WIF", "PYTH",          # well-known Solana-native assets
}

# Tokens DexScreener should search for. Seed with the universe we'd
# actually want to arb — the response list brings back pairs that
# INVOLVE these tokens, so we see e.g. SOL/USDC, SOL/USDT, SOL/mSOL all
# from one SOL search.
_DEFAULT_SEARCH_TOKENS: list[str] = [
    "SOL", "USDC", "USDT",
    "mSOL", "jitoSOL", "bSOL",
    "JUP", "PYTH", "BONK", "WIF",
]


@dataclass(frozen=True)
class DiscoveredPair:
    """One arbitrage-worthy pair surfaced by DexScreener."""
    pair_name: str
    base_symbol: str
    quote_symbol: str
    dex_count: int
    total_volume_24h: float
    total_liquidity: float
    dex_names: list[str]
    base_mint: str = ""
    quote_mint: str = ""
    is_blue_chip: bool = False
    score: float = 0.0


def score_pair(
    volume_24h: float, dex_count: int, is_blue_chip: bool
) -> float:
    """Combine volume, DEX coverage, and blue-chip status into one rank.

    Pure function so callers can sort on any slice of discovered pairs
    without re-calling the API.
    """
    bonus = 2.0 if is_blue_chip else 1.0
    return float(volume_24h) * int(dex_count) * bonus


def discover_solana_pairs(
    search_tokens: list[str] | None = None,
    min_volume: float = MIN_VOLUME_24H,
    min_liquidity: float = MIN_LIQUIDITY,
    min_dex_count: int = MIN_DEX_COUNT,
    max_results: int = 25,
    timeout: float = 10.0,
    session: requests.Session | None = None,
) -> list[DiscoveredPair]:
    """Ranked list of Solana DEX pairs worth adding to `extra_pairs`.

    Iterates `search_tokens` against DexScreener's `/latest/dex/search`,
    groups responses by `(base_symbol, quote_symbol)`, filters on the
    configured thresholds, scores, and sorts descending. Results are
    deduplicated: the same pair is only returned once even if multiple
    search tokens surfaced it.
    """
    sess = session or requests.Session()
    tokens = search_tokens or _DEFAULT_SEARCH_TOKENS

    # (base_sym, quote_sym) → {dex_id: [{volume, liquidity, base_mint, quote_mint}, ...]}
    all_pairs: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for token in tokens:
        try:
            pairs = _search_dexscreener(sess, token, timeout)
        except Exception as exc:
            logger.debug("Search for '%s' failed: %s", token, exc)
            continue
        for p in pairs:
            if (p.get("chainId") or "").lower() != SOLANA_CHAIN_ID:
                continue
            volume = _nested_float(p, "volume", "h24")
            liquidity = _nested_float(p, "liquidity", "usd")
            if volume < min_volume or liquidity < min_liquidity:
                continue
            base = p.get("baseToken") or {}
            quote = p.get("quoteToken") or {}
            base_sym = _normalize(base.get("symbol", ""))
            quote_sym = _normalize(quote.get("symbol", ""))
            if not base_sym or not quote_sym:
                continue
            dex = p.get("dexId") or "unknown"
            all_pairs[(base_sym, quote_sym)][dex].append({
                "volume": volume,
                "liquidity": liquidity,
                "base_mint": base.get("address", ""),
                "quote_mint": quote.get("address", ""),
            })

    # Score + rank
    ranked: list[DiscoveredPair] = []
    for (base_sym, quote_sym), dex_data in all_pairs.items():
        if len(dex_data) < min_dex_count:
            continue
        # Pick MAX per DEX to avoid double-counting when the same DEX
        # reports multiple fee-tier pools for a pair.
        total_volume = sum(
            max((e["volume"] for e in entries), default=0.0)
            for entries in dex_data.values()
        )
        total_liquidity = sum(
            max((e["liquidity"] for e in entries), default=0.0)
            for entries in dex_data.values()
        )
        # Use the highest-volume entry as the source of mints.
        best_entry = None
        best_vol = -1.0
        for entries in dex_data.values():
            for e in entries:
                if e["volume"] > best_vol:
                    best_vol = e["volume"]
                    best_entry = e
        is_blue = _is_blue_chip(base_sym) and _is_blue_chip(quote_sym)
        ranked.append(DiscoveredPair(
            pair_name=f"{base_sym}/{quote_sym}",
            base_symbol=base_sym,
            quote_symbol=quote_sym,
            dex_count=len(dex_data),
            total_volume_24h=total_volume,
            total_liquidity=total_liquidity,
            dex_names=sorted(dex_data.keys()),
            base_mint=(best_entry or {}).get("base_mint", ""),
            quote_mint=(best_entry or {}).get("quote_mint", ""),
            is_blue_chip=is_blue,
            score=score_pair(total_volume, len(dex_data), is_blue),
        ))

    ranked.sort(key=lambda p: p.score, reverse=True)
    return ranked[:max_results]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _search_dexscreener(
    session: requests.Session, query: str, timeout: float,
) -> list[dict[str, Any]]:
    url = f"{DEXSCREENER_API}/latest/dex/search"
    resp = session.get(url, params={"q": query}, timeout=timeout)
    resp.raise_for_status()
    body = resp.json()
    return body.get("pairs") or []


def _is_blue_chip(symbol: str) -> bool:
    return symbol.upper() in BLUE_CHIP_TOKENS


def _normalize(symbol: str) -> str:
    """DexScreener sometimes returns symbols like 'WSOL' or 'wSOL'.
    Normalize to upper, strip whitespace. Empty → empty (skipped upstream)."""
    return (symbol or "").strip().upper()


def _nested_float(d: dict, *keys: str) -> float:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return 0.0
        cur = cur.get(k)
        if cur is None:
            return 0.0
    try:
        return float(cur)
    except (TypeError, ValueError):
        return 0.0
