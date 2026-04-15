#!/usr/bin/env python3
"""Check launch readiness for live execution.

One command that tells the operator exactly what's ready and what's blocking.

Usage:
    PYTHONPATH=src python scripts/check_readiness.py
    PYTHONPATH=src python scripts/check_readiness.py --config config/arbitrum_live_execution_config.json
    PYTHONPATH=src python scripts/check_readiness.py --api http://localhost:8000  # check running instance
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def check_from_config(config_path: str) -> None:
    """Check readiness by analyzing config + env directly (no running bot needed)."""
    from env import load_env
    load_env()

    from config import BotConfig
    from contracts import PUBLIC_RPC_URLS
    from chain_executor import SWAP_ROUTERS, AAVE_V3_POOL

    config = BotConfig.from_file(config_path)

    # Determine target chain from config DEXes.
    chains = sorted(set(d.chain for d in config.dexes if d.chain))
    target_chain = chains[0] if len(chains) == 1 else "arbitrum"

    # Executor env vars.
    executor_key = bool(os.environ.get("EXECUTOR_PRIVATE_KEY", ""))
    executor_contract = bool(os.environ.get("EXECUTOR_CONTRACT", ""))

    # RPC for target chain.
    rpc_var = f"RPC_{target_chain.upper()}"
    rpc_url = os.environ.get(rpc_var, "")
    rpc_configured = bool(rpc_url) or target_chain in PUBLIC_RPC_URLS

    # Aave pool for target chain.
    aave_pool = target_chain in AAVE_V3_POOL

    # Swap routers for target chain.
    chain_routers = SWAP_ROUTERS.get(target_chain, {})

    # Executable vs detection-only DEXes.
    executable_dexes = []
    detection_only = []
    unsupported_types = {"curve", "traderjoe_lb"}
    for dex in config.dexes:
        if dex.dex_type in unsupported_types:
            detection_only.append(f"{dex.name} ({dex.dex_type})")
        elif dex.dex_type and dex.dex_type in chain_routers:
            executable_dexes.append(f"{dex.name} ({dex.dex_type})")
        else:
            detection_only.append(f"{dex.name} ({dex.dex_type or '?'})")

    # Pairs.
    pairs = [config.pair]
    if config.extra_pairs:
        pairs.extend(p.pair for p in config.extra_pairs)

    # Blockers.
    blockers = []
    if not executor_key:
        blockers.append("EXECUTOR_PRIVATE_KEY not set")
    if not executor_contract:
        blockers.append("EXECUTOR_CONTRACT not set (deploy contract first)")
    if not rpc_configured:
        blockers.append(f"No RPC for {target_chain} ({rpc_var} not set)")
    if not aave_pool:
        blockers.append(f"No Aave V3 pool address for {target_chain}")
    if not executable_dexes:
        blockers.append("No executable DEXes in config")

    # Print report.
    ready = not blockers
    status_icon = "READY" if ready else "NOT READY"
    status_color = "\033[92m" if ready else "\033[91m"
    reset = "\033[0m"

    print()
    print("=" * 60)
    print(f"  Launch Readiness: {status_color}{status_icon}{reset}")
    print("=" * 60)
    print()
    print(f"  Config:         {config_path}")
    print(f"  Target Chain:   {target_chain}")
    print(f"  Chains in Config: {', '.join(chains)}")
    print(f"  Pairs:          {', '.join(pairs)}")
    print()

    print("  Infrastructure:")
    print(f"    Executor Key:     {'set' if executor_key else 'MISSING'}")
    print(f"    Executor Contract: {'set' if executor_contract else 'MISSING'}")
    print(f"    RPC ({target_chain}):  {'configured' if rpc_configured else 'MISSING'}")
    print(f"    Aave V3 Pool:     {'configured' if aave_pool else 'MISSING'}")
    print()

    print(f"  Executable DEXes ({len(executable_dexes)}):")
    for d in executable_dexes:
        print(f"    {d}")
    if not executable_dexes:
        print("    (none)")
    print()

    if detection_only:
        print(f"  Detection-Only DEXes ({len(detection_only)}):")
        for d in detection_only:
            print(f"    {d}")
        print()

    if blockers:
        print(f"  {status_color}Blockers ({len(blockers)}):{reset}")
        for b in blockers:
            print(f"    - {b}")
        print()
        print("  Fix blockers, then re-run this check.")
    else:
        print("  All checks passed. Safe to enable live execution:")
        print(f"    curl -u admin:$DASHBOARD_PASS -X POST \\")
        print(f"      http://localhost:8000/execution \\")
        print(f"      -H 'Content-Type: application/json' \\")
        print(f"      -d '{{\"enabled\": true}}'")

    print()

    # Financial summary.
    print("  Financial Parameters:")
    print(f"    Trade Size:       {config.trade_size} {config.base_asset}")
    print(f"    Min Profit:       {config.min_profit_base} {config.base_asset} (~${float(config.min_profit_base) * 2300:.2f})")
    print(f"    Est Gas Cost:     {config.estimated_gas_cost_base} {config.base_asset}")
    print(f"    Flash Loan Fee:   {config.flash_loan_fee_bps} bps ({config.flash_loan_provider})")
    print(f"    Slippage:         {config.slippage_bps} bps")
    print()

    sys.exit(0 if ready else 1)


def check_from_api(api_url: str) -> None:
    """Check readiness from a running bot instance via API."""
    import requests

    user = os.environ.get("DASHBOARD_USER", "admin")
    password = os.environ.get("DASHBOARD_PASS", "adminTest")

    try:
        r = requests.get(f"{api_url}/launch-readiness", auth=(user, password), timeout=5)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"Error connecting to {api_url}: {e}")
        sys.exit(1)

    ready = data.get("launch_ready", False)
    status_icon = "READY" if ready else "NOT READY"
    status_color = "\033[92m" if ready else "\033[91m"
    reset = "\033[0m"

    # Also fetch ops for extra context.
    try:
        ops = requests.get(f"{api_url}/operations", auth=(user, password), timeout=5).json()
    except Exception:
        ops = {}

    print()
    print("=" * 60)
    print(f"  Launch Readiness: {status_color}{status_icon}{reset}")
    print("=" * 60)
    print()
    print(f"  API:            {api_url}")
    print(f"  Target Chain:   {data.get('launch_chain', '?')}")
    print(f"  Executor Key:   {'set' if data.get('executor_key_configured') else 'MISSING'}")
    print(f"  Executor Contract: {'set' if data.get('executor_contract_configured') else 'MISSING'}")
    print(f"  RPC:            {'configured' if data.get('rpc_configured') else 'MISSING'}")
    print()

    if ops:
        print(f"  Operations:")
        print(f"    DB Backend:       {ops.get('db_backend', '?')}")
        print(f"    Discovered Pairs: {ops.get('discovered_pairs_count', 0)}")
        print(f"    Enabled Pools:    {ops.get('enabled_pools_total', 0)}")
        exe_chains = ops.get('live_executable_chains', [])
        exe_dexes = ops.get('live_executable_dexes', [])
        print(f"    Executable Chains: {', '.join(exe_chains) if exe_chains else 'none'}")
        print(f"    Executable DEXes:  {', '.join(exe_dexes) if exe_dexes else 'none'}")
        print()

    blockers = data.get("launch_blockers", [])
    if blockers:
        print(f"  {status_color}Blockers ({len(blockers)}):{reset}")
        for b in blockers:
            print(f"    - {b}")
    else:
        print("  All checks passed.")

    print()
    sys.exit(0 if ready else 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check launch readiness for live execution")
    parser.add_argument("--config", default="config/arbitrum_live_execution_config.json",
                        help="Config file to check (default: Arbitrum live config)")
    parser.add_argument("--api", default=None,
                        help="Check a running instance via API (e.g., http://localhost:8000)")
    args = parser.parse_args()

    if args.api:
        check_from_api(args.api)
    else:
        check_from_config(args.config)


if __name__ == "__main__":
    main()
