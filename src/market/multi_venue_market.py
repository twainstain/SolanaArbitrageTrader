"""Compose quotes from multiple Solana venues in parallel.

Fans out ``get_quotes()`` across Jupiter + Raydium + Orca (or any subset)
via a small ThreadPoolExecutor so total scan latency ≈ the slowest venue,
not the sum of them.  Per-venue failures are isolated — one slow or
erroring backend never blocks the others.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Protocol

from core.models import MarketQuote

logger = logging.getLogger(__name__)


class MarketSource(Protocol):
    def get_quotes(self) -> list[MarketQuote]: ...


class MultiVenueMarket:
    """Fan-out market source combining multiple venue adapters.

    ``backends`` is a list of ``(name, source)`` tuples.  The name is used
    only for logging — the returned ``MarketQuote.venue`` field already
    carries a per-venue label from each adapter.
    """

    def __init__(
        self,
        backends: list[tuple[str, MarketSource]],
        per_backend_timeout: float = 3.0,
    ) -> None:
        self.backends = backends
        self.per_backend_timeout = per_backend_timeout
        self._executor = ThreadPoolExecutor(
            max_workers=max(2, len(backends)),
            thread_name_prefix="mv-market",
        )

    def get_quotes(self) -> list[MarketQuote]:
        """Fan-out quotes across all venues; returns merged list.

        Side-effect: stamps wall-clock per-venue timings into
        ``self.last_venue_timings_ms`` — a dict[name, float] that the
        event loop reads to emit a `quoter_timings` latency record per
        scan. Ported from the EVM ``onchain_market.py`` perf instrumentation
        so a slow/erroring venue is visible without guessing from aggregate
        p95. Each value is measured inside the worker thread so it reflects
        true wall-clock, not queue time.
        """
        self.last_venue_timings_ms: dict[str, float] = {}
        if not self.backends:
            return []

        import time as _time

        def _timed(name: str, source: MarketSource):
            t0 = _time.monotonic()
            try:
                quotes = source.get_quotes()
                return name, quotes, None, (_time.monotonic() - t0) * 1000.0
            except Exception as exc:
                return name, [], exc, (_time.monotonic() - t0) * 1000.0

        futures = [
            self._executor.submit(_timed, name, b) for name, b in self.backends
        ]
        out: list[MarketQuote] = []
        for fut in as_completed(futures, timeout=self.per_backend_timeout * 2):
            try:
                name, quotes, exc, elapsed_ms = fut.result(timeout=self.per_backend_timeout)
            except Exception as outer_exc:
                logger.warning("[multi-venue] worker future failed: %s", outer_exc)
                continue
            self.last_venue_timings_ms[name] = elapsed_ms
            if exc is not None:
                logger.warning("[multi-venue] %s failed in %.0fms: %s", name, elapsed_ms, exc)
            else:
                out.extend(quotes)
                logger.debug("[multi-venue] %s → %d quotes (%.0fms)",
                             name, len(quotes), elapsed_ms)
        return out

    def close(self) -> None:
        self._executor.shutdown(wait=False)
