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


# ---------------------------------------------------------------------------
# Phase 4: alert hooks on trade_reverted / trade_dropped.
# ---------------------------------------------------------------------------


class _RecordingDispatcher(AlertDispatcher):
    """Dispatcher with a fake backend that captures every alert() call."""

    def __init__(self):
        super().__init__()

        class _B:
            name = "record"
            sent = []
            def send(self_inner, event_type, message, details=None):
                self_inner.sent.append((event_type, message, details))
                return True

        self._backend = _B()
        self.add_backend(self._backend)


def _reverted_verifier():
    """A verifier that returns a reverted VerificationResult."""
    from pipeline.verifier import VerificationResult

    class V:
        def verify(self, signature, **_kwargs):
            return VerificationResult(
                included=True, reverted=True, dropped=False,
                signature=signature, confirmation_slot=123,
                fee_paid_lamports=5000,
            )
    return V()


def _dropped_verifier():
    from pipeline.verifier import VerificationResult

    class V:
        def verify(self, signature, **_kwargs):
            return VerificationResult(
                included=False, reverted=False, dropped=True,
                signature=signature,
            )
    return V()


def _ok_submitter():
    """Minimal submitter returning a synthetic SubmissionRef."""
    class Ref:
        kind = "rpc"
        signature = "SIG-TEST"
        metadata: dict = {}
    class S:
        def submit(self, _opp):
            return Ref()
    return S()


def _ok_simulator():
    # Pipeline expects simulate(opp) → (ok: bool, reason: str).
    class S:
        def simulate(self, _opp):
            return True, ""
    return S()


def test_reverted_fires_trade_reverted_alert(tmp_path):
    db = init_db(str(tmp_path / "tr.db"))
    repo = Repository(db)
    policy = RiskPolicy(execution_enabled=True)
    dispatcher = _RecordingDispatcher()
    pipe = CandidatePipeline(
        repo=repo, risk_policy=policy,
        simulator=_ok_simulator(),
        submitter=_ok_submitter(),
        verifier=_reverted_verifier(),
        dispatcher=dispatcher,
    )
    pipe.process(_opp())
    events = [t[0] for t in dispatcher._backend.sent]
    assert "trade_reverted" in events
    # The alert's message should include the signature and tx explorer link.
    tr_msg = [t[1] for t in dispatcher._backend.sent if t[0] == "trade_reverted"][0]
    assert "SIG-TEST" in tr_msg
    assert "solscan.io" in tr_msg


def test_dropped_fires_trade_dropped_alert(tmp_path):
    db = init_db(str(tmp_path / "td.db"))
    repo = Repository(db)
    policy = RiskPolicy(execution_enabled=True)
    dispatcher = _RecordingDispatcher()
    pipe = CandidatePipeline(
        repo=repo, risk_policy=policy,
        simulator=_ok_simulator(),
        submitter=_ok_submitter(),
        verifier=_dropped_verifier(),
        dispatcher=dispatcher,
    )
    pipe.process(_opp())
    events = [t[0] for t in dispatcher._backend.sent]
    assert "trade_dropped" in events


def test_confirmed_does_not_fire_reverted_alert(tmp_path):
    """Sanity: successful trades don't trip the revert hook."""
    from pipeline.verifier import VerificationResult

    class OkV:
        def verify(self, signature, **_kwargs):
            return VerificationResult(
                included=True, reverted=False, dropped=False,
                signature=signature, confirmation_slot=999,
                fee_paid_lamports=5000,
                actual_profit_base=D("0.005"),
            )

    db = init_db(str(tmp_path / "ok.db"))
    repo = Repository(db)
    policy = RiskPolicy(execution_enabled=True)
    dispatcher = _RecordingDispatcher()
    pipe = CandidatePipeline(
        repo=repo, risk_policy=policy,
        simulator=_ok_simulator(),
        submitter=_ok_submitter(),
        verifier=OkV(),
        dispatcher=dispatcher,
    )
    pipe.process(_opp())
    events = [t[0] for t in dispatcher._backend.sent]
    assert "trade_reverted" not in events
    assert "trade_dropped" not in events


def test_alert_dispatcher_exception_does_not_break_pipeline(tmp_path):
    """A broken dispatcher should not cause a reverted trade to crash."""
    class Broken(AlertDispatcher):
        def trade_reverted(self, *a, **kw):      # type: ignore[override]
            raise RuntimeError("dispatcher down")

    db = init_db(str(tmp_path / "b.db"))
    repo = Repository(db)
    policy = RiskPolicy(execution_enabled=True)
    pipe = CandidatePipeline(
        repo=repo, risk_policy=policy,
        simulator=_ok_simulator(),
        submitter=_ok_submitter(),
        verifier=_reverted_verifier(),
        dispatcher=Broken(),
    )
    # Should NOT raise even though dispatcher is broken.
    result = pipe.process(_opp())
    assert result.final_status == Status.REVERTED
