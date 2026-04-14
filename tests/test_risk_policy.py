"""Tests for the risk policy engine."""

import sys
from decimal import Decimal
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models import Opportunity
from risk.policy import RiskPolicy, RiskVerdict

D = Decimal


def _make_opp(**overrides) -> Opportunity:
    defaults = dict(
        pair="WETH/USDC", buy_dex="A", sell_dex="B",
        trade_size=D("1"), cost_to_buy_quote=D("3001"),
        proceeds_from_sell_quote=D("3079"), gross_profit_quote=D("78"),
        net_profit_quote=D("50"), net_profit_base=D("0.005"),
        gross_spread_pct=D("3.0"),
        gas_cost_base=D("0.001"), liquidity_score=0.8,
        warning_flags=(),
    )
    defaults.update(overrides)
    return Opportunity(**defaults)


class KillSwitchTests(unittest.TestCase):
    def test_simulation_mode_shows_simulation_approved(self) -> None:
        """When execution disabled + trade passes all rules → simulation_approved."""
        policy = RiskPolicy(execution_enabled=False)
        verdict = policy.evaluate(_make_opp())
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "simulation_approved")
        self.assertTrue(verdict.details.get("simulation"))

    def test_simulation_mode_real_rejection_still_rejected(self) -> None:
        """When execution disabled + trade fails a rule → real rejection, not simulation."""
        policy = RiskPolicy(execution_enabled=False, min_net_profit=D("100"))
        verdict = policy.evaluate(_make_opp(net_profit_base=D("0.005")))
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "below_min_profit")

    def test_execution_enabled_allows(self) -> None:
        policy = RiskPolicy(execution_enabled=True)
        verdict = policy.evaluate(_make_opp())
        self.assertTrue(verdict.approved)


class MinProfitTests(unittest.TestCase):
    def test_below_min_rejected(self) -> None:
        policy = RiskPolicy(execution_enabled=True, min_net_profit=D("0.01"))
        verdict = policy.evaluate(_make_opp(net_profit_base=D("0.005")))
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "below_min_profit")

    def test_above_min_approved(self) -> None:
        policy = RiskPolicy(execution_enabled=True, min_net_profit=D("0.001"))
        verdict = policy.evaluate(_make_opp(net_profit_base=D("0.005")))
        self.assertTrue(verdict.approved)


class WarningFlagTests(unittest.TestCase):
    def test_too_many_flags_rejected(self) -> None:
        policy = RiskPolicy(execution_enabled=True, max_warning_flags=0)
        opp = _make_opp(warning_flags=("low_liquidity",))
        verdict = policy.evaluate(opp)
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "too_many_flags")

    def test_within_flag_limit_approved(self) -> None:
        policy = RiskPolicy(execution_enabled=True, max_warning_flags=2)
        opp = _make_opp(warning_flags=("low_liquidity", "thin_market"))
        verdict = policy.evaluate(opp)
        self.assertTrue(verdict.approved)


class LiquidityScoreTests(unittest.TestCase):
    def test_low_score_rejected(self) -> None:
        policy = RiskPolicy(execution_enabled=True, min_liquidity_score=0.5)
        verdict = policy.evaluate(_make_opp(liquidity_score=0.2))
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "low_liquidity_score")


class GasProfitRatioTests(unittest.TestCase):
    def test_gas_too_expensive_rejected(self) -> None:
        policy = RiskPolicy(execution_enabled=True, max_gas_profit_ratio=D("0.3"))
        # gas 0.004 / profit 0.006 = 0.67 > 0.3 (profit above min_net_profit 0.005)
        opp = _make_opp(net_profit_base=D("0.006"), gas_cost_base=D("0.004"))
        verdict = policy.evaluate(opp)
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "gas_too_expensive")

    def test_reasonable_gas_approved(self) -> None:
        policy = RiskPolicy(execution_enabled=True, max_gas_profit_ratio=D("0.5"))
        # gas 0.001 / profit 0.005 = 0.2 < 0.5
        verdict = policy.evaluate(_make_opp())
        self.assertTrue(verdict.approved)


class RateLimitTests(unittest.TestCase):
    def test_rate_limit_exceeded(self) -> None:
        policy = RiskPolicy(execution_enabled=True, max_trades_per_hour=10)
        verdict = policy.evaluate(_make_opp(), current_hour_trades=10)
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "rate_limit_exceeded")

    def test_below_rate_limit(self) -> None:
        policy = RiskPolicy(execution_enabled=True, max_trades_per_hour=10)
        verdict = policy.evaluate(_make_opp(), current_hour_trades=5)
        self.assertTrue(verdict.approved)


class ExposureLimitTests(unittest.TestCase):
    def test_over_exposure_rejected(self) -> None:
        policy = RiskPolicy(execution_enabled=True, max_exposure_per_pair=D("5"))
        verdict = policy.evaluate(
            _make_opp(trade_size=D("3")),
            current_pair_exposure=D("4"),
        )
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "exposure_limit")

    def test_within_exposure_approved(self) -> None:
        policy = RiskPolicy(execution_enabled=True, max_exposure_per_pair=D("10"))
        verdict = policy.evaluate(
            _make_opp(trade_size=D("1")),
            current_pair_exposure=D("2"),
        )
        self.assertTrue(verdict.approved)


