"""Tests for the risk policy engine."""

import sys
from decimal import Decimal
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.models import Opportunity
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


class PerChainExecutionModeTests(unittest.TestCase):
    """Tests for per-chain execution mode (live/simulated/disabled)."""

    def test_chain_live_global_simulated(self) -> None:
        """Chain set to live should execute even when global is simulated."""
        policy = RiskPolicy(execution_enabled=False)
        policy.set_chain_mode("ethereum", "live")
        opp = _make_opp(chain="ethereum")
        verdict = policy.evaluate(opp)
        self.assertTrue(verdict.approved)
        self.assertEqual(verdict.reason, "approved")

    def test_chain_simulated_global_live(self) -> None:
        """Chain set to simulated should simulate even when global is live."""
        policy = RiskPolicy(execution_enabled=True)
        policy.set_chain_mode("arbitrum", "simulated")
        opp = _make_opp(chain="arbitrum")
        verdict = policy.evaluate(opp)
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "simulation_approved")

    def test_chain_disabled_rejects(self) -> None:
        """Chain set to disabled should reject immediately."""
        policy = RiskPolicy(execution_enabled=True)
        policy.set_chain_mode("base", "disabled")
        opp = _make_opp(chain="base")
        verdict = policy.evaluate(opp)
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "chain_disabled")

    def test_unknown_chain_uses_global(self) -> None:
        """Chain not in chain_execution_mode falls back to global."""
        policy = RiskPolicy(execution_enabled=True)
        opp = _make_opp(chain="polygon")
        verdict = policy.evaluate(opp)
        self.assertTrue(verdict.approved)

    def test_unknown_chain_global_simulated(self) -> None:
        policy = RiskPolicy(execution_enabled=False)
        opp = _make_opp(chain="polygon")
        verdict = policy.evaluate(opp)
        self.assertEqual(verdict.reason, "simulation_approved")

    def test_mixed_chains(self) -> None:
        """Arbitrum live, Optimism simulated, Base disabled."""
        policy = RiskPolicy(execution_enabled=False)
        policy.set_chain_mode("arbitrum", "live")
        policy.set_chain_mode("optimism", "simulated")
        policy.set_chain_mode("base", "disabled")

        v_arb = policy.evaluate(_make_opp(chain="arbitrum"))
        self.assertTrue(v_arb.approved)

        v_opt = policy.evaluate(_make_opp(chain="optimism"))
        self.assertFalse(v_opt.approved)
        self.assertEqual(v_opt.reason, "simulation_approved")

        v_base = policy.evaluate(_make_opp(chain="base"))
        self.assertFalse(v_base.approved)
        self.assertEqual(v_base.reason, "chain_disabled")

    def test_get_chain_mode_explicit(self) -> None:
        policy = RiskPolicy()
        policy.set_chain_mode("arbitrum", "live")
        self.assertEqual(policy.get_chain_mode("arbitrum"), "live")

    def test_get_chain_mode_fallback(self) -> None:
        policy = RiskPolicy(execution_enabled=False)
        self.assertEqual(policy.get_chain_mode("arbitrum"), "simulated")
        policy.execution_enabled = True
        self.assertEqual(policy.get_chain_mode("arbitrum"), "live")

    def test_set_invalid_mode_raises(self) -> None:
        policy = RiskPolicy()
        with self.assertRaises(ValueError):
            policy.set_chain_mode("arbitrum", "invalid")

    def test_disabled_chain_skips_other_rules(self) -> None:
        """Disabled chain should reject without evaluating profit/spread rules."""
        policy = RiskPolicy(execution_enabled=True, min_net_profit=D("100"))
        policy.set_chain_mode("base", "disabled")
        # This opp would fail min_profit, but disabled should come first
        opp = _make_opp(chain="base", net_profit_base=D("0.001"))
        verdict = policy.evaluate(opp)
        self.assertEqual(verdict.reason, "chain_disabled")


class ToDictTests(unittest.TestCase):
    def test_serializes_all_fields(self) -> None:
        policy = RiskPolicy()
        d = policy.to_dict()
        self.assertIn("min_net_profit_default", d)
        self.assertIn("min_spread_pct_default", d)
        self.assertIn("chain_min_spread_pct", d)
        self.assertIn("execution_enabled", d)
        self.assertIn("max_trades_per_hour", d)
        self.assertIn("chain_execution_mode", d)
        self.assertEqual(d["execution_enabled"], False)
        # Chain overrides should be serialized
        self.assertIn("ethereum", d["chain_min_spread_pct"])
        self.assertIn("arbitrum", d["chain_min_spread_pct"])
        # Per-chain profit thresholds
        self.assertIn("chain_min_net_profit", d)
        self.assertIn("ethereum", d["chain_min_net_profit"])
        self.assertIn("arbitrum", d["chain_min_net_profit"])

    def test_chain_execution_mode_in_dict(self) -> None:
        policy = RiskPolicy()
        policy.set_chain_mode("arbitrum", "live")
        d = policy.to_dict()
        self.assertEqual(d["chain_execution_mode"]["arbitrum"], "live")


