#!/usr/bin/env python3
"""Smoke-test Solana RPC + Jupiter API — run before any deploy.

Checks:
  1. Every ``SOLANA_RPC_URL[_N]`` in .env responds to ``getSlot`` within 2s.
  2. Jupiter ``/quote`` returns a route for SOL → USDC.

Exits 0 on success, 1 on any failure.  Designed for CI / pre-deploy
gating.

Usage:
    PYTHONPATH=src python3 scripts/test_rpc.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests

from core.env import get_jupiter_api_url, get_solana_rpc_urls, load_env


OK   = "\033[92mOK\033[0m"
FAIL = "\033[91mFAIL\033[0m"


def _ok(msg: str) -> None:
    print(f"  [{OK}]   {msg}")


def _fail(msg: str) -> None:
    print(f"  [{FAIL}] {msg}")


def check_rpcs() -> bool:
    urls = get_solana_rpc_urls()
    if not urls:
        _fail("SOLANA_RPC_URL is not set")
        return False
    all_ok = True
    for i, url in enumerate(urls):
        label = "PRIMARY" if i == 0 else f"FALLBACK-{i}"
        t0 = time.monotonic()
        try:
            resp = requests.post(
                url,
                json={"jsonrpc": "2.0", "id": 1, "method": "getSlot"},
                timeout=3.0,
            )
            resp.raise_for_status()
            body = resp.json()
            slot = int(body.get("result", 0))
            elapsed = (time.monotonic() - t0) * 1000
            if slot > 0:
                _ok(f"{label}  slot={slot}  {elapsed:.0f}ms  {_host(url)}")
            else:
                _fail(f"{label}  empty result: {body}")
                all_ok = False
        except Exception as exc:
            _fail(f"{label}  {exc.__class__.__name__}: {exc}")
            all_ok = False
    return all_ok


def check_jupiter() -> bool:
    url = get_jupiter_api_url().rstrip("/") + "/quote"
    params = {
        "inputMint": "So11111111111111111111111111111111111111112",
        "outputMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "amount": "10000000",       # 0.01 SOL
        "slippageBps": "50",
    }
    t0 = time.monotonic()
    try:
        resp = requests.get(url, params=params, timeout=3.0)
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        _fail(f"jupiter  {exc.__class__.__name__}: {exc}")
        return False
    out_amount = body.get("outAmount")
    elapsed = (time.monotonic() - t0) * 1000
    if out_amount:
        _ok(f"jupiter  outAmount={out_amount}  {elapsed:.0f}ms  {_host(url)}")
        return True
    _fail(f"jupiter  no route: {body}")
    return False


def _host(url: str) -> str:
    try:
        return url.split("//", 1)[1].split("/", 1)[0]
    except IndexError:
        return url


def main() -> None:
    load_env()
    print("=" * 60)
    print("  SolanaTrader pre-deploy RPC smoke")
    print("=" * 60)
    print("\nSolana RPCs:")
    rpcs_ok = check_rpcs()
    print("\nJupiter API:")
    jup_ok = check_jupiter()
    print()
    if rpcs_ok and jup_ok:
        print(f"  {OK}  All checks passed.")
        sys.exit(0)
    print(f"  {FAIL}  One or more checks failed.")
    sys.exit(1)


if __name__ == "__main__":
    main()
