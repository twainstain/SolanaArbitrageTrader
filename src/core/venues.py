"""Solana venue registry.

A "venue" is a quote source (AMM or aggregator). Phase 1 supports Jupiter
aggregator quotes only. Direct pool adapters (Raydium, Orca, Meteora) are
deferred to Phase 2 — their configs are defined here so the scanner can
filter them when they come online.

Venue kinds
-----------

- ``aggregator`` — Jupiter. Returns one best-route quote per side.
- ``amm``         — direct pool adapter (Raydium/Orca/Meteora). Future.

Fee discovery
-------------

Jupiter includes all route fees in its returned output amount, so quotes
from Jupiter set ``fee_included=True`` and ``fee_bps=0`` for display.

Direct pool adapters will use the per-pool fee tier — deferred.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Venue:
    name: str
    kind: str                 # "aggregator" | "amm"
    label: str                # human-readable (e.g. "Jupiter", "Raydium CLMM")
    min_liquidity_usd: int    # per-pool TVL gate
    enabled: bool = True


# Jupiter is the only venue enabled in Phase 1.
# Lamport/USD thresholds here are conservative starting values — calibrate in
# Phase 2 using real quote data (see docs/solana_migration_status.md).
VENUES: dict[str, Venue] = {
    "Jupiter": Venue("Jupiter", "aggregator", "Jupiter lite-api v1", 100_000, True),
    # Enabled in Phase 2b — direct pool quoters reading on-chain via RPC:
    "Raydium":  Venue("Raydium",  "amm", "Raydium AMM V4",      250_000, True),
    "Orca":     Venue("Orca",     "amm", "Orca Whirlpool",      250_000, True),
    # Deferred — add in Phase 2c if we need more venue diversity:
    "Meteora":  Venue("Meteora",  "amm", "Meteora DLMM",        250_000, False),
    "Phoenix":  Venue("Phoenix",  "amm", "Phoenix",             250_000, False),
}


def enabled_venues() -> list[Venue]:
    return [v for v in VENUES.values() if v.enabled]


def get_venue(name: str) -> Venue:
    if name not in VENUES:
        raise KeyError(f"Unknown Solana venue: {name}")
    return VENUES[name]
