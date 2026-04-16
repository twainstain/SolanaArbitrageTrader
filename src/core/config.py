"""Configuration loading, validation, and data classes.

All financial values use Decimal (per CLAUDE.md: "NEVER use float").

Config class hierarchy
----------------------
Three frozen dataclasses represent the bot's configuration:

* **DexConfig** -- Per-DEX settings: name, simulated base price, fee tier,
  volatility estimate, and optional chain/dex_type for live mode.  In
  simulation mode ``base_price`` drives the synthetic market; in live mode
  (when ``chain`` is set) it is unused and may be zero.

* **PairConfig** -- One tradeable pair (e.g. WETH/USDC) with its own trade
  size.  When discovered via DexScreener, ``base_address`` and
  ``quote_address`` carry the on-chain contract addresses so the market source
  can price tokens that are not in the hardcoded ``tokens.py`` registry.

* **BotConfig** -- Top-level configuration that aggregates everything: the
  primary pair, financial parameters (trade size, min profit, gas estimate,
  flash-loan fee, slippage), poll timing, a list of ``DexConfig`` entries
  (at least two are required for arbitrage), and optional ``extra_pairs``.

``from_file()``
---------------
``BotConfig.from_file(path)`` is the standard way to load configuration:

1. Reads and parses the JSON file at *path*.
2. Converts all numeric values to ``Decimal`` via ``Decimal(str(...))``.
3. Supports legacy field names (``min_profit_eth`` / ``estimated_gas_cost_eth``)
   as aliases for the current ``_base`` suffixed names.
4. Parses the ``dexes`` array into ``DexConfig`` objects and the optional
   ``extra_pairs`` array into ``PairConfig`` objects.
5. Calls ``validate()`` before returning, ensuring the config is safe to use.

Validation rules
----------------
``validate()`` enforces constraints that, if violated, would cause silent
financial errors or undefined behaviour at runtime:

* At least 2 DEXs are required -- arbitrage needs a buy and a sell venue.
* ``flash_loan_provider`` must be a recognized provider (``aave_v3`` or
  ``balancer``) to ensure the correct fee schedule is applied.
* ``trade_size`` must be positive -- a zero or negative trade is meaningless.
* ``poll_interval_seconds`` must be non-negative -- negative sleep is invalid.
* Profit and gas thresholds must be non-negative -- negative minimums would
  invert the safety filters.
* Fee and slippage BPS must be non-negative -- negative fees make no sense.
* Per-DEX: ``base_price`` must be positive in simulation mode (no chain set),
  ``fee_bps`` must be in [0, 9999], and ``volatility_bps`` must be >= 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path

D = Decimal
ZERO = D("0")


@dataclass(frozen=True)
class DexConfig:
    name: str
    base_price: Decimal
    fee_bps: Decimal
    volatility_bps: Decimal
    chain: str | None = None
    dex_type: str | None = None

    def __post_init__(self) -> None:
        for attr in ("base_price", "fee_bps", "volatility_bps"):
            val = getattr(self, attr)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                object.__setattr__(self, attr, D(str(val)))


FLASH_LOAN_PROVIDERS = ("aave_v3", "balancer")


@dataclass(frozen=True)
class PairConfig:
    """One tradeable pair — base_asset/quote_asset with its own trade size.

    When discovered from DexScreener, base_address/quote_address carry the
    on-chain contract addresses so the market source can price any token
    without needing it in the hardcoded registry.
    """
    pair: str
    base_asset: str
    quote_asset: str
    trade_size: Decimal
    base_address: str | None = None
    quote_address: str | None = None
    chain: str | None = None
    # Per-pair exposure limit in base asset units.  When set, overrides
    # RiskPolicy.max_exposure_per_pair for this pair.  Essential for non-WETH
    # pairs where the global limit (e.g. 10 WETH) makes no sense in the
    # pair's native base asset (e.g. 10 OP = $1.26).
    max_exposure: Decimal | None = None

    def __post_init__(self) -> None:
        val = self.trade_size
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            object.__setattr__(self, "trade_size", D(str(val)))
        me = self.max_exposure
        if me is not None and isinstance(me, (int, float)) and not isinstance(me, bool):
            object.__setattr__(self, "max_exposure", D(str(me)))


# Per-chain minimum pool TVL (USD) for the scanner liquidity gate.
# Arbitrum is lower because legitimate WETH/USDT pools on Sushi ($37k)
# and Camelot ($39k) show 2-5% spreads — worth trading with small size.
_CHAIN_MIN_LIQUIDITY: dict[str, Decimal] = {
    "ethereum": D("1000000"),
    "arbitrum": D("25000"),
    "base": D("100000"),
    "optimism": D("100000"),
    "polygon": D("100000"),
    "bsc": D("100000"),
    "avax": D("100000"),
}


@dataclass(frozen=True)
class BotConfig:
    pair: str
    base_asset: str
    quote_asset: str
    trade_size: Decimal
    min_profit_base: Decimal
    estimated_gas_cost_base: Decimal
    flash_loan_fee_bps: Decimal
    flash_loan_provider: str
    slippage_bps: Decimal
    poll_interval_seconds: float          # timing, not financial
    dexes: list[DexConfig]
    # Optional additional pairs to scan each cycle.
    # The video recommends scanning multiple high-volume ERC-20 pairs.
    extra_pairs: list[PairConfig] | None = None
    # Per-chain execution mode: {"arbitrum": "live", "optimism": "simulated"}.
    # Chains not listed fall back to the global execution_enabled flag.
    chain_execution_mode: dict | None = None
    # Per-chain gas cost overrides: {"ethereum": 0.005, "arbitrum": 0.0002}.
    # Chains not listed use estimated_gas_cost_base as default.
    chain_gas_cost: dict | None = None

    def __post_init__(self) -> None:
        for attr in ("trade_size", "min_profit_base", "estimated_gas_cost_base",
                      "flash_loan_fee_bps", "slippage_bps"):
            val = getattr(self, attr)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                object.__setattr__(self, attr, D(str(val)))

    @classmethod
    def from_file(cls, path: str | Path) -> "BotConfig":
        """Load and validate a BotConfig from a JSON file."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        dexes = [
            DexConfig(
                name=dex["name"],
                base_price=D(str(dex["base_price"])),
                fee_bps=D(str(dex["fee_bps"])),
                volatility_bps=D(str(dex["volatility_bps"])),
                chain=dex.get("chain"),
                dex_type=dex.get("dex_type"),
            )
            for dex in data["dexes"]
        ]
        min_profit_base = data["min_profit_base"] if "min_profit_base" in data else data["min_profit_eth"]
        estimated_gas_cost_base = (
            data["estimated_gas_cost_base"]
            if "estimated_gas_cost_base" in data
            else data["estimated_gas_cost_eth"]
        )
        extra_pairs = None
        if "extra_pairs" in data:
            extra_pairs = [
                PairConfig(
                    pair=p["pair"],
                    base_asset=p["base_asset"],
                    quote_asset=p["quote_asset"],
                    trade_size=D(str(p["trade_size"])),
                    base_address=p.get("base_address"),
                    quote_address=p.get("quote_address"),
                    chain=p.get("chain"),
                    max_exposure=D(str(p["max_exposure"])) if "max_exposure" in p else None,
                )
                for p in data["extra_pairs"]
            ]

        config = cls(
            pair=data["pair"],
            base_asset=data["base_asset"],
            quote_asset=data["quote_asset"],
            trade_size=D(str(data["trade_size"])),
            min_profit_base=D(str(min_profit_base)),
            estimated_gas_cost_base=D(str(estimated_gas_cost_base)),
            flash_loan_fee_bps=D(str(data["flash_loan_fee_bps"])),
            flash_loan_provider=data.get("flash_loan_provider", "aave_v3"),
            slippage_bps=D(str(data["slippage_bps"])),
            poll_interval_seconds=float(data["poll_interval_seconds"]),
            dexes=dexes,
            extra_pairs=extra_pairs,
            chain_execution_mode=data.get("chain_execution_mode"),
            chain_gas_cost=data.get("chain_gas_cost"),
        )
        config.validate()
        return config

    def gas_cost_for_chain(self, chain: str) -> Decimal:
        """Return estimated gas cost for a chain, falling back to the global default."""
        if self.chain_gas_cost and chain in self.chain_gas_cost:
            return D(str(self.chain_gas_cost[chain]))
        return self.estimated_gas_cost_base

    @staticmethod
    def min_liquidity_for_chain(chain: str) -> Decimal:
        """Return minimum pool TVL threshold for a chain.

        Ethereum mainnet requires $1M TVL — pools below this have high price
        impact and are mostly false positives.  L2s have legitimately smaller
        pools.  Arbitrum uses $25K to capture WETH/USDT on smaller DEXes
        (Sushi, Camelot) which show consistent 2-5% spreads.
        """
        return _CHAIN_MIN_LIQUIDITY.get(
            chain.lower(), D("1000000"),
        )

    def validate(self) -> None:
        """Raise ValueError if any config field is out of acceptable range."""
        if len(self.dexes) < 2:
            raise ValueError("At least two DEX configurations are required.")
        if self.flash_loan_provider not in FLASH_LOAN_PROVIDERS:
            raise ValueError(
                f"flash_loan_provider must be one of {FLASH_LOAN_PROVIDERS}, "
                f"got '{self.flash_loan_provider}'."
            )
        if self.trade_size <= ZERO:
            raise ValueError("trade_size must be positive.")
        if self.poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds cannot be negative.")
        if self.min_profit_base < ZERO or self.estimated_gas_cost_base < ZERO:
            raise ValueError("Profit and gas thresholds cannot be negative.")
        for field_name, value in (
            ("flash_loan_fee_bps", self.flash_loan_fee_bps),
            ("slippage_bps", self.slippage_bps),
        ):
            if value < ZERO:
                raise ValueError(f"{field_name} cannot be negative.")
        for dex in self.dexes:
            # In live mode (chain is set) base_price is unused, so allow 0.
            if dex.chain is None and dex.base_price <= ZERO:
                raise ValueError(f"{dex.name}: base_price must be positive.")
            if dex.fee_bps < ZERO or dex.fee_bps >= D("10000"):
                raise ValueError(f"{dex.name}: fee_bps must be between 0 and 9999.")
            if dex.volatility_bps < ZERO:
                raise ValueError(f"{dex.name}: volatility_bps cannot be negative.")
