"""OnChainMarket — fetches real per-DEX quotes via web3.py RPC calls.

Queries Uniswap V3 QuoterV2, SushiSwap V3 QuoterV2, and (optionally)
Balancer V2 Vault ``queryBatchSwap`` on the configured chain.  Uses public
RPC endpoints by default.

Each DEX config entry must specify ``chain`` and ``dex_type`` (one of
``uniswap_v3``, ``sushi_v3``, ``pancakeswap_v3``, ``balancer_v2``).

Usage::

    PYTHONPATH=src python -m arbitrage_bot.main \\
        --config config/onchain_config.json --onchain --dry-run --no-sleep
"""

from __future__ import annotations

from web3 import Web3

from arbitrage_bot.config import BotConfig
from arbitrage_bot.contracts import (
    BALANCER_POOL_IDS,
    BALANCER_VAULT,
    BALANCER_VAULT_ABI,
    PANCAKE_V3_QUOTER,
    PANCAKE_V3_QUOTER_ABI,
    PUBLIC_RPC_URLS,
    SUSHI_V3_QUOTER,
    SUSHI_V3_QUOTER_ABI,
    UNISWAP_FEE_TIERS,
    UNISWAP_V3_QUOTER_ABI,
    UNISWAP_V3_QUOTER_V2,
)
from arbitrage_bot.models import MarketQuote
from arbitrage_bot.tokens import CHAIN_TOKENS

SUPPORTED_DEX_TYPES = ("uniswap_v3", "sushi_v3", "pancakeswap_v3", "balancer_v2")

# Token decimals used when converting raw uint256 amounts.
WETH_DECIMALS = 18
USDC_DECIMALS = 6


class OnChainMarketError(Exception):
    """Raised when an on-chain query fails."""


