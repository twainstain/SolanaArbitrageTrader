"""In-memory candidate queue between scanner and execution pipeline.

Per the architecture doc event flow:
  scanner -> candidate queue -> execution pipeline -> results store

Thread-safe bounded queue. Drops oldest candidates if full (back-pressure).
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

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
    """Thread-safe bounded queue for arbitrage candidates.

    The scanner pushes candidates; the pipeline pops them for evaluation.
    If the queue is full, the lowest-priority candidate is dropped.
    """

    def __init__(self, max_size: int = 100) -> None:
        self._lock = threading.Lock()
        self._queue: list[QueuedCandidate] = []
        self._max_size = max_size
        self._total_enqueued = 0
        self._total_dropped = 0

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._queue)

    @property
    def is_empty(self) -> bool:
        return self.size == 0

    def push(self, opportunity: Opportunity, priority: float = 0.0,
             scan_marks: dict | None = None) -> bool:
        """Add a candidate to the queue. Returns False if it was dropped."""
        import time
        candidate = QueuedCandidate(
            opportunity=opportunity,
            enqueued_at=time.time(),
            priority=priority,
            scan_marks=scan_marks or {},
        )

        with self._lock:
            self._total_enqueued += 1

            if len(self._queue) >= self._max_size:
                # Drop the lowest-priority candidate.
                self._queue.sort(key=lambda c: c.priority)
                if candidate.priority <= self._queue[0].priority:
                    # New candidate is lower priority — drop it.
                    self._total_dropped += 1
                    return False
                dropped = self._queue.pop(0)
                self._total_dropped += 1
                logger.debug("Queue full — dropped candidate with priority %.4f", dropped.priority)

            self._queue.append(candidate)
            return True

    def pop(self) -> QueuedCandidate | None:
        """Pop the highest-priority candidate. Returns None if empty."""
        with self._lock:
            if not self._queue:
                return None
            self._queue.sort(key=lambda c: c.priority, reverse=True)
            return self._queue.pop(0)

    def pop_batch(self, max_count: int = 10) -> list[QueuedCandidate]:
        """Pop up to max_count candidates, highest priority first."""
        with self._lock:
            self._queue.sort(key=lambda c: c.priority, reverse=True)
            batch = self._queue[:max_count]
            self._queue = self._queue[max_count:]
            return batch

    def clear(self) -> int:
        """Clear the queue. Returns the number of items removed."""
        with self._lock:
            count = len(self._queue)
            self._queue.clear()
            return count

    def stats(self) -> dict:
        with self._lock:
            return {
                "current_size": len(self._queue),
                "max_size": self._max_size,
                "total_enqueued": self._total_enqueued,
                "total_dropped": self._total_dropped,
            }
