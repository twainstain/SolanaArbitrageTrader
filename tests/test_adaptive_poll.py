"""Tests for core.adaptive_poll.AdaptivePoll (Phase 2d)."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "trading_platform" / "src"))

from core.adaptive_poll import AdaptivePoll

D = Decimal


class AdaptivePollTests(unittest.TestCase):
    def test_without_fast_always_returns_slow(self):
        p = AdaptivePoll(slow_seconds=0.75)
        self.assertEqual(p.current_interval(), 0.75)
        p.observe(D("1.0"), D("0.0005"))   # definitely a "hit"
        self.assertEqual(p.current_interval(), 0.75)

    def test_fast_not_less_than_slow_is_treated_as_disabled(self):
        p = AdaptivePoll(slow_seconds=0.75, fast_seconds=1.0)   # fast >= slow
        p.observe(D("1.0"), D("0.0005"))
        self.assertEqual(p.current_interval(), 0.75)

    def test_starts_slow_with_empty_window(self):
        p = AdaptivePoll(slow_seconds=0.75, fast_seconds=0.25)
        self.assertEqual(p.current_interval(), 0.75)

    def test_downshifts_on_near_hit(self):
        p = AdaptivePoll(slow_seconds=0.75, fast_seconds=0.25, near_hit_ratio=0.5)
        # min = 0.001. Threshold = 0.0005. net_profit = 0.0008 → above threshold → near-hit.
        p.observe(D("0.0008"), D("0.001"))
        self.assertEqual(p.current_interval(), 0.25)

    def test_stays_slow_when_net_profit_below_half_threshold(self):
        p = AdaptivePoll(slow_seconds=0.75, fast_seconds=0.25, near_hit_ratio=0.5)
        p.observe(D("0.0003"), D("0.001"))          # below 0.0005 threshold
        self.assertEqual(p.current_interval(), 0.75)

    def test_window_rolls_over(self):
        p = AdaptivePoll(slow_seconds=0.75, fast_seconds=0.25, window=3)
        p.observe(D("0.0009"), D("0.001"))          # near-hit
        self.assertEqual(p.current_interval(), 0.25)
        # Three consecutive non-hits push the near-hit out of the window.
        for _ in range(3):
            p.observe(D("0"), D("0.001"))
        self.assertEqual(p.current_interval(), 0.75)

    def test_custom_near_hit_ratio(self):
        # ratio=0.9 → threshold = 0.9 × 0.001 = 0.0009
        p = AdaptivePoll(slow_seconds=0.75, fast_seconds=0.25, near_hit_ratio=0.9)
        p.observe(D("0.0008"), D("0.001"))          # below 0.0009
        self.assertEqual(p.current_interval(), 0.75)
        p.observe(D("0.00095"), D("0.001"))         # above 0.0009 → near-hit
        self.assertEqual(p.current_interval(), 0.25)

    def test_degenerate_min_profit_zero_treats_nonneg_as_hit(self):
        p = AdaptivePoll(slow_seconds=0.75, fast_seconds=0.25)
        p.observe(D("0"), D("0"))                   # edge case: min_profit=0
        self.assertEqual(p.current_interval(), 0.25)
        p.reset()
        self.assertEqual(p.current_interval(), 0.75)
        p.observe(D("-0.001"), D("0"))              # negative net → not a hit
        self.assertEqual(p.current_interval(), 0.75)

    def test_reset_clears_window(self):
        p = AdaptivePoll(slow_seconds=0.75, fast_seconds=0.25)
        p.observe(D("0.001"), D("0.001"))
        self.assertEqual(p.current_interval(), 0.25)
        p.reset()
        self.assertEqual(p.current_interval(), 0.75)

    def test_rejects_negative_config(self):
        with self.assertRaises(ValueError):
            AdaptivePoll(slow_seconds=-1)
        with self.assertRaises(ValueError):
            AdaptivePoll(slow_seconds=0.75, fast_seconds=-0.1)
        with self.assertRaises(ValueError):
            AdaptivePoll(slow_seconds=0.75, near_hit_ratio=-0.1)
        with self.assertRaises(ValueError):
            AdaptivePoll(slow_seconds=0.75, window=0)


class BotConfigAdaptivePollFieldsTests(unittest.TestCase):
    def test_config_loads_fast_poll_and_related_fields(self):
        import json
        import tempfile
        from core.config import BotConfig

        cfg = {
            "pair": "SOL/USDC",
            "base_asset": "SOL",
            "quote_asset": "USDC",
            "trade_size": 1.0,
            "min_profit_base": 0.0005,
            "priority_fee_lamports": 20000,
            "slippage_bps": 15,
            "poll_interval_seconds": 0.75,
            "fast_poll_seconds": 0.25,
            "near_hit_ratio": 0.4,
            "adaptive_window": 60,
            "venues": [
                {"name": "Jupiter-Best",   "fee_bps": 0},
                {"name": "Jupiter-Direct", "fee_bps": 0},
            ],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(cfg, f)
            p = f.name
        try:
            bc = BotConfig.from_file(p)
        finally:
            Path(p).unlink(missing_ok=True)

        self.assertEqual(bc.poll_interval_seconds, 0.75)
        self.assertEqual(bc.fast_poll_seconds, 0.25)
        self.assertEqual(bc.near_hit_ratio, 0.4)
        self.assertEqual(bc.adaptive_window, 60)

    def test_config_defaults_when_fast_poll_absent(self):
        import json
        import tempfile
        from core.config import BotConfig

        cfg = {
            "pair": "SOL/USDC",
            "base_asset": "SOL",
            "quote_asset": "USDC",
            "trade_size": 1.0,
            "min_profit_base": 0.0005,
            "priority_fee_lamports": 20000,
            "slippage_bps": 15,
            "poll_interval_seconds": 0.75,
            "venues": [
                {"name": "Jupiter-Best",   "fee_bps": 0},
                {"name": "Jupiter-Direct", "fee_bps": 0},
            ],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(cfg, f)
            p = f.name
        try:
            bc = BotConfig.from_file(p)
        finally:
            Path(p).unlink(missing_ok=True)

        self.assertIsNone(bc.fast_poll_seconds)
        self.assertEqual(bc.near_hit_ratio, 0.5)
        self.assertEqual(bc.adaptive_window, 30)


if __name__ == "__main__":
    unittest.main()