class OnChainMarket:
    """Query DEX smart contracts for real per-DEX price quotes."""

    def __init__(
        self,
        config: BotConfig,
        rpc_overrides: dict[str, str] | None = None,
    ) -> None:
        self.config = config
        self._rpc_overrides = rpc_overrides or {}

        # Pre-build web3 instances keyed by chain.
        self._w3: dict[str, Web3] = {}
        for dex in config.dexes:
            chain = dex.chain
            if chain is None:
                raise OnChainMarketError(
                    f"DEX '{dex.name}': on-chain mode requires a 'chain' field."
                )
            if chain not in PUBLIC_RPC_URLS and chain not in self._rpc_overrides:
                raise OnChainMarketError(
                    f"No RPC URL for chain '{chain}'.  "
                    f"Supported: {sorted(PUBLIC_RPC_URLS)}."
                )
            dex_type = dex.dex_type
            if dex_type is None:
                raise OnChainMarketError(
                    f"DEX '{dex.name}': on-chain mode requires a 'dex_type' field."
                )
            if dex_type not in SUPPORTED_DEX_TYPES:
                raise OnChainMarketError(
                    f"DEX '{dex.name}': unsupported dex_type '{dex_type}'.  "
                    f"Supported: {SUPPORTED_DEX_TYPES}."
                )
            if chain not in self._w3:
                rpc_url = self._rpc_overrides.get(chain, PUBLIC_RPC_URLS[chain])
                self._w3[chain] = Web3(Web3.HTTPProvider(rpc_url))

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_quotes(self) -> list[MarketQuote]:
        quotes: list[MarketQuote] = []
        for dex in self.config.dexes:
            chain = dex.chain
            assert chain is not None
            dex_type = dex.dex_type
            assert dex_type is not None

            tokens = CHAIN_TOKENS[chain]
            weth_addr = tokens.weth
            usdc_addr = tokens.usdc

            try:
                if dex_type == "uniswap_v3":
                    mid = self._quote_uniswap_v3(chain, weth_addr, usdc_addr)
                elif dex_type == "sushi_v3":
                    mid = self._quote_sushi_v3(chain, weth_addr, usdc_addr)
                elif dex_type == "pancakeswap_v3":
                    mid = self._quote_pancakeswap_v3(chain, weth_addr, usdc_addr)
                elif dex_type == "balancer_v2":
                    mid = self._quote_balancer_v2(chain, weth_addr, usdc_addr)
                else:
                    raise OnChainMarketError(f"Unknown dex_type: {dex_type}")
            except OnChainMarketError:
                raise
            except Exception as exc:
                raise OnChainMarketError(
                    f"RPC call failed for {dex.name} on {chain}: {exc}"
                ) from exc

            # Model bid-ask spread: the DEX fee tier approximates the full spread,
            # so half_spread ≈ fee/2 applied to the mid-price.
            half_spread = mid * (dex.fee_bps / 10_000.0 / 2)
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
    # DEX-specific quoting
    # ------------------------------------------------------------------

    def _quote_uniswap_v3(
        self, chain: str, weth: str, usdc: str
    ) -> float:
        """Get WETH/USDC mid-price from Uniswap V3 QuoterV2."""
        w3 = self._w3[chain]
        quoter = w3.eth.contract(
            address=Web3.to_checksum_address(UNISWAP_V3_QUOTER_V2),
            abi=UNISWAP_V3_QUOTER_ABI,
        )
        amount_in = 10 ** WETH_DECIMALS  # 1 WETH
        fee = 3000  # 0.30% tier — most liquid for WETH/USDC

        result = quoter.functions.quoteExactInputSingle(
            (
                Web3.to_checksum_address(weth),
                Web3.to_checksum_address(usdc),
                amount_in,
                fee,
                0,  # sqrtPriceLimitX96 = 0 means no limit
            )
        ).call()

        amount_out = result[0]
        return amount_out / (10 ** USDC_DECIMALS)

    def _quote_sushi_v3(
        self, chain: str, weth: str, usdc: str
    ) -> float:
        """Get WETH/USDC mid-price from SushiSwap V3 QuoterV2."""
        quoter_addr = SUSHI_V3_QUOTER.get(chain)
        if quoter_addr is None:
            raise OnChainMarketError(
                f"No SushiSwap V3 quoter address for chain '{chain}'."
            )

        w3 = self._w3[chain]
        quoter = w3.eth.contract(
            address=Web3.to_checksum_address(quoter_addr),
            abi=SUSHI_V3_QUOTER_ABI,
        )
        amount_in = 10 ** WETH_DECIMALS
        fee = 3000

        result = quoter.functions.quoteExactInputSingle(
            (
                Web3.to_checksum_address(weth),
                Web3.to_checksum_address(usdc),
                amount_in,
                fee,
                0,
            )
        ).call()

        amount_out = result[0]
        return amount_out / (10 ** USDC_DECIMALS)

    def _quote_pancakeswap_v3(
        self, chain: str, weth: str, usdc: str
    ) -> float:
        """Get WETH/USDC mid-price from PancakeSwap V3 QuoterV2.

        PancakeSwap V3 is a Uniswap V3 fork — same QuoterV2 interface,
        different contract addresses.  This is the video's recommended
        second DEX alongside Uniswap.
        """
        quoter_addr = PANCAKE_V3_QUOTER.get(chain)
        if quoter_addr is None:
            raise OnChainMarketError(
                f"No PancakeSwap V3 quoter address for chain '{chain}'."
            )

        w3 = self._w3[chain]
        quoter = w3.eth.contract(
            address=Web3.to_checksum_address(quoter_addr),
            abi=PANCAKE_V3_QUOTER_ABI,
        )
        amount_in = 10 ** WETH_DECIMALS
        fee = 2500  # PancakeSwap V3 uses 2500 (0.25%) as a common fee tier

        result = quoter.functions.quoteExactInputSingle(
            (
                Web3.to_checksum_address(weth),
                Web3.to_checksum_address(usdc),
                amount_in,
                fee,
                0,
            )
        ).call()

        amount_out = result[0]
        return amount_out / (10 ** USDC_DECIMALS)

    def _quote_balancer_v2(
        self, chain: str, weth: str, usdc: str
    ) -> float:
        """Get WETH/USDC mid-price from Balancer V2 Vault queryBatchSwap."""
        pool_id = BALANCER_POOL_IDS.get(chain)
        if pool_id is None:
            raise OnChainMarketError(
                f"No Balancer pool ID configured for chain '{chain}'."
            )

        w3 = self._w3[chain]
        vault = w3.eth.contract(
            address=Web3.to_checksum_address(BALANCER_VAULT),
            abi=BALANCER_VAULT_ABI,
        )

        weth_cs = Web3.to_checksum_address(weth)
        usdc_cs = Web3.to_checksum_address(usdc)

        # Assets must be sorted by address for Balancer.
        assets = sorted([weth_cs, usdc_cs])
        weth_index = assets.index(weth_cs)
        usdc_index = assets.index(usdc_cs)

        amount_in = 10 ** WETH_DECIMALS  # 1 WETH

        # kind=0 is GIVEN_IN
        result = vault.functions.queryBatchSwap(
            0,  # SwapKind.GIVEN_IN
            [
                (
                    bytes.fromhex(pool_id[2:]) if pool_id.startswith("0x") else bytes.fromhex(pool_id),
                    weth_index,
                    usdc_index,
                    amount_in,
                    b"",
                )
            ],
            assets,
            (
                "0x0000000000000000000000000000000000000000",  # sender
                False,
                "0x0000000000000000000000000000000000000000",  # recipient
                False,
            ),
        ).call()

        # result is int256[] — deltas per asset.  Positive = tokens going IN,
        # negative = tokens coming OUT.  The USDC delta should be negative.
        usdc_delta = result[usdc_index]
        if usdc_delta >= 0:
            raise OnChainMarketError(
                f"Balancer queryBatchSwap returned non-negative USDC delta: {usdc_delta}"
            )
        amount_out = abs(usdc_delta)
        return amount_out / (10 ** USDC_DECIMALS)
