#!/usr/bin/env python3
"""Ad-hoc Solana pair discovery via DexScreener.

Prints a ranked table of pairs. Review the list and paste interesting
entries into `config/prod_scan.json` under `extra_pairs`. Does not write
to the DB or touch the running scanner.

Usage
-----

    PYTHONPATH=src python3 scripts/discover_pairs.py
    PYTHONPATH=src python3 scripts/discover_pairs.py --top 10 --min-volume 500000
    PYTHONPATH=src python3 scripts/discover_pairs.py --tokens SOL,USDC,JUP --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from discovery.dexscreener import (
    MIN_DEX_COUNT,
    MIN_LIQUIDITY,
    MIN_VOLUME_24H,
    discover_solana_pairs,
)


def _fmt_money(v: float) -> str:
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"


def _print_table(pairs) -> None:
    header = (
        f"{'Pair':<14} {'DEXes':>6} {'Volume':>10} {'Liquidity':>11} "
        f"{'Score':>12} {'Blue-chip':>10}  DEXes"
    )
    print(header)
    print("-" * len(header))
    for p in pairs:
        print(
            f"{p.pair_name:<14} "
            f"{p.dex_count:>6} "
            f"{_fmt_money(p.total_volume_24h):>10} "
            f"{_fmt_money(p.total_liquidity):>11} "
            f"{p.score:>12.0f} "
            f"{'yes' if p.is_blue_chip else 'no':>10}  "
            f"{', '.join(p.dex_names[:5])}"
            f"{'...' if len(p.dex_names) > 5 else ''}"
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--tokens",
        default=os.environ.get("DISCOVERY_TOKENS", ""),
        help="Comma-separated symbols to seed the search (default: a sensible "
             "set of SOL/USDC/USDT/mSOL/jitoSOL/bSOL/JUP/PYTH/BONK/WIF)",
    )
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--min-volume", type=float, default=MIN_VOLUME_24H)
    ap.add_argument("--min-liquidity", type=float, default=MIN_LIQUIDITY)
    ap.add_argument("--min-dex-count", type=int, default=MIN_DEX_COUNT)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--json", action="store_true",
                    help="Output JSON (machine-readable) instead of a table")
    args = ap.parse_args()

    tokens = [t.strip() for t in args.tokens.split(",") if t.strip()] or None
    pairs = discover_solana_pairs(
        search_tokens=tokens,
        min_volume=args.min_volume,
        min_liquidity=args.min_liquidity,
        min_dex_count=args.min_dex_count,
        max_results=args.top,
        timeout=args.timeout,
    )

    if args.json:
        print(json.dumps([{
            "pair": p.pair_name,
            "base": p.base_symbol,
            "quote": p.quote_symbol,
            "dex_count": p.dex_count,
            "volume_24h": p.total_volume_24h,
            "liquidity_usd": p.total_liquidity,
            "dexes": p.dex_names,
            "base_mint": p.base_mint,
            "quote_mint": p.quote_mint,
            "blue_chip": p.is_blue_chip,
            "score": p.score,
        } for p in pairs], indent=2))
    else:
        if not pairs:
            print("No pairs cleared the thresholds. Loosen --min-volume or --min-liquidity.")
            return 1
        _print_table(pairs)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
