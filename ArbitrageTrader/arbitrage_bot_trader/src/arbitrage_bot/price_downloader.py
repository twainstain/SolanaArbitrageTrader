"""Download historical pool price data from The Graph for training/testing.

Downloads hourly OHLC snapshots from Uniswap V3 / Sushi V3 subgraphs and
saves them as JSON files that can be replayed by HistoricalMarket.

Requires THEGRAPH_API_KEY environment variable.

Usage::

    export THEGRAPH_API_KEY=your_key_here

    # Last 7 days (default)
    PYTHONPATH=src python -m arbitrage_bot.price_downloader \\
        --dex uniswap_v3 --chain ethereum --days 7 --output data/uni_eth_7d.json

    # Last 90 days
    PYTHONPATH=src python -m arbitrage_bot.price_downloader \\
        --dex uniswap_v3 --chain ethereum --days 90 --output data/uni_eth_90d.json

    # Exact date range
    PYTHONPATH=src python -m arbitrage_bot.price_downloader \\
        --dex uniswap_v3 --chain ethereum \\
        --start 2026-01-01 --end 2026-03-31 --output data/uni_eth_q1.json

    # From a start date until now
    PYTHONPATH=src python -m arbitrage_bot.price_downloader \\
        --dex sushi_v3 --chain ethereum \\
        --start "2026-04-01 00:00" --output data/sushi_eth_apr.json

Output format (JSON)::

    {
      "dex": "uniswap_v3",
      "chain": "ethereum",
      "pool": "0x88e6...",
      "pair": "WETH/USDC",
      "downloaded_at": "2026-04-12T...",
      "snapshots": [
        {
          "timestamp": 1712880000,
          "open": 2195.12,
          "high": 2201.45,
          "low": 2190.30,
          "close": 2198.76,
          "token0Price": "2198.76",
          "token1Price": "0.000454",
          "liquidity": "12345678",
          "volumeUSD": "1234567.89"
        },
        ...
      ]
    }
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from arbitrage_bot.subgraphs import (
    MESSARI_POOL_HOURLY_QUERY,
    NATIVE_SCHEMA_SUBGRAPHS,
    POOL_HOUR_DATA_QUERY,
    SUSHI_V3_POOLS,
    SUSHI_V3_SUBGRAPH,
    THEGRAPH_GATEWAY,
    UNISWAP_V3_POOLS,
    UNISWAP_V3_SUBGRAPH,
)


class DownloadError(Exception):
    pass


def _resolve_subgraph_and_pool(dex: str, chain: str) -> tuple[str, str]:
    """Return (subgraph_id, pool_address) for the given dex + chain."""
    if dex == "uniswap_v3":
        subgraph_id = UNISWAP_V3_SUBGRAPH.get(chain)
        pool = UNISWAP_V3_POOLS.get(chain)
    elif dex == "sushi_v3":
        subgraph_id = SUSHI_V3_SUBGRAPH.get(chain)
        pool = SUSHI_V3_POOLS.get(chain)
    else:
        raise DownloadError(f"Unsupported dex: {dex}. Use 'uniswap_v3' or 'sushi_v3'.")

    if subgraph_id is None:
        raise DownloadError(f"No subgraph ID for {dex} on {chain}.")
    if pool is None:
        raise DownloadError(f"No WETH/USDC pool address for {dex} on {chain}.")

    return subgraph_id, pool


def _parse_date(value: str) -> int:
    """Parse a date string (YYYY-MM-DD or YYYY-MM-DD HH:MM) to a UTC Unix timestamp."""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    raise DownloadError(f"Cannot parse date: '{value}'. Use YYYY-MM-DD or YYYY-MM-DD HH:MM.")


def download_hourly_data(
    dex: str,
    chain: str,
    api_key: str,
    days: int | None = None,
    start: str | None = None,
    end: str | None = None,
    timeout: float = 15.0,
) -> dict:
    """Download hourly pool snapshots from a subgraph.

    Time range can be specified in three ways (checked in order):
      1. ``start`` + ``end``  — explicit date range (YYYY-MM-DD or YYYY-MM-DD HH:MM)
      2. ``start`` only       — from start until now
      3. ``days`` only        — last N days until now (default: 7)

    Returns the full JSON structure ready to be saved to a file.
    """
    subgraph_id, pool_addr = _resolve_subgraph_and_pool(dex, chain)
    url = THEGRAPH_GATEWAY.format(api_key=api_key, subgraph_id=subgraph_id)

    now = int(time.time())

    if start is not None and end is not None:
        start_time = _parse_date(start)
        end_time = _parse_date(end)
    elif start is not None:
        start_time = _parse_date(start)
        end_time = now
    else:
        d = days if days is not None else 7
        start_time = now - (d * 86400)
        end_time = now

    if start_time >= end_time:
        raise DownloadError(
            f"Start time ({datetime.utcfromtimestamp(start_time).isoformat()}) "
            f"must be before end time ({datetime.utcfromtimestamp(end_time).isoformat()})."
        )

    # Uniswap V3 Ethereum uses a custom "native" schema with poolHourDatas (OHLC).
    # All other subgraphs (Arbitrum, Base, Sushi) use the Messari standardized
    # schema with liquidityPoolHourlySnapshots (balances + tick, no OHLC).
    use_native = (dex, chain) in NATIVE_SCHEMA_SUBGRAPHS
    query_template = POOL_HOUR_DATA_QUERY if use_native else MESSARI_POOL_HOURLY_QUERY
    data_key = "poolHourDatas" if use_native else "liquidityPoolHourlySnapshots"

    all_snapshots: list[dict] = []
    skip = 0
    session = requests.Session()

    while True:
        payload = {
            "query": query_template,
            "variables": {
                "poolId": pool_addr,
                "startTime": start_time,
                "endTime": end_time,
                "skip": skip,
            },
        }

        try:
            resp = session.post(url, json=payload, timeout=timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise DownloadError(f"Subgraph request failed: {exc}") from exc

        result = resp.json()
        if "errors" in result:
            raise DownloadError(f"Subgraph errors: {result['errors']}")

        page = result.get("data", {}).get(data_key, [])
        if not page:
            break

        all_snapshots.extend(page)
        skip += len(page)

        # The Graph returns max 1000 per page.
        if len(page) < 1000:
            break

    # Normalize to a common format regardless of schema.
    if use_native:
        snapshots = _normalize_native(all_snapshots)
    else:
        snapshots = _normalize_messari(all_snapshots)

    return {
        "dex": dex,
        "chain": chain,
        "pool": pool_addr,
        "pair": "WETH/USDC",
        "schema": "native" if use_native else "messari",
        "start_time": start_time,
        "end_time": end_time,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_count": len(snapshots),
        "snapshots": snapshots,
    }


def _normalize_native(raw: list[dict]) -> list[dict]:
    """Normalize Uniswap V3 native ``poolHourDatas`` snapshots."""
    snapshots = []
    for s in raw:
        snapshots.append({
            "timestamp": int(s["periodStartUnix"]),
            "open": float(s.get("open", 0)),
            "high": float(s.get("high", 0)),
            "low": float(s.get("low", 0)),
            "close": float(s.get("close", 0)),
            "token0Price": s.get("token0Price", "0"),
            "token1Price": s.get("token1Price", "0"),
            "liquidity": s.get("liquidity", "0"),
            "volumeUSD": s.get("volumeUSD", "0"),
        })
    return snapshots


def _normalize_messari(raw: list[dict]) -> list[dict]:
    """Normalize Messari ``liquidityPoolHourlySnapshots`` into the same format.

    Derives the WETH/USDC mid-price from inputTokenBalancesUSD and
    inputTokenBalances.  The token with 18 decimals is WETH.
    """
    snapshots = []
    for s in raw:
        price = _derive_messari_price(s)
        snapshots.append({
            "timestamp": int(s["timestamp"]),
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "token0Price": "0",
            "token1Price": "0",
            "liquidity": "0",
            "volumeUSD": s.get("hourlyVolumeUSD", "0"),
        })
    return snapshots


def _derive_messari_price(snapshot: dict) -> float:
    """Derive WETH price in USDC from a Messari hourly snapshot.

    Strategy (in order):
      1. Use inputTokenBalancesUSD / balance  (if USD values are populated)
      2. Use the pool tick: price = 1.0001^tick, adjusted for decimals
      3. Use raw balance ratio as last resort
    """
    tokens = snapshot.get("pool", {}).get("inputTokens", [])
    balances_usd = snapshot.get("inputTokenBalancesUSD", [])
    balances_raw = snapshot.get("inputTokenBalances", [])

    # Strategy 1: USD values from indexer.
    for i, token in enumerate(tokens):
        sym = token.get("symbol", "").upper()
        decimals = int(token.get("decimals", 18))
        if sym in ("WETH", "ETH") and i < len(balances_usd) and i < len(balances_raw):
            usd_value = float(balances_usd[i])
            raw_balance = int(balances_raw[i])
            if usd_value > 0 and raw_balance > 0:
                return usd_value / (raw_balance / (10 ** decimals))

    # Strategy 2: Derive from tick (Uniswap V3 / concentrated liquidity).
    tick_str = snapshot.get("tick")
    if tick_str is not None and tokens:
        tick = int(tick_str)
        # Identify token ordering: token0 and token1.
        dec0 = int(tokens[0].get("decimals", 18))
        dec1 = int(tokens[1].get("decimals", 18)) if len(tokens) > 1 else 6
        sym0 = tokens[0].get("symbol", "").upper()

        # raw_price = 1.0001^tick gives token1/token0 in raw units.
        raw_price = 1.0001 ** tick
        # Adjust for decimals: price = raw_price * 10^(dec0 - dec1)
        adjusted_price = raw_price * (10 ** (dec0 - dec1))

        if sym0 in ("WETH", "ETH"):
            # token0 is WETH, so adjusted_price = USDC per WETH
            return adjusted_price
        else:
            # token0 is USDC, so adjusted_price = WETH per USDC — invert
            return 1.0 / adjusted_price if adjusted_price > 0 else 0.0

    # Strategy 3: Raw balance ratio (rough for concentrated liquidity).
    weth_idx = None
    usdc_idx = None
    for i, token in enumerate(tokens):
        sym = token.get("symbol", "").upper()
        if sym in ("WETH", "ETH"):
            weth_idx = i
        elif sym in ("USDC", "USDT"):
            usdc_idx = i

    if weth_idx is not None and usdc_idx is not None:
        weth_dec = int(tokens[weth_idx].get("decimals", 18))
        usdc_dec = int(tokens[usdc_idx].get("decimals", 6))
        weth_bal = int(balances_raw[weth_idx]) / (10 ** weth_dec)
        usdc_bal = int(balances_raw[usdc_idx]) / (10 ** usdc_dec)
        if weth_bal > 0:
            return usdc_bal / weth_bal

    raise DownloadError("Cannot derive WETH price from Messari snapshot.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download historical hourly pool prices from The Graph subgraphs."
    )
    parser.add_argument(
        "--dex",
        required=True,
        choices=["uniswap_v3", "sushi_v3"],
        help="DEX to query.",
    )
    parser.add_argument(
        "--chain",
        required=True,
        choices=["ethereum", "base", "arbitrum"],
        help="Chain to query.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Number of days of history to download (default: 7). Ignored if --start is set.",
    )
    parser.add_argument(
        "--start",
        default=None,
        help="Start date: YYYY-MM-DD or 'YYYY-MM-DD HH:MM' (UTC). If omitted, uses --days.",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="End date: YYYY-MM-DD or 'YYYY-MM-DD HH:MM' (UTC). If omitted, defaults to now.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output JSON file path (e.g. data/uni_eth_7d.json).",
    )
    return parser


def main() -> None:
    from arbitrage_bot.env import load_env

    load_env()
    args = build_parser().parse_args()

    api_key = os.environ.get("THEGRAPH_API_KEY", "")
    if not api_key:
        print("ERROR: Set THEGRAPH_API_KEY in .env or as an environment variable.")
        print("Get a free key at https://thegraph.com/studio/apikeys/")
        return

    if args.start:
        label = f"{args.start} to {args.end or 'now'}"
    else:
        label = f"last {args.days or 7} days"
    print(f"Downloading {args.dex} data on {args.chain} ({label})...")

    data = download_hourly_data(
        dex=args.dex,
        chain=args.chain,
        api_key=api_key,
        days=args.days,
        start=args.start,
        end=args.end,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    print(f"Downloaded {data['snapshot_count']} hourly snapshots -> {output_path}")


if __name__ == "__main__":
    main()
