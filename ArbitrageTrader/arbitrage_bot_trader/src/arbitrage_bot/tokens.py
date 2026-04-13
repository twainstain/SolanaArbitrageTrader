"""Token address registry for supported chains.

DeFi Llama uses the format ``{chain}:{address}`` to identify tokens.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenAddresses:
    weth: str
    usdc: str
    usdt: str | None = None
    wbtc: str | None = None


# Canonical contract addresses per chain.
CHAIN_TOKENS: dict[str, TokenAddresses] = {
    "ethereum": TokenAddresses(
        weth="0xC02aaA39b223FE8D0A0e5c4F27eAD9083C756Cc2",
        usdc="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        usdt="0xdAC17F958D2ee523a2206206994597C13D831ec7",
        wbtc="0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
    ),
    "base": TokenAddresses(
        weth="0x4200000000000000000000000000000000000006",
        usdc="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    ),
    "arbitrum": TokenAddresses(
        weth="0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        usdc="0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        usdt="0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
        wbtc="0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",
    ),
    # BSC (BNB Chain) — PancakeSwap's primary chain.
    "bsc": TokenAddresses(
        weth="0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
        usdc="0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        usdt="0x55d398326f99059fF775485246999027B3197955",
        wbtc="0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c",
    ),
    "polygon": TokenAddresses(
        weth="0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
        usdc="0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
        usdt="0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
        wbtc="0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6",
    ),
    "optimism": TokenAddresses(
        weth="0x4200000000000000000000000000000000000006",
        usdc="0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
        usdt="0x94b008aA00579c1307B0EF2c499aD98a8ce58e58",
        wbtc="0x68f180fcCe6836688e9084f035309E29Bf0A2095",
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


def defillama_coin_id(chain: str, address: str) -> str:
    """Return the ``chain:address`` identifier used by DeFi Llama."""
    return f"{chain}:{address}"