class PerChainMinProfitTests(unittest.TestCase):
    """Tests for per-chain minimum net profit thresholds."""

    def test_ethereum_uses_high_threshold(self):
        """Ethereum min profit is 0.005 WETH — reject 0.001."""
        policy = RiskPolicy(execution_enabled=True)
        opp = _make_opp(chain="ethereum", net_profit_base=D("0.001"))
        verdict = policy.evaluate(opp)
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "below_min_profit")

    def test_arbitrum_uses_low_threshold(self):
        """Arbitrum min profit is 0.0002 WETH — accept 0.001."""
        policy = RiskPolicy(execution_enabled=True)
        opp = _make_opp(chain="arbitrum", net_profit_base=D("0.001"))
        verdict = policy.evaluate(opp)
        # Should NOT be rejected for below_min_profit.
        if not verdict.approved:
            self.assertNotEqual(verdict.reason, "below_min_profit")

    def test_arbitrum_rejects_below_its_threshold(self):
        """Arbitrum min is 0.0002 — reject 0.0001."""
        policy = RiskPolicy(execution_enabled=True)
        opp = _make_opp(chain="arbitrum", net_profit_base=D("0.0001"))
        verdict = policy.evaluate(opp)
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "below_min_profit")

    def test_base_uses_low_threshold(self):
        """Base min profit is 0.0002 WETH — accept 0.0005."""
        policy = RiskPolicy(execution_enabled=True)
        opp = _make_opp(chain="base", net_profit_base=D("0.0005"))
        verdict = policy.evaluate(opp)
        if not verdict.approved:
            self.assertNotEqual(verdict.reason, "below_min_profit")

    def test_unknown_chain_uses_default(self):
        """Unknown chain falls back to default 0.005 WETH."""
        policy = RiskPolicy(execution_enabled=True)
        opp = _make_opp(chain="unknown_chain", net_profit_base=D("0.001"))
        verdict = policy.evaluate(opp)
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "below_min_profit")

    def test_custom_chain_profit_override(self):
        """Custom per-chain threshold can be set."""
        policy = RiskPolicy(
            execution_enabled=True,
            chain_min_net_profit={"testchain": D("0.01")},
        )
        opp = _make_opp(chain="testchain", net_profit_base=D("0.005"))
        verdict = policy.evaluate(opp)
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "below_min_profit")


class PerPairExposureTests(unittest.TestCase):
    """Tests for per-pair max_exposure_override in exposure limit rule."""

    def test_global_exposure_rejects_large_trade(self) -> None:
        """Without override, 20000 trade with unit mismatch triggers safety net.

        trade_size=20000 >> global max=10 (>10x) indicates a non-WETH pair
        with missing max_exposure_override.  The safety net allows the trade
        rather than false-rejecting.  For WETH-sized trades, see
        test_global_exposure_rejects_moderate_trade.
        """
        policy = RiskPolicy(execution_enabled=True, max_exposure_per_pair=D("10"))
        opp = _make_opp(trade_size=D("20000"), chain="optimism")
        verdict = policy.evaluate(opp)
        # Safety net: trade_size >> 10*max → not rejected as exposure_limit
        self.assertNotEqual(verdict.reason, "exposure_limit")

    def test_global_exposure_rejects_moderate_trade(self) -> None:
        """A 15 WETH trade (only 1.5x global limit) should still be rejected."""
        policy = RiskPolicy(execution_enabled=True, max_exposure_per_pair=D("10"))
        opp = _make_opp(trade_size=D("15"), chain="base")
        verdict = policy.evaluate(opp)
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "exposure_limit")

    def test_per_pair_override_allows_large_trade(self) -> None:
        """With max_exposure_override=25000, a 20000 trade passes."""
        policy = RiskPolicy(execution_enabled=True, max_exposure_per_pair=D("10"))
        opp = _make_opp(
            trade_size=D("20000"),
            chain="optimism",
            max_exposure_override=D("25000"),
        )
        verdict = policy.evaluate(opp)
        # Should NOT be rejected for exposure_limit.
        if not verdict.approved:
            self.assertNotEqual(verdict.reason, "exposure_limit")

    def test_per_pair_override_still_rejects_when_exceeded(self) -> None:
        """Override of 15000 should still reject a 20000 trade."""
        policy = RiskPolicy(execution_enabled=True, max_exposure_per_pair=D("10"))
        opp = _make_opp(
            trade_size=D("20000"),
            chain="optimism",
            max_exposure_override=D("15000"),
        )
        verdict = policy.evaluate(opp)
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "exposure_limit")

    def test_zero_override_uses_global(self) -> None:
        """max_exposure_override=0 (default) falls back to global limit."""
        policy = RiskPolicy(execution_enabled=True, max_exposure_per_pair=D("5"))
        opp = _make_opp(trade_size=D("3"), max_exposure_override=D("0"))
        verdict = policy.evaluate(opp, current_pair_exposure=D("4"))
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "exposure_limit")

    def test_override_with_existing_exposure(self) -> None:
        """Override 30000, current exposure 15000, new trade 20000 → exceeds."""
        policy = RiskPolicy(execution_enabled=True, max_exposure_per_pair=D("10"))
        opp = _make_opp(
            trade_size=D("20000"),
            chain="optimism",
            max_exposure_override=D("30000"),
        )
        verdict = policy.evaluate(opp, current_pair_exposure=D("15000"))
        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "exposure_limit")


if __name__ == "__main__":
    unittest.main()