class MinSpreadTests(unittest.TestCase):
    """Tests for the minimum spread percentage rule."""

    def test_spread_below_min_rejected(self) -> None:
        policy = RiskPolicy(execution_enabled=True, min_spread_pct=D("0.5"))
        verdict = policy.evaluate(_make_opp(gross_spread_pct=D("0.3")))
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "below_min_spread")

    def test_spread_above_min_approved(self) -> None:
        policy = RiskPolicy(execution_enabled=True, min_spread_pct=D("0.5"))
        verdict = policy.evaluate(_make_opp(gross_spread_pct=D("1.0")))
        self.assertTrue(verdict.approved)

    def test_spread_exactly_at_min_approved(self) -> None:
        policy = RiskPolicy(execution_enabled=True, min_spread_pct=D("0.5"))
        verdict = policy.evaluate(_make_opp(gross_spread_pct=D("0.5")))
        self.assertTrue(verdict.approved)

    def test_default_min_spread_is_0_4_percent(self) -> None:
        policy = RiskPolicy()
        self.assertEqual(policy.min_spread_pct, D("0.40"))

    def test_custom_min_spread(self) -> None:
        policy = RiskPolicy(min_spread_pct=D("5.0"))
        self.assertEqual(policy.min_spread_pct, D("5.0"))

    def test_tiny_spread_rejected(self) -> None:
        """A spread of 0.1% should be rejected — too thin for real execution."""
        policy = RiskPolicy(execution_enabled=True)
        verdict = policy.evaluate(_make_opp(
            gross_spread_pct=D("0.1"), net_profit_base=D("0.01"),
        ))
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "below_min_spread")

    def test_0_4_spread_approved_on_ethereum(self) -> None:
        """0.4% spread is viable on Ethereum with flash loans."""
        policy = RiskPolicy(execution_enabled=True)
        verdict = policy.evaluate(_make_opp(
            gross_spread_pct=D("0.4"), chain="ethereum",
        ))
        self.assertTrue(verdict.approved)


class PerChainSpreadTests(unittest.TestCase):
    """Per-chain minimum spread thresholds — L2s allow tighter spreads."""

    def test_arbitrum_allows_0_2_spread(self) -> None:
        policy = RiskPolicy(execution_enabled=True)
        verdict = policy.evaluate(_make_opp(
            gross_spread_pct=D("0.25"), chain="arbitrum",
        ))
        self.assertTrue(verdict.approved)

    def test_arbitrum_rejects_0_15_spread(self) -> None:
        policy = RiskPolicy(execution_enabled=True)
        verdict = policy.evaluate(_make_opp(
            gross_spread_pct=D("0.15"), chain="arbitrum",
        ))
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "below_min_spread")
        self.assertIn("arbitrum", verdict.details["reason_detail"])

    def test_base_allows_0_15_spread(self) -> None:
        policy = RiskPolicy(execution_enabled=True)
        verdict = policy.evaluate(_make_opp(
            gross_spread_pct=D("0.18"), chain="base",
        ))
        self.assertTrue(verdict.approved)

    def test_base_rejects_0_1_spread(self) -> None:
        policy = RiskPolicy(execution_enabled=True)
        verdict = policy.evaluate(_make_opp(
            gross_spread_pct=D("0.10"), chain="base",
        ))
        self.assertFalse(verdict.approved)

    def test_ethereum_uses_0_4_threshold(self) -> None:
        policy = RiskPolicy(execution_enabled=True)
        # 0.35% should fail on Ethereum (threshold 0.40%)
        verdict = policy.evaluate(_make_opp(
            gross_spread_pct=D("0.35"), chain="ethereum",
        ))
        self.assertFalse(verdict.approved)
        self.assertIn("ethereum", verdict.details["reason_detail"])

    def test_unknown_chain_uses_default(self) -> None:
        policy = RiskPolicy(execution_enabled=True)
        # Unknown chain → uses default 0.40%
        verdict = policy.evaluate(_make_opp(
            gross_spread_pct=D("0.45"), chain="zksync",
        ))
        self.assertTrue(verdict.approved)

    def test_empty_chain_uses_default(self) -> None:
        policy = RiskPolicy(execution_enabled=True)
        verdict = policy.evaluate(_make_opp(
            gross_spread_pct=D("0.35"), chain="",
        ))
        self.assertFalse(verdict.approved)

    def test_custom_chain_override(self) -> None:
        """Can pass custom per-chain thresholds."""
        policy = RiskPolicy(
            execution_enabled=True,
            chain_min_spread_pct={"ethereum": D("1.0"), "base": D("0.05")},
        )
        # Ethereum needs 1.0% now
        verdict = policy.evaluate(_make_opp(
            gross_spread_pct=D("0.8"), chain="ethereum",
        ))
        self.assertFalse(verdict.approved)

        # Base only needs 0.05%
        verdict = policy.evaluate(_make_opp(
            gross_spread_pct=D("0.1"), chain="base",
        ))
        self.assertTrue(verdict.approved)

    def test_rejection_includes_chain_threshold(self) -> None:
        """Rejection detail should show the chain-specific threshold."""
        policy = RiskPolicy(execution_enabled=True)
        verdict = policy.evaluate(_make_opp(
            gross_spread_pct=D("0.10"), chain="arbitrum",
        ))
        self.assertIn("0.20", verdict.details["reason_detail"])
        self.assertIn("chain_min_spread", verdict.details)


class ToDictTests(unittest.TestCase):
    def test_serializes_all_fields(self) -> None:
        policy = RiskPolicy()
        d = policy.to_dict()
        self.assertIn("min_net_profit", d)
        self.assertIn("min_spread_pct_default", d)
        self.assertIn("chain_min_spread_pct", d)
        self.assertIn("execution_enabled", d)
        self.assertIn("max_trades_per_hour", d)
        self.assertEqual(d["execution_enabled"], False)
        # Chain overrides should be serialized
        self.assertIn("ethereum", d["chain_min_spread_pct"])
        self.assertIn("arbitrum", d["chain_min_spread_pct"])


if __name__ == "__main__":
    unittest.main()
