"""Shared runtime control state — scanner pause + per-pair/venue toggles.

Lives as a module-level singleton so the API (which runs on one thread of
``run_event_driven``) can mutate it and the scanner loop (another thread)
can read it each cycle.

Rules
-----

- ``paused`` — when True, the scanner loop skips the RPC fetch and waits
  ``poll_interval_seconds``.  Existing in-flight items continue.
- ``disabled_pairs`` / ``disabled_venues`` — scanner skips candidates
  whose pair or venue appears here.  Applies to *both* buy and sell sides
  for venues.

Thread-safety: reads of a single name are atomic in CPython; writes go
through the setter functions.  No lock needed for our scan rate
(~1 Hz) and the sets are small.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ControlState:
    paused: bool = False
    disabled_pairs: set[str] = field(default_factory=set)
    disabled_venues: set[str] = field(default_factory=set)

    def pair_enabled(self, pair: str) -> bool:
        return pair not in self.disabled_pairs

    def venue_enabled(self, venue: str) -> bool:
        return venue not in self.disabled_venues

    def snapshot(self) -> dict:
        return {
            "paused": self.paused,
            "disabled_pairs": sorted(self.disabled_pairs),
            "disabled_venues": sorted(self.disabled_venues),
        }


# Process-wide singleton.
CONTROL = ControlState()


def get_control() -> ControlState:
    return CONTROL
