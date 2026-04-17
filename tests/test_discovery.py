"""Tests for discovery.dexscreener — mocked HTTP, no network."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "trading_platform" / "src"))

from discovery.dexscreener import (
    BLUE_CHIP_TOKENS,
    DiscoveredPair,
    discover_solana_pairs,
    score_pair,
)


def _pair_json(base: str, quote: str, dex: str, volume: float, liquidity: float,
               chain: str = "solana", base_mint: str = "BASEMINT",
               quote_mint: str = "QUOTEMINT") -> dict:
    """DexScreener response shape we care about."""
    return {
        "chainId": chain,
        "dexId": dex,
        "baseToken": {"symbol": base, "address": base_mint},
        "quoteToken": {"symbol": quote, "address": quote_mint},
        "volume": {"h24": volume},
        "liquidity": {"usd": liquidity},
    }


def _mock_session(responses_by_query: dict[str, list[dict]]):
    """Return a Session-like mock whose .get() looks up by query."""
    sess = MagicMock()

    def _get(url, params=None, timeout=None):
        q = (params or {}).get("q", "")
        body = {"pairs": responses_by_query.get(q, [])}
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status = MagicMock()
        r.json = MagicMock(return_value=body)
        return r

    sess.get = MagicMock(side_effect=_get)
    return sess


class ScorePairTests(unittest.TestCase):
    def test_score_is_volume_times_dex_count_with_bluechip_bonus(self):
        self.assertEqual(score_pair(1_000_000, 3, is_blue_chip=True),
                         1_000_000 * 3 * 2.0)
        self.assertEqual(score_pair(1_000_000, 3, is_blue_chip=False),
                         1_000_000 * 3 * 1.0)

    def test_zero_volume_is_zero_score(self):
        self.assertEqual(score_pair(0, 5, is_blue_chip=True), 0.0)


class DiscoverSolanaPairsTests(unittest.TestCase):
    def test_filters_non_solana_chains(self):
        sess = _mock_session({
            "SOL": [
                _pair_json("SOL", "USDC", "raydium", 1e6, 5e5, chain="ethereum"),
                _pair_json("SOL", "USDC", "orca",    1e6, 5e5, chain="solana"),
                _pair_json("SOL", "USDC", "jupiter", 1e6, 5e5, chain="solana"),
            ],
        })
        pairs = discover_solana_pairs(
            search_tokens=["SOL"], session=sess, timeout=1.0,
            min_volume=100_000, min_liquidity=50_000, min_dex_count=2,
        )
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].pair_name, "SOL/USDC")
        # Ethereum entry was dropped; only 2 Solana DEXes remain.
        self.assertEqual(pairs[0].dex_count, 2)
        self.assertEqual(pairs[0].dex_names, ["jupiter", "orca"])

    def test_requires_min_dex_count(self):
        sess = _mock_session({
            "SOL": [_pair_json("SOL", "USDC", "raydium", 1e7, 1e6)],
        })
        pairs = discover_solana_pairs(
            search_tokens=["SOL"], session=sess, timeout=1.0,
            min_dex_count=2,
        )
        self.assertEqual(pairs, [])

    def test_volume_and_liquidity_thresholds_applied(self):
        sess = _mock_session({
            "SOL": [
                _pair_json("SOL", "USDC", "raydium", 10_000, 10_000),   # below thresholds
                _pair_json("SOL", "USDC", "orca",    10_000, 10_000),
            ],
        })
        pairs = discover_solana_pairs(
            search_tokens=["SOL"], session=sess,
            min_volume=100_000, min_liquidity=50_000, min_dex_count=2,
        )
        self.assertEqual(pairs, [])

    def test_scores_and_sorts_by_score_desc(self):
        sess = _mock_session({
            "SOL": [
                # SOL/USDC on 3 DEXes, $3M total volume, blue-chip → high score.
                _pair_json("SOL", "USDC", "raydium", 1_000_000, 500_000),
                _pair_json("SOL", "USDC", "orca",    1_000_000, 500_000),
                _pair_json("SOL", "USDC", "jupiter", 1_000_000, 500_000),
                # BONK/SOL on 2 DEXes, $400k total volume, NOT blue-chip.
                # (BONK is actually in our blue-chip list — use "UNKNOWN" for the non-bluechip case.)
                _pair_json("UNKNOWN", "SOL", "raydium", 200_000, 100_000),
                _pair_json("UNKNOWN", "SOL", "orca",    200_000, 100_000),
            ],
        })
        pairs = discover_solana_pairs(
            search_tokens=["SOL"], session=sess,
            min_volume=100_000, min_liquidity=50_000, min_dex_count=2,
            max_results=10,
        )
        names = [p.pair_name for p in pairs]
        self.assertEqual(names[0], "SOL/USDC")
        self.assertTrue(pairs[0].is_blue_chip)
        self.assertGreater(pairs[0].score, pairs[-1].score)

    def test_deduplicates_across_search_tokens(self):
        """SOL/USDC should be returned once even if both 'SOL' and 'USDC' surface it."""
        sol_usdc_pairs = [
            _pair_json("SOL", "USDC", "raydium", 1_000_000, 500_000),
            _pair_json("SOL", "USDC", "orca",    1_000_000, 500_000),
        ]
        sess = _mock_session({
            "SOL": sol_usdc_pairs,
            "USDC": sol_usdc_pairs,
        })
        pairs = discover_solana_pairs(
            search_tokens=["SOL", "USDC"], session=sess,
            min_dex_count=2,
        )
        names = [p.pair_name for p in pairs]
        self.assertEqual(names.count("SOL/USDC"), 1)

    def test_picks_max_volume_entry_for_mint_resolution(self):
        """When one DEX reports multiple pools for a pair, take the max."""
        sess = _mock_session({
            "SOL": [
                _pair_json("SOL", "USDC", "raydium", 100_000, 50_000,
                           base_mint="THIN",  quote_mint="Q"),
                _pair_json("SOL", "USDC", "raydium", 5_000_000, 2_500_000,
                           base_mint="FAT",   quote_mint="Q"),
                _pair_json("SOL", "USDC", "orca",    1_000_000, 500_000),
            ],
        })
        pairs = discover_solana_pairs(
            search_tokens=["SOL"], session=sess, min_dex_count=2,
        )
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].base_mint, "FAT")
        # total_volume takes max per DEX, not a sum of pools inside a DEX.
        # Raydium contributes 5M (max), Orca contributes 1M → 6M total.
        self.assertEqual(pairs[0].total_volume_24h, 6_000_000)

    def test_api_error_falls_through_quietly(self):
        """A failing search doesn't kill the other search iterations."""
        import requests as _r
        sess = MagicMock()
        def _get(url, params=None, timeout=None):
            if params.get("q") == "SOL":
                raise _r.ConnectionError("boom")
            r = MagicMock()
            r.status_code = 200
            r.raise_for_status = MagicMock()
            r.json = MagicMock(return_value={"pairs": [
                _pair_json("JUP", "USDC", "raydium", 500_000, 250_000),
                _pair_json("JUP", "USDC", "orca",    500_000, 250_000),
            ]})
            return r
        sess.get = MagicMock(side_effect=_get)
        pairs = discover_solana_pairs(
            search_tokens=["SOL", "USDC"], session=sess, min_dex_count=2,
        )
        names = [p.pair_name for p in pairs]
        # JUP/USDC still surfaced even though the SOL search failed.
        self.assertIn("JUP/USDC", names)


class BlueChipRecognitionTests(unittest.TestCase):
    def test_known_blue_chips_are_recognized(self):
        for sym in ("SOL", "USDC", "MSOL", "JITOSOL"):
            self.assertIn(sym, BLUE_CHIP_TOKENS)

    def test_case_insensitive_lookup(self):
        """Symbols from DexScreener may be lowercased; normalization uppers them."""
        sess = _mock_session({
            "SOL": [
                _pair_json("sol", "usdc", "raydium", 500_000, 250_000),
                _pair_json("sol", "usdc", "orca",    500_000, 250_000),
            ],
        })
        pairs = discover_solana_pairs(search_tokens=["SOL"], session=sess, min_dex_count=2)
        self.assertEqual(pairs[0].pair_name, "SOL/USDC")
        self.assertTrue(pairs[0].is_blue_chip)


if __name__ == "__main__":
    unittest.main()
