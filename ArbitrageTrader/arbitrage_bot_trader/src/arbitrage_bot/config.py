"""Configuration loading, validation, and data classes."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class DexConfig:
    name: str
    base_price: float
    fee_bps: float
    volatility_bps: float
    chain: str | None = None
    dex_type: str | None = None


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
    trade_size: float
    base_address: str | None = None
    quote_address: str | None = None
    chain: str | None = None


@dataclass(frozen=True)
class BotConfig:
    pair: str
    base_asset: str
    quote_asset: str
    trade_size: float
    min_profit_base: float
    estimated_gas_cost_base: float
    flash_loan_fee_bps: float
    flash_loan_provider: str
    slippage_bps: float
    poll_interval_seconds: float
    dexes: list[DexConfig]
    # Optional additional pairs to scan each cycle.
    # The video recommends scanning multiple high-volume ERC-20 pairs.
    extra_pairs: list[PairConfig] | None = None

    @classmethod
    def from_file(cls, path: str | Path) -> "BotConfig":
        """Load and validate a BotConfig from a JSON file."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        dexes = [DexConfig(**dex) for dex in data["dexes"]]
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
                    trade_size=float(p["trade_size"]),
                )
                for p in data["extra_pairs"]
            ]

        config = cls(
            pair=data["pair"],
            base_asset=data["base_asset"],
            quote_asset=data["quote_asset"],
            trade_size=float(data["trade_size"]),
            min_profit_base=float(min_profit_base),
            estimated_gas_cost_base=float(estimated_gas_cost_base),
            flash_loan_fee_bps=float(data["flash_loan_fee_bps"]),
            flash_loan_provider=data.get("flash_loan_provider", "aave_v3"),
            slippage_bps=float(data["slippage_bps"]),
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
        if self.trade_size <= 0:
            raise ValueError("trade_size must be positive.")
        if self.poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds cannot be negative.")
        if self.min_profit_base < 0 or self.estimated_gas_cost_base < 0:
            raise ValueError("Profit and gas thresholds cannot be negative.")
        for field_name, value in (
            ("flash_loan_fee_bps", self.flash_loan_fee_bps),
            ("slippage_bps", self.slippage_bps),
        ):
            if value < 0:
                raise ValueError(f"{field_name} cannot be negative.")
        for dex in self.dexes:
            # In live mode (chain is set) base_price is unused, so allow 0.
            if dex.chain is None and dex.base_price <= 0:
                raise ValueError(f"{dex.name}: base_price must be positive.")
            if dex.fee_bps < 0 or dex.fee_bps >= 10_000:
                raise ValueError(f"{dex.name}: fee_bps must be between 0 and 9999.")
            if dex.volatility_bps < 0:
                raise ValueError(f"{dex.name}: volatility_bps cannot be negative.")
