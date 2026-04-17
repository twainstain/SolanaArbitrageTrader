"""Render-smoke tests for dashboards/opportunity_detail.

The module is tiny but stitches together five repo lookups (opportunity,
pricing, risk, simulation, execution) into one HTML view. A missing
column or renamed field would 500 the per-opp detail page without this
safety net.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "trading_platform" / "src"))

from dashboards import opportunity_detail
from persistence.db import close_db, init_db
from persistence.repository import Repository


class OpportunityDetailRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(f"/tmp/oddetail_{id(self)}.db")
        self._tmp.unlink(missing_ok=True)
        self.db = init_db(str(self._tmp))
        self.repo = Repository(self.db)

    def tearDown(self) -> None:
        close_db()
        self._tmp.unlink(missing_ok=True)

    def test_unknown_opp_returns_not_found_page(self):
        html = opportunity_detail.render(self.repo, "does_not_exist")
        self.assertIn("Not found", html)
        self.assertIn("does_not_exist", html)

    def test_minimal_opp_renders_without_error(self):
        opp_id = self.repo.create_opportunity(
            pair="SOL/USDC", buy_venue="Orca-SOL/USDC", sell_venue="Jupiter-Best",
            spread_bps=Decimal("0.012"),
        )
        html = opportunity_detail.render(self.repo, opp_id)
        self.assertIn(f"Opportunity {opp_id}", html)
        self.assertIn("SOL/USDC", html)
        self.assertIn("Orca-SOL/USDC", html)
        self.assertIn("Jupiter-Best", html)
        # Pricing block shows the placeholder text when no pricing record yet.
        self.assertIn("Pricing", html)
        self.assertIn("Risk decision", html)
        self.assertIn("Simulation", html)
        self.assertIn("Execution attempt", html)

    def test_populated_opp_surfaces_pricing_and_risk(self):
        opp_id = self.repo.create_opportunity(
            pair="SOL/USDC", buy_venue="Orca-SOL/USDC", sell_venue="Jupiter-Best",
            spread_bps=Decimal("0.02"),
        )
        self.repo.save_pricing(
            opp_id=opp_id,
            input_amount=Decimal("1"),
            estimated_output=Decimal("90.5"),
            fee_cost=Decimal("0.01"),
            slippage_cost=Decimal("0.002"),
            fee_estimate_base=Decimal("0.00001"),
            expected_net_profit=Decimal("0.0008"),
        )
        self.repo.save_risk_decision(
            opp_id=opp_id, approved=True, reason_code="approved",
            threshold_snapshot={"min_profit_base": 0.0005},
        )

        html = opportunity_detail.render(self.repo, opp_id)

        # Pricing values surface.
        self.assertIn("90.5", html)
        self.assertIn("0.0008", html)
        # Risk decision surfaces with the threshold snapshot.
        self.assertIn("approved", html)
        self.assertIn("min_profit_base", html)
        # No simulation or execution rows yet — placeholder text shows.
        # (_kv_table returns "— no record —" for an empty sim dict, and the
        # exec block shows its own placeholder.)
        self.assertIn("no record", html)
        self.assertIn("No execution", html)

    def test_malformed_threshold_snapshot_doesnt_crash(self):
        opp_id = self.repo.create_opportunity(
            pair="SOL/USDC", buy_venue="Orca-SOL/USDC", sell_venue="Jupiter-Best",
            spread_bps=Decimal("0.01"),
        )
        self.repo.save_risk_decision(
            opp_id=opp_id, approved=False, reason_code="below_min_profit",
            threshold_snapshot="not-json",
        )
        # Should not raise — the render path catches JSON errors.
        html = opportunity_detail.render(self.repo, opp_id)
        self.assertIn("below_min_profit", html)


if __name__ == "__main__":
    unittest.main()
