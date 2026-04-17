"""SubgraphMarket — fetches per-DEX pool prices from The Graph subgraphs.

Each DEX entry queries its subgraph for the WETH/USDC pool's current
token0Price / token1Price.  This gives true per-DEX prices on the same chain.

Requires THEGRAPH_API_KEY environment variable (free at thegraph.com/studio).

Usage::

    export THEGRAPH_API_KEY=your_key_here
    PYTHONPATH=src python -m main \\
        --config config/subgraph_config.json --subgraph --dry-run --no-sleep
"""

from __future__ import annotations

import os
from decimal import Decimal

import requests

from core.config import BotConfig
from core.models import BPS_DIVISOR, ZERO, MarketQuote
from market.subgraphs import (
    BALANCER_POOL_QUERY,
    BALANCER_V2_SUBGRAPH,
    POOL_CURRENT_QUERY,
    SUSHI_V3_POOLS,
    SUSHI_V3_SUBGRAPH,
    THEGRAPH_GATEWAY,
    UNISWAP_V3_POOLS,
    UNISWAP_V3_SUBGRAPH,
)

D = Decimal
TWO = D("2")


class SubgraphMarketError(Exception):
    """Raised when a subgraph query fails."""


class SubgraphMarket:
    """Fetch per-DEX pool prices via The Graph subgraphs."""

    def __init__(self, config: BotConfig, api_key: str | None = None, timeout: float = 15.0) -> None:
        self.config = config
        self.api_key = api_key if api_key is not None else os.environ.get("THEGRAPH_API_KEY", "")
        self.timeout = timeout
        self._session = requests.Session()

        if not self.api_key:
            raise SubgraphMarketError(
                "THEGRAPH_API_KEY environment variable is required. "
                "Get a free key at https://thegraph.com/studio/apikeys/"
            )

        for dex in config.dexes:
            if dex.chain is None:
                raise SubgraphMarketError(f"DEX '{dex.name}': subgraph mode requires a 'chain' field.")
            if dex.dex_type is None:
                raise SubgraphMarketError(f"DEX '{dex.name}': subgraph mode requires a 'dex_type' field.")

    def get_quotes(self) -> list[MarketQuote]:
        quotes: list[MarketQuote] = []
        for dex in self.config.dexes:
            chain = dex.chain
            dex_type = dex.dex_type
            assert chain is not None and dex_type is not None

            if dex_type in ("uniswap_v3", "sushi_v3"):
                mid = self._query_uniswap_style(chain, dex_type)
            elif dex_type == "balancer_v2":
                mid = self._query_balancer(chain)
            else:
                raise SubgraphMarketError(f"Unsupported dex_type: {dex_type}")

            half_spread = mid * (dex.fee_bps / BPS_DIVISOR / TWO)
            quotes.append(
                MarketQuote(
                    dex=dex.name,
                    pair=self.config.pair,
                    buy_price=mid + half_spread,
                    sell_price=mid - half_spread,
                    fee_bps=dex.fee_bps,
                )
            )
        return quotes

    # ------------------------------------------------------------------
    # Uniswap V3 / SushiSwap V3
    # ------------------------------------------------------------------

    def _query_uniswap_style(self, chain: str, dex_type: str) -> Decimal:
        if dex_type == "uniswap_v3":
            subgraph_id = UNISWAP_V3_SUBGRAPH.get(chain)
            pool_addr = UNISWAP_V3_POOLS.get(chain)
        else:
            subgraph_id = SUSHI_V3_SUBGRAPH.get(chain)
            pool_addr = SUSHI_V3_POOLS.get(chain)

        if subgraph_id is None:
            raise SubgraphMarketError(f"No {dex_type} subgraph for chain '{chain}'.")
        if pool_addr is None:
            raise SubgraphMarketError(f"No {dex_type} WETH/USDC pool address for chain '{chain}'.")

        url = THEGRAPH_GATEWAY.format(api_key=self.api_key, subgraph_id=subgraph_id)
        payload = {
            "query": POOL_CURRENT_QUERY,
            "variables": {"poolId": pool_addr},
        }

        data = self._post(url, payload)
        pool = data.get("data", {}).get("pool")
        if pool is None:
            raise SubgraphMarketError(f"Pool {pool_addr} not found in {dex_type} subgraph on {chain}.")

        return self._extract_weth_usdc_price(pool)

    # ------------------------------------------------------------------
    # Balancer V2
    # ------------------------------------------------------------------

    def _query_balancer(self, chain: str) -> Decimal:
        subgraph_id = BALANCER_V2_SUBGRAPH.get(chain)
        if subgraph_id is None:
            raise SubgraphMarketError(f"No Balancer V2 subgraph for chain '{chain}'.")

        from core.contracts import BALANCER_POOL_IDS

        pool_id = BALANCER_POOL_IDS.get(chain)
        if pool_id is None:
            raise SubgraphMarketError(f"No Balancer pool ID configured for chain '{chain}'.")

        url = THEGRAPH_GATEWAY.format(api_key=self.api_key, subgraph_id=subgraph_id)
        payload = {
            "query": BALANCER_POOL_QUERY,
            "variables": {"poolId": pool_id},
        }

        data = self._post(url, payload)
        pool = data.get("data", {}).get("pool")
        if pool is None:
            raise SubgraphMarketError(f"Balancer pool {pool_id} not found on {chain}.")

        return self._balancer_spot_price(pool)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _post(self, url: str, payload: dict) -> dict:
        try:
            resp = self._session.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise SubgraphMarketError(f"Subgraph request failed: {exc}") from exc

        result = resp.json()
        if "errors" in result:
            raise SubgraphMarketError(f"Subgraph returned errors: {result['errors']}")
        return result

    @staticmethod
    def _extract_weth_usdc_price(pool: dict) -> Decimal:
        """Extract WETH/USDC price from a Uniswap V3 style pool response.

        The subgraph returns token0Price (price of token0 in token1) and
        token1Price (price of token1 in token0).  We need to figure out
        which token is WETH and return its price in USDC.
        """
        t0_symbol = pool["token0"]["symbol"].upper()
        t1_symbol = pool["token1"]["symbol"].upper()
        t0_price = D(str(pool["token0Price"]))
        t1_price = D(str(pool["token1Price"]))

        # token0Price = how much token1 you get for 1 token0
        # token1Price = how much token0 you get for 1 token1
        if t1_symbol in ("WETH", "ETH"):
            # token1 is WETH, token0 is USDC
            # token1Price = WETH price in USDC
            return t1_price
        elif t0_symbol in ("WETH", "ETH"):
            # token0 is WETH, token1 is USDC
            # token0Price = WETH price in USDC
            return t0_price
        else:
            raise SubgraphMarketError(
                f"Cannot identify WETH in pool tokens: {t0_symbol}, {t1_symbol}"
            )

    @staticmethod
    def _balancer_spot_price(pool: dict) -> Decimal:
        """Derive WETH/USDC spot price from Balancer pool token balances and weights."""
        weth_token = None
        usdc_token = None
        for token in pool["tokens"]:
            sym = token["symbol"].upper()
            if sym in ("WETH", "ETH"):
                weth_token = token
            elif sym in ("USDC",):
                usdc_token = token

        if weth_token is None or usdc_token is None:
            raise SubgraphMarketError(
                f"Cannot find WETH and USDC in Balancer pool tokens: "
                f"{[t['symbol'] for t in pool['tokens']]}"
            )

        weth_balance = D(str(weth_token["balance"]))
        usdc_balance = D(str(usdc_token["balance"]))
        weth_weight = D(str(weth_token["weight"]))
        usdc_weight = D(str(usdc_token["weight"]))

        if weth_balance == ZERO or weth_weight == ZERO:
            raise SubgraphMarketError("Balancer pool has zero WETH balance or weight.")
        if usdc_balance == ZERO or usdc_weight == ZERO:
            raise SubgraphMarketError("Balancer pool has zero USDC balance or weight.")

        # Balancer weighted pool spot price (before swap fee):
        #   price(A→B) = (balance_B / weight_B) / (balance_A / weight_A)
        # For a 50/50 pool this simplifies to balance_USDC / balance_WETH.
        return (usdc_balance / usdc_weight) / (weth_balance / weth_weight)
