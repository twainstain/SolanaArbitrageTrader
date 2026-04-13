"""LiveMarket — fetches real token prices from the DeFi Llama API.

DeFi Llama returns a single aggregated price per token per chain, so each
chain acts as a separate venue.  This gives real (small) cross-chain price
discrepancies that the arbitrage strategy can evaluate.

Generates quotes for the primary pair and all extra_pairs from the config.
When pairs carry token addresses (from DexScreener discovery), ANY token
can be priced — not just the hardcoded registry.

Endpoint used:
    GET https://coins.llama.fi/prices/current/{coins}

No API key required.  Response is cached by CloudFront (~5 min TTL).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from arbitrage_bot.config import BotConfig, PairConfig
from arbitrage_bot.log import get_logger
from arbitrage_bot.models import MarketQuote
from arbitrage_bot.tokens import CHAIN_TOKENS, TokenAddresses, defillama_coin_id

logger = get_logger(__name__)

DEFILLAMA_BASE_URL = "https://coins.llama.fi"

# Maps asset symbol to the attribute name on TokenAddresses (hardcoded fallback).
BASE_ASSET_MAP = {"WETH": "weth", "ETH": "weth", "WBTC": "wbtc"}
QUOTE_ASSET_MAP = {"USDC": "usdc", "USDT": "usdt"}


class LiveMarketError(Exception):
    """Raised when the DeFi Llama API call fails or returns unexpected data."""


@dataclass(frozen=True)
class _PairDef:
    pair_name: str
    base_asset: str
    quote_asset: str
    # Token addresses from DexScreener discovery (if available).
    base_address: str | None = None
    quote_address: str | None = None
    # Chain where these addresses are valid.
    pair_chain: str | None = None


class LiveMarket:
    """Fetch real prices from DeFi Llama across multiple chains.

    Each chain is treated as a venue.  Produces quotes for the primary pair
    and all extra_pairs/discovered_pairs in one API call.
    """

    def __init__(
        self,
        config: BotConfig,
        pairs: list[PairConfig] | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.config = config
        self.timeout = timeout
        self._session = requests.Session()

        # Map each configured DEX name to its chain.
        self._venue_chains: dict[str, str] = {}
        for dex in config.dexes:
            chain = dex.chain
            if chain is None:
                raise LiveMarketError(
                    f"DEX '{dex.name}' has no chain configured — "
                    f"live mode requires a chain for every DEX."
                )
            if chain not in CHAIN_TOKENS:
                raise LiveMarketError(
                    f"Chain '{chain}' for DEX '{dex.name}' is not in the "
                    f"token registry.  Supported chains: {sorted(CHAIN_TOKENS)}."
                )
            self._venue_chains[dex.name] = chain

        # Build pair definitions from either:
        # 1. Directly passed pairs (from --discover)
        # 2. Config primary pair + extra_pairs
        self._pairs: list[_PairDef] = []

        if pairs is not None:
            for p in pairs:
                self._pairs.append(_PairDef(
                    p.pair, p.base_asset, p.quote_asset,
                    p.base_address, p.quote_address, p.chain,
                ))
        else:
            self._pairs.append(_PairDef(config.pair, config.base_asset, config.quote_asset))
            if config.extra_pairs:
                for ep in config.extra_pairs:
                    self._pairs.append(_PairDef(
                        ep.pair, ep.base_asset, ep.quote_asset,
                        getattr(ep, "base_address", None),
                        getattr(ep, "quote_address", None),
                        getattr(ep, "chain", None),
                    ))

    def get_quotes(self) -> list[MarketQuote]:
        """Fetch current prices and return MarketQuotes for all pairs x venues."""
        # Step 1: Collect all token coin IDs we need.
        all_coin_ids: set[str] = set()
        # Track which (pair, chain) -> (base_coin_id, quote_coin_id) for lookup.
        pair_chain_ids: dict[tuple[str, str], tuple[str, str]] = {}

        for pair_def in self._pairs:
            for chain in set(self._venue_chains.values()):
                base_id = self._resolve_coin_id(pair_def.base_asset, pair_def.base_address, pair_def.pair_chain, chain, is_base=True)
                quote_id = self._resolve_coin_id(pair_def.quote_asset, pair_def.quote_address, pair_def.pair_chain, chain, is_base=False)
                if base_id and quote_id:
                    all_coin_ids.add(base_id)
                    all_coin_ids.add(quote_id)
                    pair_chain_ids[(pair_def.pair_name, chain)] = (base_id, quote_id)

        if not all_coin_ids:
            return []

        # Step 2: Single API call for all tokens.
        price_data = self._fetch_all_prices(sorted(all_coin_ids))

        # Step 3: Build quotes.
        now = time.time()
        quotes: list[MarketQuote] = []
        for pair_def in self._pairs:
            for dex in self.config.dexes:
                chain = self._venue_chains[dex.name]
                key = (pair_def.pair_name, chain)

                if key not in pair_chain_ids:
                    continue

                base_id, quote_id = pair_chain_ids[key]
                base_entry = price_data.get(base_id)
                quote_entry = price_data.get(quote_id)
                if base_entry is None or quote_entry is None:
                    continue

                base_usd = float(base_entry["price"])
                quote_usd = float(quote_entry["price"])
                if quote_usd == 0:
                    continue

                # Mid-price: how many quote tokens per 1 base token.
                # DeFi Llama returns USD prices, so base_USD / quote_USD ≈ base/quote.
                mid_price = base_usd / quote_usd
                half_spread = mid_price * (dex.fee_bps / 10_000.0 / 2)

                quotes.append(
                    MarketQuote(
                        dex=dex.name,
                        pair=pair_def.pair_name,
                        buy_price=mid_price + half_spread,
                        sell_price=mid_price - half_spread,
                        fee_bps=dex.fee_bps,
                        quote_timestamp=now,
                    )
                )
        return quotes

    def _resolve_coin_id(
        self,
        symbol: str,
        discovered_address: str | None,
        discovered_chain: str | None,
        target_chain: str,
        is_base: bool,
    ) -> str | None:
        """Resolve a token to a DeFi Llama coin ID.

        Priority:
          1. Discovered address from DexScreener (if chain matches)
          2. Hardcoded registry in tokens.py
        """
        # Try discovered address first (from DexScreener).
        if discovered_address and discovered_chain:
            if discovered_chain == target_chain:
                return defillama_coin_id(target_chain, discovered_address)

        # Fall back to hardcoded registry.
        if target_chain in CHAIN_TOKENS:
            tokens = CHAIN_TOKENS[target_chain]
            mapping = BASE_ASSET_MAP if is_base else QUOTE_ASSET_MAP
            addr = _resolve_address(tokens, symbol, mapping)
            if addr:
                return defillama_coin_id(target_chain, addr)

        return None

    def _fetch_all_prices(self, coin_ids: list[str]) -> dict:
        """Fetch prices for all coin IDs in a single DeFi Llama call."""
        if not coin_ids:
            return {}
        url = f"{DEFILLAMA_BASE_URL}/prices/current/{','.join(coin_ids)}"
        try:
            resp = self._session.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LiveMarketError(f"DeFi Llama request failed: {exc}") from exc
        return resp.json().get("coins", {})


def _resolve_address(
    tokens: TokenAddresses, asset: str, mapping: dict[str, str]
) -> str | None:
    """Resolve an asset symbol to a token address, or None if unavailable."""
    attr = mapping.get(asset.upper())
    if attr is None:
        return None
    return getattr(tokens, attr, None)
