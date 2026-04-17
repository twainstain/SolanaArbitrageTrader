"""Pair and pool registry — in-memory whitelist of tradable pairs.

WHY THIS EXISTS:
  The bot needs a single source of truth for "what am I allowed to trade?"
  This registry holds pair metadata (decimals, risk class, max trade size)
  and their associated pool addresses. It acts as a whitelist — pairs not
  in the registry cannot be traded, even if the scanner finds them.

vs. other registry files:
  - discovery.py:       finds pairs dynamically via DexScreener API
  - pool_discovery.py:  finds pool addresses via factory contracts
  - monitored_pools.py: hardcoded bootstrap pool addresses
  - pair_refresher.py:  background thread that re-runs discovery hourly
  - THIS FILE:          in-memory data structures and static presets

DATA MODEL:
  PairEntry → has many PoolEntry objects
  PairRegistry → dict of PairEntry keyed by pair name

  Example: PairEntry("WETH/USDC") has pools on Uniswap (0.05% fee),
  Uniswap (0.30% fee), PancakeSwap (0.25% fee).

CLASSIFICATION:
  LiquidityClass: HIGH (>$10M), MEDIUM ($1M-$10M), LOW (<$1M)
  RiskCategory:   BLUE_CHIP (WETH, WBTC, stables), ESTABLISHED (UNI, LINK),
                  VOLATILE (meme coins — not traded in production)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class LiquidityClass(str, Enum):
    """Liquidity tier for pool classification."""
    HIGH = "high"       # > $10M TVL
    MEDIUM = "medium"   # $1M - $10M TVL
    LOW = "low"         # < $1M TVL


class RiskCategory(str, Enum):
    """Token/pair risk category."""
    BLUE_CHIP = "blue_chip"   # WETH, WBTC, major stables
    ESTABLISHED = "established"  # UNI, LINK, AAVE
    VOLATILE = "volatile"     # meme coins, small caps


@dataclass(frozen=True)
class PoolEntry:
    """One DEX pool for a pair."""
    pool_address: str
    dex: str
    chain: str
    fee_tier_bps: Decimal
    dex_type: str              # uniswap_v3, sushi_v3, pancakeswap_v3, balancer_v2
    liquidity_class: LiquidityClass = LiquidityClass.MEDIUM
    enabled: bool = True


@dataclass(frozen=True)
class PairEntry:
    """A tradable pair with its metadata and known pools."""
    pair: str                  # e.g. "WETH/USDC"
    base_asset: str
    quote_asset: str
    base_decimals: int
    quote_decimals: int
    chain: str
    risk_category: RiskCategory = RiskCategory.BLUE_CHIP
    max_trade_size: Decimal = Decimal("10")
    pools: tuple[PoolEntry, ...] = ()


class PairRegistry:
    """In-memory whitelist of tradable pairs and their pools.

    The registry is the single source of truth for what the bot is allowed
    to trade. Pools and pairs can be added programmatically or loaded
    from config.
    """

    def __init__(self) -> None:
        self._pairs: dict[str, PairEntry] = {}

    def register(self, entry: PairEntry) -> None:
        """Add or replace a pair entry."""
        self._pairs[entry.pair] = entry

    def get(self, pair: str) -> PairEntry | None:
        return self._pairs.get(pair)

    def all_pairs(self) -> list[PairEntry]:
        return list(self._pairs.values())

    def enabled_pairs(self) -> list[PairEntry]:
        """Return pairs that have at least one enabled pool."""
        return [p for p in self._pairs.values() if any(pool.enabled for pool in p.pools)]

    def pools_for_pair(self, pair: str) -> list[PoolEntry]:
        """Return all enabled pools for a pair."""
        entry = self._pairs.get(pair)
        if entry is None:
            return []
        return [p for p in entry.pools if p.enabled]

    def pairs_on_chain(self, chain: str) -> list[PairEntry]:
        """Return all pairs on a specific chain."""
        return [p for p in self._pairs.values() if p.chain == chain]

    @property
    def pair_count(self) -> int:
        return len(self._pairs)

    @property
    def pool_count(self) -> int:
        return sum(len(p.pools) for p in self._pairs.values())

    def remove(self, pair: str) -> bool:
        """Remove a pair from the registry. Returns True if it existed."""
        return self._pairs.pop(pair, None) is not None

    @classmethod
    def default_ethereum(cls) -> "PairRegistry":
        """Create a registry pre-loaded with the recommended Ethereum pairs."""
        from core.tokens import CHAIN_TOKENS

        reg = cls()
        eth = CHAIN_TOKENS["ethereum"]

        reg.register(PairEntry(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            base_decimals=18, quote_decimals=6, chain="ethereum",
            risk_category=RiskCategory.BLUE_CHIP,
            max_trade_size=Decimal("10"),
            pools=(
                PoolEntry("0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640",
                          "Uniswap", "ethereum", Decimal("5"), "uniswap_v3",
                          LiquidityClass.HIGH),
                PoolEntry("0x8ad599c3A0ff1De082011EFDDc58f1908eb6e6D8",
                          "Uniswap", "ethereum", Decimal("30"), "uniswap_v3",
                          LiquidityClass.HIGH),
                PoolEntry("0x36696169C63e42cd08ce11f5deeBbCeBae652050",
                          "PancakeSwap", "ethereum", Decimal("25"), "pancakeswap_v3",
                          LiquidityClass.MEDIUM),
            ),
        ))

        reg.register(PairEntry(
            pair="WETH/USDT", base_asset="WETH", quote_asset="USDT",
            base_decimals=18, quote_decimals=6, chain="ethereum",
            risk_category=RiskCategory.BLUE_CHIP,
            max_trade_size=Decimal("10"),
            pools=(
                PoolEntry("0x4e68Ccd3E89f51C3074ca5072bbAC773960dFa36",
                          "Uniswap", "ethereum", Decimal("30"), "uniswap_v3",
                          LiquidityClass.HIGH),
            ),
        ))

        reg.register(PairEntry(
            pair="WBTC/USDC", base_asset="WBTC", quote_asset="USDC",
            base_decimals=8, quote_decimals=6, chain="ethereum",
            risk_category=RiskCategory.BLUE_CHIP,
            max_trade_size=Decimal("0.5"),
            pools=(
                PoolEntry("0x99ac8cA7087fA4A2A1FB6357269965A2014ABc35",
                          "Uniswap", "ethereum", Decimal("30"), "uniswap_v3",
                          LiquidityClass.HIGH),
            ),
        ))

        return reg
