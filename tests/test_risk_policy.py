"""RiskPolicy tests (Solana)."""

from decimal import Decimal

from core.models import Opportunity
from risk.policy import RiskPolicy
from core.models import OpportunityStatus as Status

D = Decimal


def _opp(**overrides) -> Opportunity:
    base = dict(
        pair="SOL/USDC", buy_venue="Jupiter-Direct", sell_venue="Jupiter-Best",
        trade_size=D("1"), cost_to_buy_quote=D("165"),
        proceeds_from_sell_quote=D("166.5"),
        gross_profit_quote=D("1.5"), net_profit_quote=D("1.5"),
        net_profit_base=D("0.009"),
        gross_spread_pct=D("0.9"),
        venue_fee_cost_quote=D("0"),
        slippage_cost_quote=D("0"),
        fee_cost_base=D("0.00001"),
        liquidity_score=0.9,
        warning_flags=(),
        buy_liquidity_usd=D("1000000"),
        sell_liquidity_usd=D("1000000"),
    )
    base.update(overrides)
    return Opportunity(**base)


def test_approves_good_opportunity_live_mode():
    pol = RiskPolicy(execution_enabled=True)
    verdict = pol.evaluate(_opp())
    assert verdict.approved is True
    assert verdict.reason == "approved"


def test_simulation_mode_marks_as_simulation_approved():
    pol = RiskPolicy(execution_enabled=False)
    verdict = pol.evaluate(_opp())
    assert verdict.approved is False
    assert verdict.reason == Status.SIMULATION_APPROVED


def test_rejects_below_min_profit():
    pol = RiskPolicy(min_net_profit=D("0.1"), execution_enabled=True)
    verdict = pol.evaluate(_opp(net_profit_base=D("0.001")))
    assert verdict.approved is False
    assert verdict.reason == "below_min_profit"


def test_rejects_below_min_spread():
    pol = RiskPolicy(min_spread_pct=D("1.0"), execution_enabled=True)
    verdict = pol.evaluate(_opp(gross_spread_pct=D("0.1")))
    assert verdict.approved is False
    assert verdict.reason == "below_min_spread"


def test_rejects_too_many_warning_flags():
    pol = RiskPolicy(max_warning_flags=1, execution_enabled=True)
    verdict = pol.evaluate(_opp(warning_flags=("stale_quote", "low_liquidity", "high_fee_ratio")))
    assert verdict.approved is False
    assert verdict.reason == "too_many_flags"


def test_rejects_fee_too_large_vs_profit():
    pol = RiskPolicy(max_fee_profit_ratio=D("0.1"), execution_enabled=True)
    # fee_cost_base / net_profit_base = 0.01 / 0.02 = 0.5 > 0.1
    verdict = pol.evaluate(_opp(net_profit_base=D("0.02"), fee_cost_base=D("0.01")))
    assert verdict.approved is False
    assert verdict.reason == "fee_too_expensive"


def test_rejects_thin_pool():
    pol = RiskPolicy(min_liquidity_usd=D("500000"), execution_enabled=True)
    verdict = pol.evaluate(_opp(buy_liquidity_usd=D("100000"), sell_liquidity_usd=D("100000")))
    assert verdict.approved is False
    assert verdict.reason == "pool_too_thin"


def test_rejects_rate_limit():
    pol = RiskPolicy(max_trades_per_hour=5, execution_enabled=True)
    verdict = pol.evaluate(_opp(), current_hour_trades=10)
    assert verdict.approved is False
    assert verdict.reason == "rate_limit_exceeded"


def test_rejects_exposure():
    pol = RiskPolicy(max_exposure_per_pair=D("0.5"), execution_enabled=True)
    verdict = pol.evaluate(_opp(trade_size=D("1")), current_pair_exposure=D("0.3"))
    assert verdict.approved is False
    assert verdict.reason == "exposure_limit"


def test_disabled_policy_rejects_everything():
    pol = RiskPolicy(disabled=True, execution_enabled=True)
    verdict = pol.evaluate(_opp())
    assert verdict.approved is False
    assert verdict.reason == "execution_disabled"
