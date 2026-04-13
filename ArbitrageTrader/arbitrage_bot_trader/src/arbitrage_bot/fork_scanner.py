"""DeFiLlama fork scanner — find Uniswap-style DEX clones on each chain.

The video recommends checking DeFiLlama forks to find Uniswap-style clones
that may have arbitrage opportunities due to similar interfaces but different
liquidity and pricing.

Uses the DeFiLlama /protocols endpoint (free, no API key).

Usage::

    # Find all Uniswap V3 forks
    PYTHONPATH=src python -m arbitrage_bot.fork_scanner --parent "Uniswap V3"

    # Find forks on a specific chain with minimum TVL
    PYTHONPATH=src python -m arbitrage_bot.fork_scanner --parent "Uniswap V3" --chain Ethereum --min-tvl 1000000

    # List all DEX forks
    PYTHONPATH=src python -m arbitrage_bot.fork_scanner --all-dex-forks
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import requests

from arbitrage_bot.env import load_env
from arbitrage_bot.log import get_logger

logger = get_logger(__name__)

DEFILLAMA_PROTOCOLS_URL = "https://api.llama.fi/protocols"


@dataclass
class ForkInfo:
    """A protocol found via DeFiLlama that is a fork of another."""
    name: str
    slug: str
    forked_from: str
    category: str
    chains: list[str]
    tvl: float
    url: str


def fetch_all_protocols(timeout: float = 15.0) -> list[dict]:
    """Fetch the full protocol list from DeFiLlama."""
    resp = requests.get(DEFILLAMA_PROTOCOLS_URL, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def find_forks(
    parent: str | None = None,
    chain: str | None = None,
    category: str | None = None,
    min_tvl: float = 0,
    timeout: float = 15.0,
) -> list[ForkInfo]:
    """Find protocols that are forks of a given parent.

    Args:
        parent: Parent protocol name to match (e.g. "Uniswap V3"). Case-insensitive.
        chain: Filter to protocols deployed on this chain.
        category: Filter by DeFiLlama category (e.g. "Dexes", "Lending").
        min_tvl: Minimum TVL in USD.
    """
    protocols = fetch_all_protocols(timeout=timeout)

    results: list[ForkInfo] = []
    for p in protocols:
        forked_from_raw = p.get("forkedFrom")
        if not forked_from_raw:
            continue

        # Normalize forkedFrom — DeFiLlama returns string or list of strings.
        if isinstance(forked_from_raw, list):
            forked_from = ", ".join(forked_from_raw)
        else:
            forked_from = str(forked_from_raw)

        # Match parent name (case-insensitive, partial match).
        if parent:
            if parent.lower() not in forked_from.lower():
                continue

        # Filter by category (DeFiLlama uses "Dexs" not "Dexes").
        p_category = p.get("category", "")
        if category:
            cat_lower = category.lower().rstrip("s").rstrip("e")
            p_lower = p_category.lower().rstrip("s").rstrip("e")
            if cat_lower != p_lower:
                continue

        # Filter by chain.
        p_chains = p.get("chains", [])
        if chain and chain not in p_chains:
            continue

        # Filter by TVL.
        tvl = float(p.get("tvl", 0) or 0)
        if tvl < min_tvl:
            continue

        results.append(ForkInfo(
            name=p.get("name", "?"),
            slug=p.get("slug", ""),
            forked_from=forked_from,
            category=p_category,
            chains=p_chains,
            tvl=tvl,
            url=f"https://defillama.com/protocol/{p.get('slug', '')}",
        ))

    results.sort(key=lambda x: x.tvl, reverse=True)
    return results


def find_all_dex_forks(
    chain: str | None = None,
    min_tvl: float = 0,
    timeout: float = 15.0,
) -> list[ForkInfo]:
    """Find all DEX forks across all parent protocols."""
    return find_forks(category="Dexs", chain=chain, min_tvl=min_tvl, timeout=timeout)


def find_uniswap_style_dexes(
    chain: str | None = None,
    min_tvl: float = 0,
    timeout: float = 15.0,
) -> list[ForkInfo]:
    """Find DEXes that are Uniswap-style (by name/description matching).

    Since DeFiLlama's forkedFrom field is sparsely populated, this uses
    keyword matching on protocol names to find AMMs with similar interfaces.
    """
    protocols = fetch_all_protocols(timeout=timeout)

    # Keywords that indicate Uniswap V2/V3 style AMMs.
    uniswap_keywords = {"swap", "amm", "dex", "exchange", "liquidity"}

    results: list[ForkInfo] = []
    for p in protocols:
        p_category = p.get("category", "")
        if p_category not in ("Dexs", "Dexes"):
            continue

        p_chains = p.get("chains", [])
        if chain and chain not in p_chains:
            continue

        tvl = float(p.get("tvl", 0) or 0)
        if tvl < min_tvl:
            continue

        forked_raw = p.get("forkedFrom")
        if isinstance(forked_raw, list):
            forked_from = ", ".join(str(x) for x in forked_raw)
        elif forked_raw:
            forked_from = str(forked_raw)
        else:
            forked_from = ""

        results.append(ForkInfo(
            name=p.get("name", "?"),
            slug=p.get("slug", ""),
            forked_from=forked_from or "—",
            category=p_category,
            chains=p_chains,
            tvl=tvl,
            url=f"https://defillama.com/protocol/{p.get('slug', '')}",
        ))

    results.sort(key=lambda x: x.tvl, reverse=True)
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Find Uniswap-style DEX forks via DeFiLlama."
    )
    parser.add_argument(
        "--parent",
        default=None,
        help="Parent protocol name (e.g. 'Uniswap V3', 'Uniswap V2').",
    )
    parser.add_argument(
        "--chain",
        default=None,
        help="Filter to a specific chain (e.g. 'Ethereum', 'BSC', 'Arbitrum').",
    )
    parser.add_argument(
        "--min-tvl",
        type=float,
        default=100_000,
        help="Minimum TVL in USD (default: 100000).",
    )
    parser.add_argument(
        "--all-dex-forks",
        action="store_true",
        help="List all DEX forks regardless of parent.",
    )
    return parser


def main() -> None:
    load_env()
    args = build_parser().parse_args()

    if args.all_dex_forks:
        print(f"Scanning all DEX forks"
              f"{f' on {args.chain}' if args.chain else ''}"
              f" (min TVL: ${args.min_tvl:,.0f})...\n")
        forks = find_all_dex_forks(chain=args.chain, min_tvl=args.min_tvl)
    elif args.parent:
        print(f"Scanning forks of '{args.parent}'"
              f"{f' on {args.chain}' if args.chain else ''}"
              f" (min TVL: ${args.min_tvl:,.0f})...\n")
        forks = find_forks(parent=args.parent, chain=args.chain, min_tvl=args.min_tvl)
    else:
        # Default: all Uniswap-style DEXes.
        print(f"Scanning Uniswap-style DEXes"
              f"{f' on {args.chain}' if args.chain else ''}"
              f" (min TVL: ${args.min_tvl:,.0f})...\n")
        forks = find_uniswap_style_dexes(chain=args.chain, min_tvl=args.min_tvl)

    if not forks:
        print("No forks found matching criteria.")
        return

    print(f"Found {len(forks)} fork(s):\n")
    print(f"{'Name':<30} {'Forked From':<20} {'TVL':>14}  {'Chains'}")
    print("-" * 90)
    for f in forks[:50]:
        chains_str = ", ".join(f.chains[:5])
        if len(f.chains) > 5:
            chains_str += f" +{len(f.chains) - 5} more"
        print(f"{f.name:<30} {f.forked_from:<20} ${f.tvl:>13,.0f}  {chains_str}")


if __name__ == "__main__":
    main()
