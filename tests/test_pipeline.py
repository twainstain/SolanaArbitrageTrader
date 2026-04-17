"""CandidatePipeline scanner-only tests."""

from decimal import Decimal

from alerting.dispatcher import AlertDispatcher
from core.models import Opportunity, OpportunityStatus as Status
from persistence.db import init_db
from persistence.repository import Repository
from pipeline.lifecycle import CandidatePipeline
from risk.policy import RiskPolicy

D = Decimal


def _opp() -> Opportunity:
    return Opportunity(
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
        buy_liquidity_usd=D("1000000"),
        sell_liquidity_usd=D("1000000"),
    )


def test_scanner_only_dry_run_terminal(tmp_path):
    db = init_db(str(tmp_path / "t.db"))
    repo = Repository(db)
    policy = RiskPolicy(execution_enabled=True)
    pipe = CandidatePipeline(
        repo=repo,
        risk_policy=policy,
        simulator=None,
        submitter=None,
        verifier=None,
        dispatcher=AlertDispatcher(),
    )
    result = pipe.process(_opp())
    assert result.final_status == Status.DRY_RUN
    # DB state should match
    opp = repo.get_opportunity(result.opportunity_id)
    assert opp["status"] == Status.DRY_RUN
    # Timings captured (latency visibility — explicit ask from the user)
    assert result.timings is not None
    assert "detect_ms" in result.timings
    assert "price_ms" in result.timings
    assert "risk_ms" in result.timings
    assert "total_ms" in result.timings


def test_simulation_mode_marks_simulation_approved(tmp_path):
    db = init_db(str(tmp_path / "t.db"))
    repo = Repository(db)
    policy = RiskPolicy(execution_enabled=False)
    pipe = CandidatePipeline(
        repo=repo, risk_policy=policy,
        simulator=None, submitter=None, verifier=None,
    )
    result = pipe.process(_opp())
    assert result.final_status == Status.SIMULATION_APPROVED


def test_rejected_opportunity_persists_reason(tmp_path):
    db = init_db(str(tmp_path / "t.db"))
    repo = Repository(db)
    # Force rejection via very high min_profit.
    policy = RiskPolicy(min_net_profit=D("1000"), execution_enabled=True)
    pipe = CandidatePipeline(
        repo=repo, risk_policy=policy,
        simulator=None, submitter=None, verifier=None,
    )
    result = pipe.process(_opp())
    assert result.final_status == Status.REJECTED
    risk = repo.get_risk_decision(result.opportunity_id)
    assert risk["approved"] == 0
    assert risk["reason_code"] == "below_min_profit"
