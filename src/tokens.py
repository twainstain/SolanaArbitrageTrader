"""Token address registry for supported chains.

This module is the single source of truth for canonical ERC-20 contract
addresses used throughout the arbitrage bot.  Every market source (LiveMarket,
OnChainMarket, SubgraphMarket) resolves token addresses through the
``CHAIN_TOKENS`` registry defined here.

DeFi Llama uses the format ``{chain}:{address}`` to identify tokens, which is
why ``defillama_coin_id()`` exists at the bottom of this file.

Chain-specific notes
--------------------
* **BSC (BNB Chain)** -- The primary wrapped native token is WBNB, not WETH.
  The ``TokenAddresses`` dataclass carries an optional ``wbnb`` field
  specifically for this chain.  WETH *does* exist on BSC (bridged), so both
  fields are populated.  PancakeSwap pairs are typically denominated in
  WBNB/USDT rather than WETH/USDC.

* **Polygon USDC migration** -- In 2023 Polygon migrated from the bridged
  ``USDC.e`` (0x2791Bce8....) to native USDC issued by Circle
  (0x3c499c54...).  Uniswap V3 liquidity has largely moved to native USDC, so
  that is the address stored here.  If you need to interact with legacy
  USDC.e pools, add a separate field or override at the config level.

Adding a new chain
------------------
1. Add a new entry to ``CHAIN_TOKENS`` with at least ``weth`` and ``usdc``.
2. Add a public RPC URL in ``contracts.py`` → ``PUBLIC_RPC_URLS``.
3. Add DEX quoter addresses in ``contracts.py`` if on-chain quoting is needed.
4. If the chain uses a non-standard native wrapped token (like BSC's WBNB),
   add an optional field to ``TokenAddresses`` and a corresponding entry in
   ``SYMBOL_TO_ATTR``.

SYMBOL_TO_ATTR
--------------
Maps user-facing ticker symbols (e.g. "ETH", "BTC") to the attribute name on
``TokenAddresses``.  This lets callers pass familiar symbols without knowing
whether the underlying field is ``weth`` or ``wbtc``.  Both the wrapped and
unwrapped names map to the same attribute (e.g. "ETH" -> "weth",
"BTC" -> "wbtc") because on-chain we always deal with the wrapped variant.

token_decimals()
----------------
Returns the *standard* decimal precision for well-known token symbols.  This
is a convenience used when converting between raw on-chain uint256 amounts and
human-readable Decimal values.  The function hard-codes the widely adopted
defaults (USDC/USDT = 6, WBTC = 8, everything else = 18) rather than making
an on-chain ``decimals()`` call, because these values never change for the
tokens we trade and an RPC round-trip would add unnecessary latency.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenAddresses:
    weth: str
    usdc: str
    usdt: str | None = None
    wbtc: str | None = None
    wbnb: str | None = None  # BSC native wrapped token
    usdc_e: str | None = None  # Bridged USDC (USDC.e / USDbC)
    arb: str | None = None
    op: str | None = None
    link: str | None = None
    dai: str | None = None
    uni: str | None = None
    aave: str | None = None
    crv: str | None = None
    gmx: str | None = None


# Canonical contract addresses per chain.
CHAIN_TOKENS: dict[str, TokenAddresses] = {
    "ethereum": TokenAddresses(
        weth="0xC02aaA39b223FE8D0A0e5c4F27eAD9083C756Cc2",
        usdc="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        usdt="0xdAC17F958D2ee523a2206206994597C13D831ec7",
        wbtc="0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
        link="0x514910771AF9Ca656af840dff83E8264EcF986CA",
        dai="0x6B175474E89094C44Da98b954EedeAC495271d0F",
        uni="0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
        aave="0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9",
        crv="0xD533a949740bb3306d119CC777fa900bA034cd52",
    ),
    "base": TokenAddresses(
        weth="0x4200000000000000000000000000000000000006",
        usdc="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        usdt="0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",
        usdc_e="0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA",  # USDbC (bridged)
        dai="0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
    ),
    "arbitrum": TokenAddresses(
        weth="0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        usdc="0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        usdt="0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
        wbtc="0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",
        usdc_e="0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8",
        arb="0x912CE59144191C1204E64559FE8253a0e49E6548",
        link="0xf97f4df75117a78c1A5a0DBb814Af92458539FB4",
        gmx="0xfc5A1A6EB076a2C7aD06eD22C90d7E710E35ad0a",
        dai="0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
        uni="0xFa7F8980b0f1E64A2062791cc3b0871572f1F7f0",
    ),
    # BSC (BNB Chain) — PancakeSwap's primary chain.
    # Primary pairs are WBNB/USDT, not WETH/USDC.
    "bsc": TokenAddresses(
        weth="0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
        usdc="0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        usdt="0x55d398326f99059fF775485246999027B3197955",
        wbtc="0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c",
        wbnb="0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
    ),
    "polygon": TokenAddresses(
        weth="0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
        # Native USDC — Polygon migrated from USDC.e to native in 2023.
        # Uniswap V3 pools have moved to native USDC.
        usdc="0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
        usdt="0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
        wbtc="0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6",
    ),
    "optimism": TokenAddresses(
        weth="0x4200000000000000000000000000000000000006",
        usdc="0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
        usdc_e="0x7F5c764cBc14f9669B88837ca1490cCa17c31607",
        usdt="0x94b008aA00579c1307B0EF2c499aD98a8ce58e58",
        wbtc="0x68f180fcCe6836688e9084f035309E29Bf0A2095",
        op="0x4200000000000000000000000000000000000042",
        link="0x350a791Bfc2C21F9Ed5d10980Dad2e2638ffa7f6",
        dai="0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
    ),
    "avax": TokenAddresses(
        weth="0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB",
        usdc="0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
        usdt="0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7",
        wbtc="0x50b7545627a5162F82A992c33b87aDc75187B218",
    ),
    "fantom": TokenAddresses(
        weth="0x74b23882a30290451A17c44f4F05243b6b58C76d",
        usdc="0x04068DA6C83AFCFA0e13ba15A6696662335D5B75",
        usdt="0x049d68029688eAbF473097a2fC38ef61633A3C7A",
    ),
    "gnosis": TokenAddresses(
        weth="0x6A023CCd1ff6F2045C3309768eAd9E68F978f6e1",
        usdc="0xDDAfbb505ad214D7b80b1f830fcCc89B60fb7A83",
        usdt="0x4ECaBa5870353805a9F068101A40E0f32ed605C6",
    ),
    "linea": TokenAddresses(
        weth="0xe5D7C2a44FfDDf6b295A15c148167daaAf5Cf34f",
        usdc="0x176211869cA2b568f2A7D4EE941E073a821EE1ff",
    ),
    "scroll": TokenAddresses(
        weth="0x5300000000000000000000000000000000000004",
        usdc="0x06eFdBFf2a14a7c8E15944D1F4A48F9F95F663A4",
    ),
    "zksync": TokenAddresses(
        weth="0x5AEa5775959fBC2557Cc8789bC1bf90A239D9a91",
        usdc="0x1d17CBcF0D6D143135aE902365D2E5e2A16538D4",
    ),
}


# Map common asset symbols to their attribute name on TokenAddresses.
SYMBOL_TO_ATTR: dict[str, str] = {
    "WETH": "weth", "ETH": "weth",
    "USDC": "usdc",
    "USDT": "usdt",
    "WBTC": "wbtc", "BTC": "wbtc",
    "WBNB": "wbnb", "BNB": "wbnb",
    "ARB": "arb",
    "OP": "op",
    "LINK": "link",
    "DAI": "dai",
    "UNI": "uni",
    "AAVE": "aave",
    "CRV": "crv",
    "GMX": "gmx",
    "USDC.E": "usdc_e",
    "USDBC": "usdc_e",
}

# ---------------------------------------------------------------------------
# Dynamic token registry — stores addresses discovered at runtime
# (e.g. from DexScreener) that aren't in the static CHAIN_TOKENS above.
# ---------------------------------------------------------------------------
import logging
import threading

_logger = logging.getLogger(__name__)
_dynamic_tokens: dict[str, str] = {}  # key: "chain:SYMBOL" → address
_dynamic_lock = threading.Lock()
_unresolved: dict[str, int] = {}  # key: "chain:SYMBOL" → miss count


def register_token(chain: str, symbol: str, address: str) -> None:
    """Register a dynamically discovered token address.

    Called by pair discovery when DexScreener returns an address
    for a token not in the static registry.
    """
    key = f"{chain}:{symbol.upper()}"
    with _dynamic_lock:
        if key not in _dynamic_tokens:
            _dynamic_tokens[key] = address
            _logger.info("Registered dynamic token: %s = %s", key, address)


def resolve_token_address(chain: str, symbol: str) -> str | None:
    """Resolve an asset symbol to its on-chain address.

    Checks in order:
    1. Static CHAIN_TOKENS registry (hardcoded, fastest)
    2. Dynamic registry (discovered at runtime)
    3. Returns None and logs to unresolved list
    """
    sym = symbol.upper()

    # 1. Static registry
    tokens = CHAIN_TOKENS.get(chain)
    if tokens is not None:
        attr = SYMBOL_TO_ATTR.get(sym)
        if attr is not None:
            addr = getattr(tokens, attr, None)
            if addr is not None:
                return addr

    # 2. Dynamic registry
    key = f"{chain}:{sym}"
    with _dynamic_lock:
        addr = _dynamic_tokens.get(key)
        if addr is not None:
            return addr

        # 3. Track unresolved for debugging
        _unresolved[key] = _unresolved.get(key, 0) + 1
        if _unresolved[key] <= 3:  # log first 3 misses
            _logger.warning("Unresolved token: %s on %s (miss #%d)",
                          sym, chain, _unresolved[key])
    return None


def get_unresolved_tokens() -> dict:
    """Return tokens that were requested but couldn't be resolved."""
    with _dynamic_lock:
        return dict(_unresolved)


def get_dynamic_tokens() -> dict:
    """Return all dynamically registered tokens."""
    with _dynamic_lock:
        return dict(_dynamic_tokens)


def bridged_usdc_address(chain: str) -> str | None:
    """Return the bridged USDC (USDC.e / USDbC) address for a chain, or None."""
    tokens = CHAIN_TOKENS.get(chain)
    if tokens is not None:
        return tokens.usdc_e
    return None


def token_decimals(symbol: str) -> int:
    """Return the standard decimals for a well-known token symbol."""
    sym = symbol.upper()
    if sym in ("USDC", "USDT"):
        return 6
    if sym in ("WBTC", "BTC"):
        return 8
    # WETH, ETH, and most ERC-20s default to 18.
    return 18


def defillama_coin_id(chain: str, address: str) -> str:
    """Return the ``chain:address`` identifier used by DeFi Llama."""
    return f"{chain}:{address}"
