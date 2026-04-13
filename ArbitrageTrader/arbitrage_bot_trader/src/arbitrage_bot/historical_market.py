"""HistoricalMarket — replays downloaded price data for backtesting.

Loads one or more JSON files produced by price_downloader.py and replays
them tick-by-tick through the bot.  Each call to ``get_quotes()`` advances
to the next hourly snapshot across all loaded DEX files.

Usage::

    PYTHONPATH=src python -m arbitrage_bot.main \\
        --config config/historical_config.json \\
        --historical data/uni_eth_7d.json data/sushi_eth_7d.json \\
        --dry-run --no-sleep
"""

from __future__ import annotations

import json
from pathlib import Path

from arbitrage_bot.config import BotConfig
from arbitrage_bot.models import MarketQuote


class HistoricalMarketError(Exception):
    """Raised when historical data cannot be loaded or is exhausted."""


class HistoricalMarket:
    """Replays downloaded hourly snapshots as MarketQuotes.

    Parameters
    ----------
    config:
        The bot config (used for pair name and fee info).
    data_files:
        Paths to JSON files produced by ``price_downloader.py``.
        Each file represents one DEX venue.
    """

    def __init__(self, config: BotConfig, data_files: list[str | Path]) -> None:
        self.config = config

        if not data_files:
            raise HistoricalMarketError("At least one data file is required.")

        self._venues: list[dict] = []
        for path in data_files:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            snapshots = raw.get("snapshots", [])
            if not snapshots:
                raise HistoricalMarketError(f"No snapshots in {path}.")
            self._venues.append(raw)

        # Build a unified timeline: only timestamps present in ALL venues.
        # This ensures every get_quotes() call returns a quote for every venue.
        # Venues with different hour boundaries (e.g. Messari vs native) may
        # have few or no overlapping timestamps — download matching ranges.
        timestamp_sets = []
        for venue in self._venues:
            ts_set = {s["timestamp"] for s in venue["snapshots"]}
            timestamp_sets.append(ts_set)

        common_timestamps = sorted(set.intersection(*timestamp_sets))
        if not common_timestamps:
            raise HistoricalMarketError(
                "No overlapping timestamps across the data files. "
                "Make sure they cover the same time range."
            )

        self._timeline = common_timestamps
        self._tick_index = 0

        # Index snapshots by timestamp for O(1) lookup.
        self._snapshot_index: list[dict[int, dict]] = []
        for venue in self._venues:
            idx = {s["timestamp"]: s for s in venue["snapshots"]}
            self._snapshot_index.append(idx)

    @property
    def total_ticks(self) -> int:
        return len(self._timeline)

    @property
    def ticks_remaining(self) -> int:
        return max(0, len(self._timeline) - self._tick_index)

    def get_quotes(self) -> list[MarketQuote]:
        """Return one MarketQuote per venue for the current tick, then advance."""
        if self._tick_index >= len(self._timeline):
            raise HistoricalMarketError(
                f"Historical data exhausted after {len(self._timeline)} ticks."
            )

        ts = self._timeline[self._tick_index]
        self._tick_index += 1

        quotes: list[MarketQuote] = []
        for i, venue in enumerate(self._venues):
            snapshot = self._snapshot_index[i][ts]
            mid_price = self._extract_price(snapshot, venue)

            # Find the matching DEX config for fee info.
            dex_name = venue.get("dex", f"venue_{i}")
            chain = venue.get("chain", "unknown")
            fee_bps = self._find_fee_bps(dex_name, chain)

            half_spread = mid_price * (fee_bps / 10_000.0 / 2)

            quotes.append(
                MarketQuote(
                    dex=f"{dex_name}-{chain}",
                    pair=self.config.pair,
                    buy_price=mid_price + half_spread,
                    sell_price=mid_price - half_spread,
                    fee_bps=fee_bps,
                )
            )
        return quotes

    def _extract_price(self, snapshot: dict, venue: dict) -> float:
        """Extract the WETH/USDC mid-price from a snapshot.

        Native schema snapshots have OHLC (close is preferred).
        Messari snapshots store the derived price in the close field via the
        downloader.  As a last resort, we pick the larger of token0Price /
        token1Price — this heuristic works for WETH/USDC (~2000) vs the
        inverse (~0.0005), but would fail for pairs near parity.
        """
        # Prefer the close price (populated by both native OHLC and Messari normalizer).
        close = snapshot.get("close", 0)
        if close and float(close) > 0:
            return float(close)

        # Fall back to raw token price fields from the subgraph.
        t0p = float(snapshot.get("token0Price", 0))
        t1p = float(snapshot.get("token1Price", 0))

        # Heuristic: the larger value is WETH priced in USDC (~2000 >> ~0.0005).
        if t0p > t1p:
            return t0p
        elif t1p > 0:
            return t1p
        else:
            raise HistoricalMarketError(
                f"Cannot extract price from snapshot at ts={snapshot.get('timestamp')}"
            )

    def _find_fee_bps(self, dex_name: str, chain: str) -> float:
        """Find the fee_bps from config for a matching DEX, or default to 30."""
        for dex in self.config.dexes:
            if dex.dex_type == dex_name or dex.name.lower().startswith(dex_name.split("_")[0]):
                return dex.fee_bps
        return 30.0  # default Uniswap 0.30% tier
