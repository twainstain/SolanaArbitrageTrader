"""In-memory candidate queue between scanner and execution pipeline.

Per the architecture doc event flow:
  scanner -> candidate queue -> execution pipeline -> results store

Thread-safe bounded priority queue backed by heapq.
Push and pop are O(log n) instead of O(n log n) with list.sort().
Drops lowest-priority candidate when full (back-pressure).

Heap structure:
  We store (priority, seq, QueuedCandidate) tuples in a min-heap.
  The root is always the LOWEST priority item in the queue — this is the
  admission threshold.  When a new candidate arrives and the queue is full:
    - If new.priority > root.priority: evict root via heapreplace (O(log n))
    - If new.priority <= root.priority: drop the new candidate (O(1))

  To POP the highest-priority candidate, we use heapq.nlargest(1) + remove,
  or rebuild a max-heap.  Since pop() is called much less frequently than
  push() (one pop per pipeline cycle vs. many pushes per scan), we use
  nlargest for pop and keep push as O(log n).
"""

from __future__ import annotations

import heapq
import logging
import threading
import time
from dataclasses import dataclass, field

from models import Opportunity

logger = logging.getLogger(__name__)


@dataclass
class QueuedCandidate:
    """A candidate opportunity waiting in the queue."""
    opportunity: Opportunity
    enqueued_at: float = 0.0
    priority: float = 0.0  # higher = more urgent (composite score)
    scan_marks: dict = field(default_factory=dict)  # snapshot of scan timing marks


class CandidateQueue:
    """Thread-safe bounded priority queue for arbitrage candidates.

    Uses a min-heap so the lowest-priority candidate is always the root.
    This makes push O(log n): when full, we compare the new candidate
    against the root and either drop the new one or evict the root.

    Pop extracts the highest-priority candidate by negating priorities
    into a temporary max-heap extraction.
    """

    def __init__(self, max_size: int = 100) -> None:
        self._lock = threading.Lock()
        # Min-heap of (priority, seq, QueuedCandidate).
        # seq is a tie-breaker: lower seq = older = popped first at equal priority.
        # Using positive priority so root = lowest priority = eviction candidate.
        self._heap: list[tuple[float, int, QueuedCandidate]] = []
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

    def push(self, opportunity: Opportunity, priority: float = 0.0,
             scan_marks: dict | None = None) -> bool:
        """Add a candidate to the queue. Returns False if it was dropped.

        O(log n) via heappush or heapreplace.
        """
        candidate = QueuedCandidate(
            opportunity=opportunity,
            enqueued_at=time.time(),
            priority=priority,
            scan_marks=scan_marks or {},
        )

        with self._lock:
            self._total_enqueued += 1
            self._seq += 1
            entry = (priority, self._seq, candidate)

            if len(self._heap) < self._max_size:
                heapq.heappush(self._heap, entry)
                return True

            # Queue full — root is the lowest-priority item (eviction candidate).
            if priority <= self._heap[0][0]:
                # New candidate is worse than or equal to the worst in queue — drop it.
                self._total_dropped += 1
                return False

            # New candidate beats the worst — evict the root and insert new.
            dropped = heapq.heapreplace(self._heap, entry)
            self._total_dropped += 1
            logger.debug("Queue full — evicted candidate with priority %.4f", dropped[0])
            return True

    def pop(self) -> QueuedCandidate | None:
        """Pop the highest-priority candidate. Returns None if empty.

        O(n) — scans for max then removes.  Acceptable because pop is called
        once per pipeline cycle (~every 0.5s), while push is called many times
        per scan cycle.  For n=333, scan is <10 microseconds.
        """
        with self._lock:
            if not self._heap:
                return None
            # Find the entry with highest priority (max).
            max_idx = 0
            for i in range(1, len(self._heap)):
                if self._heap[i][0] > self._heap[max_idx][0]:
                    max_idx = i
                elif (self._heap[i][0] == self._heap[max_idx][0]
                      and self._heap[i][1] < self._heap[max_idx][1]):
                    # Equal priority — prefer older (lower seq) for FIFO fairness.
                    max_idx = i
            # Swap with last and pop — O(1) removal from list end.
            entry = self._heap[max_idx]
            self._heap[max_idx] = self._heap[-1]
            self._heap.pop()
            # Re-heapify the disturbed position.
            if self._heap and max_idx < len(self._heap):
                heapq.heapify(self._heap)
            return entry[2]

    def pop_batch(self, max_count: int = 10) -> list[QueuedCandidate]:
        """Pop up to max_count candidates, highest priority first.

        Uses heapq.nlargest for efficient top-K extraction, then rebuilds
        the heap with the remaining items.
        """
        with self._lock:
            if not self._heap:
                return []
            # nlargest returns the K largest entries — O(n + K log n).
            top = heapq.nlargest(min(max_count, len(self._heap)), self._heap)
            top_set = {id(e) for e in top}
            self._heap = [e for e in self._heap if id(e) not in top_set]
            heapq.heapify(self._heap)
            return [entry[2] for entry in top]

    def clear(self) -> int:
        """Clear the queue. Returns the number of items removed."""
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
