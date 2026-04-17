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
        if not self.backends:
            return []

        futures = {
            self._executor.submit(b.get_quotes): name
            for name, b in self.backends
        }
        out: list[MarketQuote] = []
        for fut in as_completed(futures, timeout=self.per_backend_timeout * 2):
            name = futures[fut]
            try:
                quotes = fut.result(timeout=self.per_backend_timeout)
                out.extend(quotes)
                logger.debug("[multi-venue] %s → %d quotes", name, len(quotes))
            except Exception as exc:
                logger.warning("[multi-venue] %s failed: %s", name, exc)
        return out

    def close(self) -> None:
        self._executor.shutdown(wait=False)
