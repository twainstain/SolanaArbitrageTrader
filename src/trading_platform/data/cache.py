"""Generic TTL cache — thread-safe key-value store with expiration.

Extracted from ArbitrageTrader's LiquidityCache. Products use this
for any data that should be cached with automatic expiry (quotes,
pool metadata, API responses, etc.).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """One cached item with TTL."""
    key: str
    value: object
    reason: str = ""
    cached_at: float = 0.0
    ttl: float = 300.0
    hit_count: int = 0

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.cached_at) > self.ttl


class TTLCache:
    """Thread-safe TTL cache.

    Usage:
        cache = TTLCache(ttl_seconds=300)
        cache.set("key", value, reason="discovered")
        if cache.has("key"):
            val = cache.get("key")
        cache.delete("key")
    """

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self._total_hits = 0
        self._total_misses = 0

    def get(self, key: str) -> object | None:
        """Get a cached value. Returns None if expired or missing."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._total_misses += 1
                return None
            if entry.expired:
                del self._store[key]
                self._total_misses += 1
                return None
            entry.hit_count += 1
            self._total_hits += 1
            return entry.value

    def has(self, key: str) -> bool:
        """Check if a key exists and is not expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False
            if entry.expired:
                del self._store[key]
                return False
            return True

    def set(self, key: str, value: object, reason: str = "",
            ttl_override: float | None = None) -> None:
        """Set a cached value."""
        ttl = ttl_override if ttl_override is not None else self._ttl
        with self._lock:
            self._store[key] = CacheEntry(
                key=key, value=value, reason=reason,
                cached_at=time.monotonic(), ttl=ttl,
            )

    def delete(self, key: str) -> bool:
        """Delete a key. Returns True if it existed."""
        with self._lock:
            return self._store.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    @property
    def size(self) -> int:
        with self._lock:
            self._purge_expired()
            return len(self._store)

    def stats(self) -> dict:
        with self._lock:
            self._purge_expired()
            return {
                "size": len(self._store),
                "total_hits": self._total_hits,
                "total_misses": self._total_misses,
                "hit_rate": round(self._total_hits / max(self._total_hits + self._total_misses, 1), 3),
                "ttl_seconds": self._ttl,
            }

    def _purge_expired(self) -> None:
        expired_keys = [k for k, v in self._store.items() if v.expired]
        for k in expired_keys:
            del self._store[k]
