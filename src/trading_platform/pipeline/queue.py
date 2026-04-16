"""Generic bounded priority queue backed by heapq.

Thread-safe. Push is O(log n), pop is O(n).
When full, lowest-priority item is evicted (back-pressure).

Products use this between their scanner/feed (producer) and
pipeline consumer. The candidate type is generic — any object works.
"""

from __future__ import annotations

import heapq
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class QueuedItem:
    """A candidate waiting in the queue."""
    item: Any
    enqueued_at: float = 0.0
    priority: float = 0.0
    metadata: dict = field(default_factory=dict)


class PriorityQueue:
    """Thread-safe bounded priority queue.

    Uses a min-heap so the lowest-priority item is always the root.
    When full, new items with higher priority evict the lowest.

    Args:
        max_size: Maximum items in the queue. 0 = unbounded.
    """

    def __init__(self, max_size: int = 100) -> None:
        self._lock = threading.Lock()
        self._heap: list[tuple[float, int, QueuedItem]] = []
        self._max_size = max_size
        self._seq = 0
        self._total_enqueued = 0
        self._total_dropped = 0

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._heap)

    @property
    def is_empty(self) -> bool:
        return self.size == 0

    def push(self, item: Any, priority: float = 0.0,
             metadata: dict | None = None) -> bool:
        """Add an item. Returns False if it was dropped (back-pressure)."""
        queued = QueuedItem(
            item=item,
            enqueued_at=time.time(),
            priority=priority,
            metadata=metadata or {},
        )

        with self._lock:
            self._total_enqueued += 1
            self._seq += 1
            entry = (priority, self._seq, queued)

            if self._max_size <= 0 or len(self._heap) < self._max_size:
                heapq.heappush(self._heap, entry)
                return True

            if priority <= self._heap[0][0]:
                self._total_dropped += 1
                return False

            dropped = heapq.heapreplace(self._heap, entry)
            self._total_dropped += 1
            logger.debug("Queue full — evicted item with priority %.4f", dropped[0])
            return True

    def pop(self) -> QueuedItem | None:
        """Pop the highest-priority item. Returns None if empty."""
        with self._lock:
            if not self._heap:
                return None
            max_idx = 0
            for i in range(1, len(self._heap)):
                if self._heap[i][0] > self._heap[max_idx][0]:
                    max_idx = i
                elif (self._heap[i][0] == self._heap[max_idx][0]
                      and self._heap[i][1] < self._heap[max_idx][1]):
                    max_idx = i
            entry = self._heap[max_idx]
            self._heap[max_idx] = self._heap[-1]
            self._heap.pop()
            if self._heap and max_idx < len(self._heap):
                heapq.heapify(self._heap)
            return entry[2]

    def pop_batch(self, max_count: int = 10) -> list[QueuedItem]:
        """Pop up to max_count items, highest priority first."""
        with self._lock:
            if not self._heap:
                return []
            top = heapq.nlargest(min(max_count, len(self._heap)), self._heap)
            top_set = {id(e) for e in top}
            self._heap = [e for e in self._heap if id(e) not in top_set]
            heapq.heapify(self._heap)
            return [entry[2] for entry in top]

    def clear(self) -> int:
        """Clear the queue. Returns count of items removed."""
        with self._lock:
            count = len(self._heap)
            self._heap.clear()
            return count

    def stats(self) -> dict:
        with self._lock:
            return {
                "current_size": len(self._heap),
                "max_size": self._max_size,
                "total_enqueued": self._total_enqueued,
                "total_dropped": self._total_dropped,
            }
