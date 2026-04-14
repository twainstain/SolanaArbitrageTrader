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

    def __post_init__(self) -> None:
        val = self.trade_size
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            object.__setattr__(self, "trade_size", D(str(val)))


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
        )
        config.validate()
        return config

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
