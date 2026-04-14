"""Background pair discovery — auto-refresh top trading pairs every hour.

Queries DexScreener for high-volume pairs on 2+ DEXes, caches them,
and updates the bot's scan list. Runs as a daemon thread.

Usage:
    refresher = PairRefresher(interval_seconds=3600)
    refresher.start()
    # ... later ...
    pairs = refresher.get_pairs()  # returns cached DiscoveredPair list
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from registry.discovery import DiscoveredPair, discover_best_pairs
from tokens import register_token

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = 3600  # 1 hour


class PairRefresher:
    """Background thread that refreshes top trading pairs via DexScreener.

    Thread-safe: ``get_pairs()`` can be called from any thread.
    """

    def __init__(
        self,
        chains: list[str] | None = None,
        min_volume: float = 100_000,
        min_dex_count: int = 2,
        max_results: int = 20,
        interval_seconds: float = DEFAULT_INTERVAL,
    ) -> None:
        self.chains = chains or [
            "ethereum", "arbitrum", "base", "polygon",
            "optimism", "bsc", "avalanche",
        ]
        self.min_volume = min_volume
        self.min_dex_count = min_dex_count
        self.max_results = max_results
        self.interval = interval_seconds
        self._pairs: list[DiscoveredPair] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_refresh: float = 0
        self._refresh_count = 0

    def start(self) -> None:
        """Start background refresh thread. Does an immediate first refresh."""
        if self._running:
            return
        self._running = True
        # Do first refresh synchronously so pairs are available immediately.
        self._refresh()
        # Then start background thread for periodic refreshes.
        self._thread = threading.Thread(target=self._loop, daemon=True, name="pair-refresher")
        self._thread.start()
        logger.info("Pair refresher started: %d chains, refresh every %.0fm",
                     len(self.chains), self.interval / 60)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def get_pairs(self) -> list[DiscoveredPair]:
        """Get the latest cached pairs (thread-safe)."""
        with self._lock:
            return list(self._pairs)

    @property
    def pair_count(self) -> int:
        with self._lock:
            return len(self._pairs)

    @property
    def last_refresh_age_minutes(self) -> float:
        if self._last_refresh == 0:
            return -1
        return (time.monotonic() - self._last_refresh) / 60

    def stats(self) -> dict:
        with self._lock:
            return {
                "pair_count": len(self._pairs),
                "refresh_count": self._refresh_count,
                "last_refresh_age_minutes": round(self.last_refresh_age_minutes, 1),
                "interval_minutes": round(self.interval / 60, 1),
                "chains": self.chains,
                "pairs": [
                    {
                        "pair": p.pair_name,
                        "chain": p.chain,
                        "dex_count": p.dex_count,
                        "volume_24h": p.total_volume_24h,
                        "liquidity": p.total_liquidity,
                    }
                    for p in self._pairs[:10]  # top 10
                ],
            }

    def _refresh(self) -> None:
        """Run one discovery cycle."""
        try:
            pairs = discover_best_pairs(
                chains=self.chains,
                min_volume=self.min_volume,
                min_dex_count=self.min_dex_count,
                max_results=self.max_results,
            )
            # Register discovered token addresses in the dynamic registry
            for p in pairs:
                if p.base_address and p.base_symbol:
                    register_token(p.chain, p.base_symbol, p.base_address)
                if p.quote_address and p.quote_symbol:
                    register_token(p.chain, p.quote_symbol, p.quote_address)

            with self._lock:
                self._pairs = pairs
                self._last_refresh = time.monotonic()
                self._refresh_count += 1
            logger.info("Pair refresh complete: %d pairs found", len(pairs))
            for p in pairs[:5]:
                logger.info("  %s on %s — %d DEXes, $%.0f vol, $%.0f liq",
                           p.pair_name, p.chain, p.dex_count,
                           p.total_volume_24h, p.total_liquidity)
        except Exception as e:
            logger.error("Pair refresh failed: %s", e)

    def _loop(self) -> None:
        """Background loop — refresh at interval."""
        while self._running:
            time.sleep(60)  # check every minute
            if time.monotonic() - self._last_refresh >= self.interval:
                self._refresh()
