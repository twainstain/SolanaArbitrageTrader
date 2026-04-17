"""Simulated Solana market for unit tests and offline sanity-checks.

Generates deterministic, slightly-divergent quotes across the configured
venues so the scanner/strategy can be exercised without hitting Jupiter.

The random walk is tiny (bps-scale) so most scan cycles produce plausible
same-venue spreads and occasional cross-venue opportunities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import random

from core.config import BotConfig, VenueConfig
from core.models import BPS_DIVISOR, ONE, MarketQuote

D = Decimal

# Synthetic base prices per pair (in quote asset).  Used when the sim market
# has no external price source.  Values are approximate SOL=$165, USDC=USDT.
PAIR_BASE_PRICES: dict[str, Decimal] = {
    "SOL/USDC":  D("165.00"),
    "SOL/USDT":  D("165.00"),
    "USDC/USDT": D("1.0000"),
    "USDT/USDC": D("1.0000"),
    "mSOL/SOL":  D("1.0450"),
    "jitoSOL/SOL": D("1.0520"),
}

HALF_SPREAD_FACTOR = D("0.0005")   # 5 bps half-spread baseline
DEFAULT_VOL_BPS = D("8")           # per-tick walk amplitude


@dataclass
class VenueState:
    config: VenueConfig
    current_mid_price: Decimal


@dataclass
class PairState:
    pair_name: str
    venues: list[VenueState] = field(default_factory=list)


class SimulatedMarket:
    """Lightweight deterministic market used by tests and sim-mode runs."""

    def __init__(self, config: BotConfig, seed: int = 7) -> None:
        self.config = config
        self._rng = random.Random(seed)
        self._pairs: list[PairState] = []
        pair_names = [config.pair]
        if config.extra_pairs:
            pair_names.extend(p.pair for p in config.extra_pairs)
        for pair_name in pair_names:
            base = PAIR_BASE_PRICES.get(pair_name, D("1"))
            ps = PairState(pair_name=pair_name)
            for venue in config.venues:
                # Tiny per-venue offset so cross-venue spreads exist at t=0.
                jitter = D(str(self._rng.uniform(0.995, 1.005)))
                ps.venues.append(VenueState(config=venue, current_mid_price=base * jitter))
            self._pairs.append(ps)

    def get_quotes(self) -> list[MarketQuote]:
        quotes: list[MarketQuote] = []
        for pair_state in self._pairs:
            for venue in pair_state.venues:
                self._advance_price(venue)
                half = venue.current_mid_price * HALF_SPREAD_FACTOR
                quotes.append(
                    MarketQuote(
                        venue=venue.config.name,
                        pair=pair_state.pair_name,
                        buy_price=venue.current_mid_price + half,
                        sell_price=venue.current_mid_price - half,
                        fee_bps=venue.config.fee_bps,
                        fee_included=False,
                        liquidity_usd=D("5000000"),
                        venue_type="aggregator",
                    )
                )
        return quotes

    def _advance_price(self, venue: VenueState) -> None:
        move_bps = D(str(self._rng.uniform(float(-DEFAULT_VOL_BPS), float(DEFAULT_VOL_BPS))))
        venue.current_mid_price *= ONE + (move_bps / BPS_DIVISOR)
