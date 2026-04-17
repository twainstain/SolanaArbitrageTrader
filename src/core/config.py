"""Configuration loading and validation for SolanaTrader.

All financial values use ``Decimal`` (per CLAUDE.md: "NEVER use float").

Shape
-----

- **VenueConfig** — one Solana quote source (e.g. Jupiter). In Phase 1 only
  Jupiter is enabled; direct-pool venues live in the registry but are
  disabled.
- **PairConfig** — one tradeable pair (e.g. SOL/USDC), with per-pair trade
  size and optional exposure cap in the base asset.
- **BotConfig** — top-level: primary pair, financial parameters
  (trade size, min profit in SOL, priority fee, slippage), poll timing, and
  the venue list. ``min_profit_sol`` replaces the old ``min_profit_eth``
  field. Gas/flash-loan concepts are removed — Solana uses a flat
  ``priority_fee_lamports`` plus optional Jito tip in Phase 3.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path

D = Decimal
ZERO = D("0")


@dataclass(frozen=True)
class VenueConfig:
    name: str                  # must match a key in core.venues.VENUES
    fee_bps: Decimal           # for display only — Jupiter output already nets fees
    min_liquidity_usd: Decimal = ZERO

    def __post_init__(self) -> None:
        for attr in ("fee_bps", "min_liquidity_usd"):
            val = getattr(self, attr)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                object.__setattr__(self, attr, D(str(val)))


@dataclass(frozen=True)
class PairConfig:
    """One tradeable Solana pair — base_asset/quote_asset with its own trade size."""
    pair: str
    base_asset: str
    quote_asset: str
    trade_size: Decimal
    # Optional per-pair exposure cap in base-asset units. Overrides the global
    # risk limit; essential for mixed-decimal pairs (e.g. SOL vs USDC).
    max_exposure: Decimal | None = None

    def __post_init__(self) -> None:
        if isinstance(self.trade_size, (int, float)) and not isinstance(self.trade_size, bool):
            object.__setattr__(self, "trade_size", D(str(self.trade_size)))
        me = self.max_exposure
        if me is not None and isinstance(me, (int, float)) and not isinstance(me, bool):
            object.__setattr__(self, "max_exposure", D(str(me)))


@dataclass(frozen=True)
class BotConfig:
    pair: str
    base_asset: str
    quote_asset: str
    trade_size: Decimal
    # Minimum net profit to execute, denominated in the base asset (e.g. SOL).
    # Scanner-only mode still uses this as the "actionable" threshold.
    min_profit_base: Decimal
    # Flat priority-fee estimate in lamports (SOL 10^-9).  Used as a rough
    # execution-cost proxy even in scanner mode so reports are realistic.
    # Solana mainnet priority fees: ~5k–50k lamports for normal, 100k+ congested.
    priority_fee_lamports: int
    slippage_bps: Decimal
    poll_interval_seconds: float       # timing, not financial
    venues: list[VenueConfig]
    extra_pairs: list[PairConfig] | None = None

    def __post_init__(self) -> None:
        for attr in ("trade_size", "min_profit_base", "slippage_bps"):
            val = getattr(self, attr)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                object.__setattr__(self, attr, D(str(val)))
        if isinstance(self.priority_fee_lamports, float):
            object.__setattr__(self, "priority_fee_lamports", int(self.priority_fee_lamports))

    @classmethod
    def from_file(cls, path: str | Path) -> "BotConfig":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        venues = [
            VenueConfig(
                name=v["name"],
                fee_bps=D(str(v.get("fee_bps", 0))),
                min_liquidity_usd=D(str(v.get("min_liquidity_usd", 0))),
            )
            for v in data["venues"]
        ]
        extra_pairs = None
        if "extra_pairs" in data:
            extra_pairs = [
                PairConfig(
                    pair=p["pair"],
                    base_asset=p["base_asset"],
                    quote_asset=p["quote_asset"],
                    trade_size=D(str(p["trade_size"])),
                    max_exposure=D(str(p["max_exposure"])) if "max_exposure" in p else None,
                )
                for p in data["extra_pairs"]
            ]

        config = cls(
            pair=data["pair"],
            base_asset=data["base_asset"],
            quote_asset=data["quote_asset"],
            trade_size=D(str(data["trade_size"])),
            min_profit_base=D(str(data["min_profit_base"])),
            priority_fee_lamports=int(data.get("priority_fee_lamports", 10_000)),
            slippage_bps=D(str(data["slippage_bps"])),
            poll_interval_seconds=float(data["poll_interval_seconds"]),
            venues=venues,
            extra_pairs=extra_pairs,
        )
        config.validate()
        return config

    def priority_fee_sol(self) -> Decimal:
        """Return the priority-fee estimate in SOL (not lamports)."""
        return D(self.priority_fee_lamports) / D(10 ** 9)

    def validate(self) -> None:
        if len(self.venues) < 2:
            # Arbitrage needs ≥ 2 quote sources.  Phase 1 has only Jupiter, so
            # the example config declares Jupiter twice under different route
            # preferences OR the scanner synthesises a second quote from the
            # sim_market.  See config/example_config.json for details.
            raise ValueError("At least two venue configurations are required for arbitrage.")
        if self.trade_size <= ZERO:
            raise ValueError("trade_size must be positive.")
        if self.poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds cannot be negative.")
        if self.min_profit_base < ZERO:
            raise ValueError("min_profit_base cannot be negative.")
        if self.slippage_bps < ZERO:
            raise ValueError("slippage_bps cannot be negative.")
        if self.priority_fee_lamports < 0:
            raise ValueError("priority_fee_lamports cannot be negative.")
        for venue in self.venues:
            if venue.fee_bps < ZERO or venue.fee_bps >= D("10000"):
                raise ValueError(f"{venue.name}: fee_bps must be between 0 and 9999.")
