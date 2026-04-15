#!/usr/bin/env python3
"""Test all RPC endpoints configured in .env.

Validates:
  1. Each RPC URL is reachable
  2. It returns the correct chain ID (catches misconfigured URLs)
  3. Latency is acceptable (<2s)
  4. Alchemy vs Infura vs public endpoint detection

Usage:
    PYTHONPATH=src python scripts/test_rpc_endpoints.py

Requires .env to be populated with RPC_* variables.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Add src to path so we can import project modules.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from env import load_env, get_rpc_overrides
from contracts import PUBLIC_RPC_URLS

load_env()

# Expected chain IDs per chain name.
EXPECTED_CHAIN_IDS: dict[str, int] = {
    "ethereum": 1,
    "arbitrum": 42161,
    "base": 8453,
    "polygon": 137,
    "optimism": 10,
    "avax": 43114,
    "bsc": 56,
    "fantom": 250,
    "linea": 59144,
    "scroll": 534352,
    "zksync": 324,
    "gnosis": 100,
}


def classify_provider(url: str) -> str:
    """Identify the RPC provider from the URL."""
    url_lower = url.lower()
    if "alchemy.com" in url_lower:
        return "Alchemy"
    if "infura.io" in url_lower:
        return "Infura"
    if "llamarpc.com" in url_lower:
        return "LlamaRPC"
    if "ankr.com" in url_lower:
        return "Ankr"
    if "1rpc.io" in url_lower:
        return "1RPC"
    if "publicnode.com" in url_lower:
        return "PublicNode"
    return "Public/Other"


def check_url_chain_match(url: str, expected_chain: str) -> str | None:
    """Check if the URL hostname matches the expected chain.

    Returns an error string if there's a mismatch, None if OK.
    """
    url_lower = url.lower()
    chain_lower = expected_chain.lower()

    # Map chain names to expected URL substrings.
    chain_url_hints: dict[str, list[str]] = {
        "ethereum": ["eth-mainnet", "eth."],
        "arbitrum": ["arb-mainnet", "arb1.arbitrum"],
        "base": ["base-mainnet", "mainnet.base.org"],
        "polygon": ["polygon-mainnet", "matic", "polygon"],
        "optimism": ["opt-mainnet", "optimism-mainnet", "mainnet.optimism"],
        "avax": ["avax", "avalanche", "api.avax.network"],
        "bsc": ["bsc", "binance"],
        "fantom": ["fantom"],
        "linea": ["linea"],
        "scroll": ["scroll"],
        "zksync": ["zksync"],
        "gnosis": ["gnosis"],
    }

    hints = chain_url_hints.get(chain_lower, [])
    if not hints:
        return None  # No hints to check.

    # Check if ANY hint matches the URL.
    if any(hint in url_lower for hint in hints):
        return None  # Match found.

    # Check for known mismatches.
    for other_chain, other_hints in chain_url_hints.items():
        if other_chain == chain_lower:
            continue
        if any(hint in url_lower for hint in other_hints):
            return f"URL appears to be for {other_chain}, not {expected_chain}"

    return None  # Can't determine, assume OK.


def probe_endpoint(chain: str, url: str) -> dict:
    """Test a single RPC endpoint. Returns result dict."""
    result = {
        "chain": chain,
        "url": url[:60] + "..." if len(url) > 60 else url,
        "provider": classify_provider(url),
        "status": "UNKNOWN",
        "chain_id": None,
        "latency_ms": None,
        "error": None,
        "url_mismatch": None,
    }

    # Check URL/chain mismatch before even calling.
    mismatch = check_url_chain_match(url, chain)
    if mismatch:
        result["url_mismatch"] = mismatch

    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 10}))

        start = time.monotonic()
        chain_id = w3.eth.chain_id
        latency = (time.monotonic() - start) * 1000

        result["chain_id"] = chain_id
        result["latency_ms"] = round(latency)

        expected = EXPECTED_CHAIN_IDS.get(chain)
        if expected and chain_id != expected:
            result["status"] = "WRONG_CHAIN"
            result["error"] = f"Expected chain_id={expected}, got {chain_id}"
        elif latency > 2000:
            result["status"] = "SLOW"
            result["error"] = f"Latency {latency:.0f}ms > 2000ms threshold"
        else:
            result["status"] = "OK"

    except Exception as exc:
        result["status"] = "FAIL"
        result["error"] = str(exc)[:100]

    return result


def main() -> None:
    overrides = get_rpc_overrides()

    # Chains we care about (configured in multichain_onchain_config.json).
    priority_chains = ["ethereum", "arbitrum", "base", "optimism", "polygon", "avax"]

    print("=" * 80)
    print("RPC Endpoint Validation")
    print("=" * 80)
    print()

    results = []
    for chain in priority_chains:
        url = overrides.get(chain)
        source = ".env override"
        if not url:
            url = PUBLIC_RPC_URLS.get(chain)
            source = "public fallback"
        if not url:
            results.append({
                "chain": chain, "status": "MISSING", "provider": "-",
                "error": "No RPC URL configured", "url": "-",
                "latency_ms": None, "chain_id": None, "url_mismatch": None,
            })
            continue

        print(f"Testing {chain:12s} ({source})...", end=" ", flush=True)
        r = probe_endpoint(chain, url)
        r["source"] = source
        results.append(r)

        if r["status"] == "OK":
            print(f"OK  ({r['provider']}, {r['latency_ms']}ms, chain_id={r['chain_id']})")
        else:
            print(f"{r['status']}  — {r['error']}")

        if r["url_mismatch"]:
            print(f"  ⚠ URL MISMATCH: {r['url_mismatch']}")

    # Summary
    print()
    print("=" * 80)
    print("Summary")
    print("=" * 80)
    print()
    print(f"{'Chain':12s} {'Status':12s} {'Provider':12s} {'Latency':>10s} {'Chain ID':>10s} {'Notes'}")
    print("-" * 80)

    issues = []
    for r in results:
        lat = f"{r['latency_ms']}ms" if r['latency_ms'] else "-"
        cid = str(r['chain_id']) if r['chain_id'] else "-"
        notes = ""
        if r["url_mismatch"]:
            notes = f"⚠ {r['url_mismatch']}"
        elif r["error"] and r["status"] != "OK":
            notes = r["error"][:40]
        print(f"{r['chain']:12s} {r['status']:12s} {r['provider']:12s} {lat:>10s} {cid:>10s} {notes}")

        if r["status"] != "OK":
            issues.append(r)

    print()
    if not issues:
        print("All endpoints OK.")
    else:
        print(f"{len(issues)} issue(s) found:")
        for r in issues:
            print(f"  - {r['chain']}: {r['status']} — {r.get('error') or r.get('url_mismatch')}")

        # Suggest fixes.
        print()
        print("Suggested .env fixes:")
        for r in issues:
            if r.get("url_mismatch") and "appears to be for" in (r["url_mismatch"] or ""):
                print(f"  {r['chain'].upper()}: URL points to wrong chain. Create a new Alchemy app for {r['chain']}:")
                print(f"    RPC_{r['chain'].upper()}=https://{r['chain']}-mainnet.g.alchemy.com/v2/YOUR_KEY")
            elif r["status"] == "MISSING":
                print(f"  {r['chain'].upper()}: No RPC configured. Add to .env:")
                print(f"    RPC_{r['chain'].upper()}=https://{r['chain']}-mainnet.g.alchemy.com/v2/YOUR_KEY")

    sys.exit(1 if issues else 0)


if __name__ == "__main__":
    main()
