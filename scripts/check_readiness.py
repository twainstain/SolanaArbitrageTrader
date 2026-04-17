#!/usr/bin/env python3
"""Check Phase-1 scanner readiness for SolanaTrader.

This is a lightweight equivalent of the EVM ``check_readiness.py`` — it
verifies that enough environment is present to run the Jupiter-backed
scanner, not that live execution is safe (Phase 3+).

Usage:
    PYTHONPATH=src python scripts/check_readiness.py
    PYTHONPATH=src python scripts/check_readiness.py --config config/example_config.json
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/example_config.json")
    args = parser.parse_args()

    from core.env import load_env, get_solana_rpc_urls, get_jupiter_api_url
    from core.config import BotConfig
    from core.tokens import is_known

    load_env()

    config = BotConfig.from_file(args.config)

    rpcs = get_solana_rpc_urls()
    jupiter_url = get_jupiter_api_url()

    blockers: list[str] = []

    for pair_asset in (config.base_asset, config.quote_asset):
        if not is_known(pair_asset):
            blockers.append(f"Unknown SPL symbol in primary pair: {pair_asset}")

    if config.extra_pairs:
        for p in config.extra_pairs:
            for sym in (p.base_asset, p.quote_asset):
                if not is_known(sym):
                    blockers.append(f"Unknown SPL symbol in extra pair {p.pair}: {sym}")

    if len(config.venues) < 2:
        blockers.append(f"Fewer than 2 venues configured ({len(config.venues)})")

    # Optional — RPC is only needed for wallet balance / Phase 3 execution
    # but we still warn if none is configured.
    warnings: list[str] = []
    if not rpcs:
        warnings.append(
            "SOLANA_RPC_URL not set — wallet balance checks and Phase 3 execution will be disabled."
        )

    ready = not blockers
    status = "READY" if ready else "NOT READY"
    color = "\033[92m" if ready else "\033[91m"
    reset = "\033[0m"

    print()
    print("=" * 60)
    print(f"  SolanaTrader Phase-1 Readiness: {color}{status}{reset}")
    print("=" * 60)
    print()
    print(f"  Config:        {args.config}")
    print(f"  Primary pair:  {config.pair}")
    print(f"  Trade size:    {config.trade_size} {config.base_asset}")
    print(f"  Min profit:    {config.min_profit_base} {config.base_asset}")
    print(f"  Slippage:      {config.slippage_bps} bps")
    print(f"  Priority fee:  {config.priority_fee_lamports} lamports "
          f"({config.priority_fee_sol()} SOL)")
    print(f"  Jupiter API:   {jupiter_url}")
    print(f"  Solana RPCs:   {len(rpcs)} configured")
    print()

    if warnings:
        print("  Warnings:")
        for w in warnings:
            print(f"    - {w}")
        print()

    if blockers:
        print(f"  {color}Blockers ({len(blockers)}):{reset}")
        for b in blockers:
            print(f"    - {b}")
        print()
        sys.exit(1)
    print("  All Phase-1 checks passed.  You can run:")
    print(f"    PYTHONPATH=src python -m main --config {args.config} --iterations 5 --dry-run")
    print()


if __name__ == "__main__":
    main()
