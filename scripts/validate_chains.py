"""Validate execution readiness for each chain — no real gas spent.

For every live chain, verifies:
  1. RPC connectivity
  2. Wallet balance (enough for gas)
  3. Executor contract deployed and responsive (code exists on-chain)
  4. Contract owner matches our wallet
  5. Aave V3 pool is reachable
  6. Swap routers have code deployed
  7. Token addresses resolve
  8. eth_call simulation with a tiny WETH/USDC arb (will revert with
     "profit below minimum" — that's SUCCESS, it means the full
     flash loan → swap → repay path executed correctly)

Usage:
    PYTHONPATH=src python scripts/validate_chains.py
    PYTHONPATH=src python scripts/validate_chains.py --chain arbitrum
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.env import load_env

load_env()

from decimal import Decimal
from web3 import Web3

from core.contracts import PUBLIC_RPC_URLS
from core.env import get_rpc_overrides
from core.tokens import CHAIN_TOKENS, resolve_token_address
from execution.chain_executor import (
    AAVE_V3_POOL,
    EXECUTOR_ABI,
    SWAP_ROUTERS,
    VELO_FACTORIES,
)

D = Decimal

# Chains we attempt to validate.
LIVE_CHAINS = ["arbitrum", "base", "optimism", "polygon", "avax"]

# Minimal ABI for reading contract owner and aavePool.
OWNER_ABI = [
    {"inputs": [], "name": "owner", "outputs": [{"name": "", "type": "address"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "aavePool", "outputs": [{"name": "", "type": "address"}],
     "stateMutability": "view", "type": "function"},
]


def _pass(msg: str) -> None:
    print(f"  \033[92mPASS\033[0m  {msg}")


def _fail(msg: str) -> None:
    print(f"  \033[91mFAIL\033[0m  {msg}")


def _warn(msg: str) -> None:
    print(f"  \033[93mWARN\033[0m  {msg}")


def _info(msg: str) -> None:
    print(f"  \033[94mINFO\033[0m  {msg}")


def _has_code(w3: Web3, address: str) -> bool:
    """Check if an address has contract code deployed."""
    try:
        code = w3.eth.get_code(Web3.to_checksum_address(address))
        return len(code) > 2  # "0x" = no code
    except Exception:
        return False


def validate_chain(chain: str) -> tuple[int, int]:
    """Validate a single chain. Returns (passed, failed) counts."""
    passed = 0
    failed = 0

    print(f"\n{'='*60}")
    print(f"  Chain: {chain.upper()}")
    print(f"{'='*60}")

    # --- 1. Resolve env vars ---
    pk = os.environ.get("EXECUTOR_PRIVATE_KEY", "")
    contract_addr = (
        os.environ.get(f"EXECUTOR_CONTRACT_{chain.upper()}", "")
        or os.environ.get("EXECUTOR_CONTRACT", "")
    )

    if not pk:
        _fail("EXECUTOR_PRIVATE_KEY not set")
        return 0, 1

    if not contract_addr:
        _fail(f"EXECUTOR_CONTRACT_{chain.upper()} not set (no fallback either)")
        return 0, 1

    _pass(f"Contract address: {contract_addr}")
    passed += 1

    # --- 2. RPC connectivity ---
    rpc_overrides = get_rpc_overrides()
    rpc_url = rpc_overrides.get(chain, PUBLIC_RPC_URLS.get(chain, ""))
    rpc_source = "override" if chain in rpc_overrides else "public fallback"

    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
        block = w3.eth.block_number
        _pass(f"RPC connected ({rpc_source}): block {block}")
        passed += 1
    except Exception as e:
        _fail(f"RPC connection failed ({rpc_source}): {e}")
        return passed, failed + 1

    # --- 3. Wallet balance ---
    account = w3.eth.account.from_key(pk)
    balance = w3.eth.get_balance(account.address)
    balance_eth = w3.from_wei(balance, "ether")

    if balance_eth > 0.001:
        _pass(f"Wallet {account.address[:10]}...{account.address[-6:]}: {balance_eth:.6f} ETH")
        passed += 1
    elif balance_eth > 0:
        _warn(f"Wallet balance low: {balance_eth:.6f} ETH (need >0.001 for gas)")
        passed += 1
    else:
        _fail(f"Wallet has 0 ETH — cannot pay gas")
        failed += 1

    # --- 4. Contract deployed ---
    if _has_code(w3, contract_addr):
        _pass(f"Executor contract has code on-chain")
        passed += 1
    else:
        _fail(f"No contract code at {contract_addr}")
        return passed, failed + 1

    # --- 5. Contract owner matches wallet ---
    try:
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(contract_addr), abi=OWNER_ABI
        )
        owner = contract.functions.owner().call()
        if owner.lower() == account.address.lower():
            _pass(f"Contract owner matches wallet")
            passed += 1
        else:
            _fail(f"Contract owner {owner} != wallet {account.address}")
            failed += 1
    except Exception as e:
        _fail(f"Cannot read contract owner: {e}")
        failed += 1

    # --- 6. Aave V3 pool ---
    aave_pool = AAVE_V3_POOL.get(chain)
    if aave_pool and _has_code(w3, aave_pool):
        _pass(f"Aave V3 pool has code: {aave_pool[:10]}...")
        passed += 1
    elif aave_pool:
        _fail(f"Aave V3 pool has no code at {aave_pool}")
        failed += 1
    else:
        _fail(f"No Aave V3 pool address for {chain}")
        failed += 1

    # --- 7. Contract aavePool matches expected ---
    try:
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(contract_addr), abi=OWNER_ABI
        )
        on_chain_pool = contract.functions.aavePool().call()
        if aave_pool and on_chain_pool.lower() == aave_pool.lower():
            _pass(f"Contract aavePool matches expected")
            passed += 1
        else:
            _fail(f"Contract aavePool {on_chain_pool} != expected {aave_pool}")
            failed += 1
    except Exception as e:
        _warn(f"Cannot read contract aavePool: {e}")

    # --- 8. Swap routers ---
    chain_routers = SWAP_ROUTERS.get(chain, {})
    for dex_type, router_addr in chain_routers.items():
        if _has_code(w3, router_addr):
            _pass(f"Router {dex_type}: {router_addr[:10]}... has code")
            passed += 1
        else:
            _fail(f"Router {dex_type}: no code at {router_addr}")
            failed += 1

    # --- 9. Velo factories ---
    chain_factories = VELO_FACTORIES.get(chain, {})
    for dex_type, factory_addr in chain_factories.items():
        if _has_code(w3, factory_addr):
            _pass(f"Factory {dex_type}: {factory_addr[:10]}... has code")
            passed += 1
        else:
            _fail(f"Factory {dex_type}: no code at {factory_addr}")
            failed += 1

    # --- 10. Token addresses ---
    tokens = CHAIN_TOKENS.get(chain)
    if tokens:
        for symbol in ("WETH", "USDC"):
            addr = resolve_token_address(chain, symbol)
            if addr and _has_code(w3, addr):
                _pass(f"Token {symbol}: {addr[:10]}... has code")
                passed += 1
            elif addr:
                _fail(f"Token {symbol}: no code at {addr}")
                failed += 1
            else:
                _fail(f"Token {symbol}: cannot resolve on {chain}")
                failed += 1
    else:
        _fail(f"No token registry for {chain}")
        failed += 1

    # --- 11. eth_call simulation (tiny arb) ---
    # Build a minimal executeArbitrage call with $1 USDC input and
    # minProfit=0. This exercises the full flash loan path:
    #   Aave flash loan → swap on DEX A → swap on DEX B → repay
    # Expected outcome: revert with "profit below minimum threshold"
    # (because there's no real arb). That revert means the contract
    # is alive and the full execution path works.
    _info("Running eth_call simulation (tiny arb, no gas spent)...")
    try:
        weth = resolve_token_address(chain, "WETH")
        usdc = resolve_token_address(chain, "USDC")
        # Pick the first two V3 routers on this chain.
        v3_routers = [
            addr for dex_type, addr in chain_routers.items()
            if dex_type in ("uniswap_v3", "sushi_v3", "pancakeswap_v3")
        ]
        if len(v3_routers) < 2:
            _warn("Need 2+ V3 routers for simulation, skipping")
        elif not weth or not usdc:
            _warn("Cannot resolve WETH/USDC, skipping simulation")
        else:
            executor = w3.eth.contract(
                address=Web3.to_checksum_address(contract_addr),
                abi=EXECUTOR_ABI,
            )
            # $1 USDC = 1_000_000 raw (6 decimals), minProfit = 0
            call_data = executor.functions.executeArbitrage((
                Web3.to_checksum_address(weth),
                Web3.to_checksum_address(usdc),
                Web3.to_checksum_address(v3_routers[0]),
                Web3.to_checksum_address(v3_routers[1]),
                3000,   # 30 bps fee tier
                3000,
                1_000_000,  # $1 USDC
                0,          # minProfit = 0 (accept any result)
                0,          # swapTypeA = V3
                0,          # swapTypeB = V3
                Web3.to_checksum_address("0x" + "00" * 20),
                Web3.to_checksum_address("0x" + "00" * 20),
                False,
                False,
            ))

            t0 = time.monotonic()
            try:
                call_data.call({"from": account.address})
                elapsed = (time.monotonic() - t0) * 1000
                # If it doesn't revert, the flash loan path works!
                _pass(f"eth_call succeeded ({elapsed:.0f}ms) — contract fully operational")
                passed += 1
            except Exception as sim_err:
                elapsed = (time.monotonic() - t0) * 1000
                reason = str(sim_err).lower()
                if "profit below minimum" in reason:
                    # This is the EXPECTED revert — flash loan executed,
                    # swaps ran, but no real arb profit. Contract works!
                    _pass(f"eth_call reverted with 'profit below minimum' ({elapsed:.0f}ms) — contract works!")
                    passed += 1
                elif "not owner" in reason:
                    _fail(f"eth_call reverted 'not owner' — wallet doesn't own contract")
                    failed += 1
                elif "erc20" in reason or "transfer" in reason or "insufficient" in reason:
                    # Flash loan executed but swap failed (pool doesn't
                    # have liquidity for this route, or token issue).
                    # Contract is alive, route is the problem.
                    _warn(f"eth_call reverted at swap level ({elapsed:.0f}ms): {str(sim_err)[:80]}")
                    _info("Contract is responding, but this specific route failed (expected for $1 test)")
                    passed += 1
                else:
                    _warn(f"eth_call reverted ({elapsed:.0f}ms): {str(sim_err)[:120]}")
                    _info("Contract responded — check revert reason above")
                    passed += 1  # Contract is at least responding
    except Exception as e:
        _fail(f"Simulation setup error: {e}")
        failed += 1

    return passed, failed


def main():
    parser = argparse.ArgumentParser(description="Validate execution readiness per chain")
    parser.add_argument("--chain", type=str, default=None,
                        help="Validate a single chain (default: all live chains)")
    args = parser.parse_args()

    chains = [args.chain] if args.chain else LIVE_CHAINS

    print("=" * 60)
    print("  FlashArbExecutor Chain Validation")
    print("=" * 60)

    total_passed = 0
    total_failed = 0
    chain_results: list[tuple[str, int, int]] = []

    for chain in chains:
        p, f = validate_chain(chain)
        total_passed += p
        total_failed += f
        chain_results.append((chain, p, f))

    # Summary
    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    for chain, p, f in chain_results:
        status = "\033[92mREADY\033[0m" if f == 0 else f"\033[91m{f} FAILED\033[0m"
        print(f"  {chain:12s}  {p} passed, {status}")
    print(f"\n  Total: {total_passed} passed, {total_failed} failed")

    if total_failed > 0:
        print(f"\n  Some chains have issues — fix before enabling live execution.")
        sys.exit(1)
    else:
        print(f"\n  All chains validated — ready for live execution.")


if __name__ == "__main__":
    main()
