"""Tests for observability.time_windows.

Windows feed the /metrics + ops/analytics dashboards. These tests exercise
the SQL against a real SQLite instance so drift in schema column names
(detected_at, fee_paid_base, fee_paid_lamports, etc.) is caught here.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from decimal import Decimal
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "trading_platform" / "src"))

from observability.time_windows import (
    WINDOWS,
    get_all_windows,
    get_pair_summary,
    get_windowed_stats,
)
from persistence.db import close_db, init_db
from persistence.repository import Repository


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class _FixtureBase(unittest.TestCase):
    """Subclasses set up a fresh in-memory DB per test."""

    def setUp(self) -> None:
        self._tmp = Path(f"/tmp/twtest_{id(self)}.db")
        self._tmp.unlink(missing_ok=True)
        self.db = init_db(str(self._tmp))
        self.repo = Repository(self.db)

    def tearDown(self) -> None:
        close_db()
        self._tmp.unlink(missing_ok=True)

    def _insert_opp(self, pair: str, status: str, detected_at: datetime) -> str:
        # Use the repo helper to create the opp, then backdate detected_at.
        opp_id = self.repo.create_opportunity(
            pair=pair, buy_venue="Orca-SOL/USDC", sell_venue="Jupiter-Best",
            spread_bps=Decimal("0.01"),
        )
        self.db.execute(
            "UPDATE opportunities SET detected_at = ?, status = ? WHERE opportunity_id = ?",
            (_iso(detected_at), status, opp_id),
        )
        self.db.commit()
        return opp_id


class GetWindowedStatsTests(_FixtureBase):
    def test_unknown_window_returns_error(self):
        out = get_windowed_stats(self.db, "bogus")
        self.assertIn("error", out)
        self.assertIn("bogus", out["error"])

    def test_empty_db_returns_zero_funnel(self):
        out = get_windowed_stats(self.db, "1h")
        self.assertEqual(out["window"], "1h")
        self.assertEqual(out["opportunities"]["total"], 0)
        self.assertEqual(out["opportunities"]["funnel"], {})

    def test_funnel_counts_only_in_window(self):
        now = datetime.now(timezone.utc)
        self._insert_opp("SOL/USDC",  "approved", now - timedelta(minutes=5))
        self._insert_opp("SOL/USDC",  "approved", now - timedelta(minutes=10))
        self._insert_opp("USDC/USDT", "rejected", now - timedelta(minutes=20))
        # This one is outside the 15m window.
        self._insert_opp("SOL/USDC",  "rejected", now - timedelta(hours=2))

        out = get_windowed_stats(self.db, "15m")
        self.assertEqual(out["opportunities"]["total"], 2)
        self.assertEqual(out["opportunities"]["funnel"]["approved"], 2)

        out_24h = get_windowed_stats(self.db, "24h")
        self.assertEqual(out_24h["opportunities"]["total"], 4)
        self.assertEqual(set(out_24h["opportunities"]["funnel"]), {"approved", "rejected"})

    def test_trade_results_zeroed_for_empty(self):
        out = get_windowed_stats(self.db, "1h")
        trades = out["trades"]
        self.assertEqual(trades["total_trades"], 0)
        self.assertEqual(trades["successful"], 0)
        self.assertEqual(trades["reverted"], 0)
        self.assertEqual(trades["dropped"], 0)
        self.assertEqual(trades["total_fee_paid_lamports"], 0)

    def test_profit_block_uses_pricing_results(self):
        now = datetime.now(timezone.utc)
        opp_id = self._insert_opp("SOL/USDC", "approved", now - timedelta(minutes=5))
        self.repo.save_pricing(
            opp_id=opp_id,
            input_amount=Decimal("1"), estimated_output=Decimal("90"),
            fee_cost=Decimal("0"), slippage_cost=Decimal("0.01"),
            fee_estimate_base=Decimal("0.00001"),
            expected_net_profit=Decimal("0.005"),
        )
        out = get_windowed_stats(self.db, "1h")
        self.assertGreaterEqual(out["profit"]["priced_count"], 1)
        self.assertAlmostEqual(out["profit"]["max_expected_profit"], 0.005, places=6)


class GetAllWindowsTests(_FixtureBase):
    def test_returns_every_window_key(self):
        out = get_all_windows(self.db)
        self.assertEqual(set(out), set(WINDOWS))
        for k, v in out.items():
            self.assertEqual(v["window"], k)
            self.assertIn("opportunities", v)
            self.assertIn("trades", v)
            self.assertIn("profit", v)


class GetPairSummaryTests(_FixtureBase):
    def test_empty_returns_empty_list(self):
        self.assertEqual(get_pair_summary(self.db), [])

    def test_groups_by_pair_and_status_sorted_by_total(self):
        now = datetime.now(timezone.utc)
        # SOL/USDC: 3 (2 approved, 1 rejected)
        self._insert_opp("SOL/USDC", "approved", now - timedelta(minutes=5))
        self._insert_opp("SOL/USDC", "approved", now - timedelta(minutes=10))
        self._insert_opp("SOL/USDC", "rejected", now - timedelta(minutes=15))
        # USDC/USDT: 1
        self._insert_opp("USDC/USDT", "rejected", now - timedelta(minutes=5))

        rows = get_pair_summary(self.db, "24h")
        self.assertEqual(len(rows), 2)
        # Sorted descending by total.
        self.assertEqual(rows[0]["pair"], "SOL/USDC")
        self.assertEqual(rows[0]["total"], 3)
        self.assertEqual(rows[0]["funnel"], {"approved": 2, "rejected": 1})
        self.assertEqual(rows[1]["pair"], "USDC/USDT")
        self.assertEqual(rows[1]["total"], 1)

    def test_unknown_window_falls_back_to_24h(self):
        now = datetime.now(timezone.utc)
        self._insert_opp("SOL/USDC", "approved", now - timedelta(hours=2))
        # 'bogus' is unknown → implementation defaults to 24h, so this record
        # still falls within the window.
        rows = get_pair_summary(self.db, "bogus")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pair"], "SOL/USDC")


if __name__ == "__main__":
    unittest.main()
