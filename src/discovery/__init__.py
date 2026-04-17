"""Pair discovery — find the best Solana pairs for arbitrage.

Ports the EVM repo's `registry.discovery` pattern to Solana: query
DexScreener + DeFi Llama, score pairs by volume × dex_count × blue-chip
bonus, return the top N. Meant to be run ad-hoc via
`scripts/discover_pairs.py`; results are a ranked list for an operator
to eyeball and paste interesting entries into `config/prod_scan.json`
under `extra_pairs`.
"""

from discovery.dexscreener import (
    DiscoveredPair,
    discover_solana_pairs,
    score_pair,
)

__all__ = ["DiscoveredPair", "discover_solana_pairs", "score_pair"]
