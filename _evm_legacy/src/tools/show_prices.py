"""Utility to display current DeFi Llama prices and exchange info."""

from __future__ import annotations

import requests

from core.contracts import (
    BALANCER_POOL_IDS,
    BALANCER_VAULT,
    PUBLIC_RPC_URLS,
    SUSHI_V3_QUOTER,
    UNISWAP_FEE_TIERS,
    UNISWAP_V3_QUOTER_V2,
)
from market.subgraphs import (
    BALANCER_V2_SUBGRAPH,
    SUSHI_V3_POOLS,
    SUSHI_V3_SUBGRAPH,
    UNISWAP_V3_POOLS,
    UNISWAP_V3_SUBGRAPH,
)
from core.tokens import CHAIN_TOKENS, defillama_coin_id

DEFILLAMA_URL = "https://coins.llama.fi/prices/current"


def show_exchange_info() -> None:
    """Print supported exchanges, contracts, pools, and RPC endpoints."""

    print("=" * 80)
    print("  EXCHANGE INFO")
    print("=" * 80)

    # -- Supported chains & RPC endpoints --
    print("\n--- Supported Chains & RPC Endpoints ---\n")
    for chain in sorted(PUBLIC_RPC_URLS):
        print(f"  {chain:<12}  {PUBLIC_RPC_URLS[chain]}")

    # -- Uniswap V3 --
    print("\n--- Uniswap V3 ---\n")
    print(f"  QuoterV2 (all chains): {UNISWAP_V3_QUOTER_V2}")
    print("  Fee tiers:")
    for raw, bps in sorted(UNISWAP_FEE_TIERS.items(), key=lambda x: x[1]):
        print(f"    {bps:>4} bps  ({float(bps) / 100:.2f}%)  [raw: {raw}]")
    print("  Pools (WETH/USDC):")
    for chain in sorted(UNISWAP_V3_POOLS):
        print(f"    {chain:<12}  {UNISWAP_V3_POOLS[chain]}")
    print("  Subgraphs:")
    for chain in sorted(UNISWAP_V3_SUBGRAPH):
        print(f"    {chain:<12}  {UNISWAP_V3_SUBGRAPH[chain]}")

    # -- SushiSwap V3 --
    print("\n--- SushiSwap V3 ---\n")
    print("  Quoter addresses:")
    for chain in sorted(SUSHI_V3_QUOTER):
        print(f"    {chain:<12}  {SUSHI_V3_QUOTER[chain]}")
    print("  Pools (WETH/USDC):")
    for chain in sorted(SUSHI_V3_POOLS):
        print(f"    {chain:<12}  {SUSHI_V3_POOLS[chain]}")
    print("  Subgraphs:")
    for chain in sorted(SUSHI_V3_SUBGRAPH):
        print(f"    {chain:<12}  {SUSHI_V3_SUBGRAPH[chain]}")

    # -- Balancer V2 --
    print("\n--- Balancer V2 ---\n")
    print(f"  Vault (all chains):    {BALANCER_VAULT}")
    print("  Pool IDs (WETH/USDC):")
    for chain in sorted(BALANCER_POOL_IDS):
        print(f"    {chain:<12}  {BALANCER_POOL_IDS[chain]}")
    print("  Subgraphs:")
    for chain in sorted(BALANCER_V2_SUBGRAPH):
        print(f"    {chain:<12}  {BALANCER_V2_SUBGRAPH[chain]}")

    # -- Token addresses --
    print("\n--- Token Addresses ---\n")
    for chain in sorted(CHAIN_TOKENS):
        tokens = CHAIN_TOKENS[chain]
        print(f"  {chain}:")
        for attr in ("weth", "usdc", "usdt", "wbtc"):
            addr = getattr(tokens, attr, None)
            if addr:
                print(f"    {attr.upper():<6}  {addr}")
        print()


def show_prices() -> None:
    """Fetch and print current DeFi Llama prices for all registered tokens."""

    print("=" * 80)
    print("  CURRENT PRICES (DeFi Llama)")
    print("=" * 80)
    print()

    # Build all coin IDs from the token registry.
    coin_ids: list[str] = []
    for chain, tokens in sorted(CHAIN_TOKENS.items()):
        for attr in ("weth", "usdc", "usdt", "wbtc"):
            addr = getattr(tokens, attr, None)
            if addr:
                coin_ids.append(defillama_coin_id(chain, addr))

    url = DEFILLAMA_URL + "/" + ",".join(coin_ids)
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json().get("coins", {})

    header = f"{'Chain':<12} {'Token':<8} {'Price (USD)':>14}  {'Confidence':>10}  Address"
    print(header)
    print("-" * len(header) + "-" * 20)

    for coin_id in sorted(data.keys()):
        entry = data[coin_id]
        chain, addr = coin_id.split(":", 1)
        symbol = entry.get("symbol", "?")
        price = entry.get("price", 0.0)
        confidence = entry.get("confidence", "n/a")
        print(f"{chain:<12} {symbol:<8} ${price:>13,.4f}  {confidence!s:>10}  {addr}")


def main() -> None:
    from core.env import load_env

    load_env()
    show_exchange_info()
    print()
    show_prices()


if __name__ == "__main__":
    main()
