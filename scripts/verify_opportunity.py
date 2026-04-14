"""Verify an arbitrage opportunity by checking live prices on both DEXes.

Usage:
    PYTHONPATH=src python scripts/verify_opportunity.py opp_58c6ff03da4d
    PYTHONPATH=src python scripts/verify_opportunity.py opp_58c6ff03da4d --api-url https://arb-trader.yeda-ai.com
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests
from env import load_env

load_env()

# DeFi Llama chain name → WETH contract address
WETH_ADDRESSES = {
    "ethereum": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    "arbitrum": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
    "base": "0x4200000000000000000000000000000000000006",
    "polygon": "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
    "optimism": "0x4200000000000000000000000000000000000006",
    "avalanche": "0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB",
    "bsc": "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
    "scroll": "0x5300000000000000000000000000000000000004",
    "linea": "0xe5D7C2a44FfDDf6b295A15c148167daaAf5Cf34f",
    "fantom": "0x74b23882a30290451A17c44f4F05243b6b58C76d",
    "gnosis": "0x6A023CCd1ff6F2045C3309768eAd9E68F978f6e1",
    "zksync": "0x5AEa5775959fBC2557Cc8789bC1bf90A239D9a91",
}

# Map DEX display names to chain names
DEX_TO_CHAIN = {
    "ethereum": "ethereum", "arbitrum": "arbitrum", "base": "base",
    "polygon": "polygon", "optimism": "optimism", "avalanche": "avalanche",
    "bsc": "bsc", "scroll": "scroll", "linea": "linea", "fantom": "fantom",
    "gnosis": "gnosis", "zksync": "zksync",
}


def get_live_price(chain: str) -> float | None:
    """Fetch current WETH price on a chain from DeFi Llama."""
    weth = WETH_ADDRESSES.get(chain.lower())
    if not weth:
        print(f"  [!] Unknown chain: {chain}")
        return None

    url = f"https://coins.llama.fi/prices/current/{chain.lower()}:{weth}"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        for key, val in data.get("coins", {}).items():
            return val["price"]
    except Exception as e:
        print(f"  [!] DeFi Llama error for {chain}: {e}")
    return None


def resolve_chain(dex_name: str, opp_chain: str) -> str:
    """Extract chain from DEX name or opportunity chain field."""
    lower = dex_name.lower()
    # Try direct match first
    if lower in DEX_TO_CHAIN:
        return DEX_TO_CHAIN[lower]
    # Try splitting "Uniswap-Ethereum" format
    parts = lower.rsplit("-", 1)
    if len(parts) == 2 and parts[1] in DEX_TO_CHAIN:
        return DEX_TO_CHAIN[parts[1]]
    # Fallback to opportunity chain field
    return opp_chain.lower()


def fetch_opportunity(opp_id: str, api_url: str, user: str, password: str) -> dict | None:
    """Fetch opportunity data from the bot API."""
    url = f"{api_url}/opportunities/{opp_id}/full"
    try:
        resp = requests.get(url, auth=(user, password), timeout=10)
        if resp.status_code == 404:
            print(f"Opportunity {opp_id} not found")
            return None
        if resp.status_code == 401:
            print("Authentication failed — check DASHBOARD_USER/DASHBOARD_PASS in .env")
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"API error: {e}")
        return None


def _parse_url_or_id(value: str) -> tuple[str, str]:
    """Parse a full URL or plain opp_id into (api_url, opp_id).

    Accepts:
      https://arb-trader.yeda-ai.com/opportunity/opp_fe3c82cbc001
      opp_fe3c82cbc001
    """
    if value.startswith("http"):
        # Extract opp_id and base URL from full URL
        from urllib.parse import urlparse
        parsed = urlparse(value)
        path_parts = parsed.path.strip("/").split("/")
        opp_id = path_parts[-1]  # last segment is the opp_id
        api_url = f"{parsed.scheme}://{parsed.netloc}"
        return api_url, opp_id
    return "", value


def main():
    parser = argparse.ArgumentParser(description="Verify an arbitrage opportunity against live prices")
    parser.add_argument("opp_id_or_url",
                        help="Opportunity ID or full URL (e.g. opp_58c6ff03da4d or https://arb-trader.yeda-ai.com/opportunity/opp_58c6ff03da4d)")
    parser.add_argument("--api-url", default=None,
                        help="Bot API URL (default: auto-detect from URL or http://localhost:8000)")
    args = parser.parse_args()

    url_from_arg, opp_id = _parse_url_or_id(args.opp_id_or_url)
    api_url = args.api_url or url_from_arg or "http://localhost:8000"

    import os
    user = os.environ.get("DASHBOARD_USER", "admin")
    password = os.environ.get("DASHBOARD_PASS", "adminTest")

    print(f"Fetching opportunity {opp_id} from {api_url}...")
    data = fetch_opportunity(opp_id, api_url, user, password)
    if not data:
        sys.exit(1)

    opp = data["opportunity"]
    pricing = data.get("pricing")
    risk = data.get("risk_decision")

    # --- Display opportunity ---
    print(f"\n{'='*60}")
    print(f"  Opportunity: {opp['opportunity_id']}")
    print(f"  Pair:        {opp['pair']}")
    print(f"  Buy DEX:     {opp['buy_dex']}")
    print(f"  Sell DEX:    {opp['sell_dex']}")
    print(f"  Chain:       {opp.get('chain', 'unknown')}")
    print(f"  Spread:      {float(Decimal(opp['spread_bps'])):.4f}%")
    print(f"  Status:      {opp['status']}")
    print(f"  Detected:    {opp['detected_at']}")
    print(f"{'='*60}")

    if pricing:
        print(f"\n  Pricing at detection time:")
        print(f"    Input (buy cost):    ${float(Decimal(pricing['input_amount'])):.2f}")
        print(f"    Output (sell):       ${float(Decimal(pricing['estimated_output'])):.2f}")
        print(f"    DEX fees:            ${float(Decimal(pricing['fee_cost'])):.2f}")
        print(f"    Slippage:            ${float(Decimal(pricing['slippage_cost'])):.2f}")
        print(f"    Gas:                 {pricing['gas_estimate']} ETH")
        print(f"    Expected profit:     {float(Decimal(pricing['expected_net_profit'])):.6f} ETH")

    if risk:
        print(f"\n  Risk decision:")
        print(f"    Approved: {'YES' if risk['approved'] else 'NO (simulation mode)'}")
        print(f"    Reason:   {risk['reason_code']}")

    # --- Verify with live prices ---
    buy_chain = resolve_chain(opp["buy_dex"], opp.get("chain", ""))
    sell_chain = resolve_chain(opp["sell_dex"], opp.get("chain", ""))

    print(f"\n  Verifying live prices...")
    print(f"    Buy chain:  {buy_chain}")
    print(f"    Sell chain: {sell_chain}")

    buy_price = get_live_price(buy_chain)
    sell_price = get_live_price(sell_chain)

    if buy_price and sell_price:
        live_spread = (sell_price - buy_price) / buy_price * 100
        is_cross_chain = buy_chain != sell_chain

        print(f"\n  Live prices (DeFi Llama):")
        print(f"    {buy_chain.title():12s} WETH: ${buy_price:,.2f}")
        print(f"    {sell_chain.title():12s} WETH: ${sell_price:,.2f}")
        print(f"    Live spread:          {live_spread:+.4f}%")
        print(f"    Cross-chain:          {'YES' if is_cross_chain else 'NO (same-chain, executable)'}")

        # Compare with detected spread
        detected_spread = float(Decimal(opp["spread_bps"]))
        drift = live_spread - detected_spread
        print(f"\n  Comparison:")
        print(f"    Detected spread:      {detected_spread:+.4f}%")
        print(f"    Live spread:          {live_spread:+.4f}%")
        print(f"    Drift:                {drift:+.4f}%")

        if live_spread > 0.5:
            print(f"\n  VERDICT: Spread STILL EXISTS ({live_spread:.2f}%)")
        elif live_spread > 0:
            print(f"\n  VERDICT: Spread exists but THIN ({live_spread:.2f}%) — may not cover costs")
        else:
            print(f"\n  VERDICT: Spread CLOSED — no longer profitable")

        if is_cross_chain:
            print(f"  NOTE: Cross-chain opportunity — cannot be executed atomically.")
            print(f"         Requires bridging, which adds time and risk.")
    else:
        print("  Could not fetch live prices for comparison")

    print()


if __name__ == "__main__":
    main()
