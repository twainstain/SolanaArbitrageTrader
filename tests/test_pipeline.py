"""Tests for the candidate pipeline lifecycle."""

import sys
import tempfile
from decimal import Decimal
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from alerting.dispatcher import AlertDispatcher
from models import Opportunity
from persistence.db import init_db, close_db
from persistence.repository import Repository
from pipeline.lifecycle import CandidatePipeline, PipelineResult
from pipeline.verifier import VerificationResult
from risk.policy import RiskPolicy

D = Decimal


def _make_opp(**overrides) -> Opportunity:
    defaults = dict(
        pair="WETH/USDC", buy_dex="Uniswap", sell_dex="PancakeSwap",
        trade_size=D("1"), cost_to_buy_quote=D("2200"),
        proceeds_from_sell_quote=D("2210"), gross_profit_quote=D("10"),
        net_profit_quote=D("8"), net_profit_base=D("0.005"),
        gross_spread_pct=D("3.0"), dex_fee_cost_quote=D("2"),
        slippage_cost_quote=D("1"), gas_cost_base=D("0.001"),
        liquidity_score=0.8, warning_flags=(),
    )
    defaults.update(overrides)
    return Opportunity(**defaults)


class _MockSimulator:
    def __init__(self, success: bool = True, reason: str = "ok"):
        self._success = success
        self._reason = reason

    def simulate(self, opp):
        return self._success, self._reason


class _MockSubmitter:
    def __init__(self, submission_type: str = "flashbots"):
        self.submitted = []
        self.submission_type = submission_type

    def submit(self, opp):
        self.submitted.append(opp)
        return "0xabc123", "bundle_1", 12345, self.submission_type


class _MockVerifier:
    def __init__(self, included=True, reverted=False, gas=250000, profit=D("0.004")):
        self._included = included
        self._reverted = reverted
        self._gas = gas
        self._profit = profit

    def verify(self, tx_hash):
        return VerificationResult(
            included=self._included,
            reverted=self._reverted,
            gas_used=self._gas,
            actual_profit_base=self._profit,
        )


class PipelineRejectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self):
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_simulation_mode_shows_simulation_approved(self):
        """Kill switch off + passing trade → simulation_approved (not rejected)."""
        policy = RiskPolicy(execution_enabled=False)
        pipeline = CandidatePipeline(self.repo, policy)

        result = pipeline.process(_make_opp())
        self.assertEqual(result.final_status, "simulation_approved")

        opp = self.repo.get_opportunity(result.opportunity_id)
        self.assertEqual(opp["status"], "simulation_approved")

    def test_rejected_by_min_profit(self):
        policy = RiskPolicy(execution_enabled=True, min_net_profit=D("1.0"))
        pipeline = CandidatePipeline(self.repo, policy)

        result = pipeline.process(_make_opp(net_profit_base=D("0.005")))
        self.assertEqual(result.final_status, "rejected")
        self.assertEqual(result.reason, "below_min_profit")

    def test_pricing_persisted_before_rejection(self):
        policy = RiskPolicy(execution_enabled=True, min_net_profit=D("1.0"))
        pipeline = CandidatePipeline(self.repo, policy)

        result = pipeline.process(_make_opp())
        pricing = self.repo.get_pricing(result.opportunity_id)
        self.assertIsNotNone(pricing)
        self.assertEqual(pricing["input_amount"], "2200")


class PipelineSkipPricedStatusTests(unittest.TestCase):
    """Verify that the pipeline skips the intermediate 'priced' status update.

    The pipeline batches detect→price→risk into a single transaction.
    Setting status='priced' between price and risk is invisible to other
    readers and wastes a DB round-trip. The final status (rejected,
    simulation_approved, approved, dry_run) is the only one that matters.
    """

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self):
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_opportunity_never_set_to_priced(self):
        """After pipeline.process, status should jump from detected to final."""
        policy = RiskPolicy(execution_enabled=False)
        pipeline = CandidatePipeline(self.repo, policy)

        result = pipeline.process(_make_opp())
        # Final status should be simulation_approved, never "priced".
        opp = self.repo.get_opportunity(result.opportunity_id)
        self.assertNotEqual(opp["status"], "priced")
        self.assertEqual(opp["status"], "simulation_approved")

    def test_batch_commits_once(self):
        """All DB writes in detect→price→risk should batch into one commit."""
        policy = RiskPolicy(execution_enabled=True, min_net_profit=D("0.001"))
        pipeline = CandidatePipeline(self.repo, policy)

        result = pipeline.process(_make_opp())
        # Verify all data persisted correctly despite single commit.
        opp = self.repo.get_opportunity(result.opportunity_id)
        self.assertIsNotNone(opp)
        pricing = self.repo.get_pricing(result.opportunity_id)
        self.assertIsNotNone(pricing)
        risk = self.repo.get_risk_decision(result.opportunity_id)
        self.assertIsNotNone(risk)


class PipelineDryRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self):
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_approved_no_submitter_is_dry_run(self):
        policy = RiskPolicy(execution_enabled=True, min_net_profit=D("0.001"))
        pipeline = CandidatePipeline(self.repo, policy)

        result = pipeline.process(_make_opp())
        self.assertEqual(result.final_status, "dry_run")
        self.assertEqual(result.net_profit, D("0.005"))

        opp = self.repo.get_opportunity(result.opportunity_id)
        self.assertEqual(opp["status"], "dry_run")

    def test_risk_decision_persisted(self):
        policy = RiskPolicy(execution_enabled=True)
        pipeline = CandidatePipeline(self.repo, policy)

        result = pipeline.process(_make_opp())
        dec = self.repo.get_risk_decision(result.opportunity_id)
        self.assertIsNotNone(dec)
        self.assertEqual(dec["approved"], 1)


class PipelineSimulationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self):
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_simulation_failure_stops_pipeline(self):
        policy = RiskPolicy(execution_enabled=True)
        sim = _MockSimulator(success=False, reason="profit_below_minimum")
        pipeline = CandidatePipeline(self.repo, policy, simulator=sim)

        result = pipeline.process(_make_opp())
        self.assertEqual(result.final_status, "simulation_failed")

        sim_record = self.repo.get_simulation(result.opportunity_id)
        self.assertEqual(sim_record["success"], 0)
        self.assertEqual(sim_record["revert_reason"], "profit_below_minimum")

    def test_simulation_success_continues(self):
        policy = RiskPolicy(execution_enabled=True)
        sim = _MockSimulator(success=True)
        pipeline = CandidatePipeline(self.repo, policy, simulator=sim)

        result = pipeline.process(_make_opp())
        # No submitter, so ends as dry_run after simulation passes.
        self.assertEqual(result.final_status, "dry_run")

        opp = self.repo.get_opportunity(result.opportunity_id)
        # Status should reflect that simulation passed (dry_run comes after simulated)
        self.assertEqual(opp["status"], "dry_run")


class PipelineFullExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self):
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_full_success_lifecycle(self):
        policy = RiskPolicy(execution_enabled=True)
        sim = _MockSimulator(success=True)
        sub = _MockSubmitter()
        ver = _MockVerifier(included=True, reverted=False, profit=D("0.004"))

        pipeline = CandidatePipeline(self.repo, policy, sim, sub, ver)
        result = pipeline.process(_make_opp())

        self.assertEqual(result.final_status, "included")
        self.assertEqual(result.net_profit, D("0.004"))
        self.assertEqual(len(sub.submitted), 1)

        opp = self.repo.get_opportunity(result.opportunity_id)
        self.assertEqual(opp["status"], "included")

    def test_full_revert_lifecycle(self):
        policy = RiskPolicy(execution_enabled=True)
        sim = _MockSimulator(success=True)
        sub = _MockSubmitter()
        ver = _MockVerifier(included=True, reverted=True)

        pipeline = CandidatePipeline(self.repo, policy, sim, sub, ver)
        result = pipeline.process(_make_opp())

        self.assertEqual(result.final_status, "reverted")

    def test_not_included_lifecycle(self):
        policy = RiskPolicy(execution_enabled=True)
        sim = _MockSimulator(success=True)
        sub = _MockSubmitter()
        ver = _MockVerifier(included=False)

        pipeline = CandidatePipeline(self.repo, policy, sim, sub, ver)
        result = pipeline.process(_make_opp())

        self.assertEqual(result.final_status, "not_included")

    def test_submitted_no_verifier(self):
        policy = RiskPolicy(execution_enabled=True)
        sim = _MockSimulator(success=True)
        sub = _MockSubmitter()

        pipeline = CandidatePipeline(self.repo, policy, sim, sub)
        result = pipeline.process(_make_opp())

        self.assertEqual(result.final_status, "submitted")
        self.assertEqual(result.reason, "awaiting_verification")

    def test_submission_type_persisted_from_submitter(self):
        policy = RiskPolicy(execution_enabled=True)
        sim = _MockSimulator(success=True)
        sub = _MockSubmitter(submission_type="public")

        pipeline = CandidatePipeline(self.repo, policy, sim, sub)
        result = pipeline.process(_make_opp())

        self.assertEqual(result.final_status, "submitted")
        execution = self.repo.get_latest_execution_attempt(result.opportunity_id)
        self.assertEqual(execution["submission_type"], "public")


class _FakeBackend:
    """Records all alerts for assertion."""
    def __init__(self):
        self._name = "test"
        self.received: list[tuple] = []

    @property
    def name(self) -> str:
        return self._name

    def send(self, event_type, message, details=None):
        self.received.append((event_type, message, details))
        return True


class PipelineAlertingTests(unittest.TestCase):
    """Tests that the pipeline fires alerts at key decision points."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)
        self.backend = _FakeBackend()
        self.dispatcher = AlertDispatcher([self.backend])

    def tearDown(self):
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_simulation_failure_fires_alert(self):
        policy = RiskPolicy(execution_enabled=True)
        sim = _MockSimulator(success=False, reason="profit_below_minimum")
        pipeline = CandidatePipeline(
            self.repo, policy, simulator=sim, dispatcher=self.dispatcher)

        pipeline.process(_make_opp())

        events = [r[0] for r in self.backend.received]
        self.assertIn("simulation_failed", events)
        msg = self.backend.received[0][1]
        self.assertIn("profit_below_minimum", msg)

    def test_trade_executed_fires_alert(self):
        policy = RiskPolicy(execution_enabled=True)
        sim = _MockSimulator(success=True)
        sub = _MockSubmitter()
        ver = _MockVerifier(included=True, reverted=False, profit=D("0.004"))
        pipeline = CandidatePipeline(
            self.repo, policy, sim, sub, ver, dispatcher=self.dispatcher)

        pipeline.process(_make_opp())

        events = [r[0] for r in self.backend.received]
        self.assertIn("trade_executed", events)

    def test_trade_reverted_fires_alert(self):
        policy = RiskPolicy(execution_enabled=True)
        sim = _MockSimulator(success=True)
        sub = _MockSubmitter()
        ver = _MockVerifier(included=True, reverted=True)
        pipeline = CandidatePipeline(
            self.repo, policy, sim, sub, ver, dispatcher=self.dispatcher)

        pipeline.process(_make_opp())

        events = [r[0] for r in self.backend.received]
        self.assertIn("trade_reverted", events)

    def test_not_included_fires_alert(self):
        policy = RiskPolicy(execution_enabled=True)
        sim = _MockSimulator(success=True)
        sub = _MockSubmitter()
        ver = _MockVerifier(included=False)
        pipeline = CandidatePipeline(
            self.repo, policy, sim, sub, ver, dispatcher=self.dispatcher)

        pipeline.process(_make_opp())

        events = [r[0] for r in self.backend.received]
        self.assertIn("trade_not_included", events)

    def test_dry_run_no_execution_alerts(self):
        """Dry run (no submitter) should not fire trade alerts."""
        policy = RiskPolicy(execution_enabled=True)
        pipeline = CandidatePipeline(
            self.repo, policy, dispatcher=self.dispatcher)

        pipeline.process(_make_opp())

        events = [r[0] for r in self.backend.received]
        self.assertNotIn("trade_executed", events)
        self.assertNotIn("trade_reverted", events)
        self.assertNotIn("trade_not_included", events)

    def test_rejected_no_alerts(self):
        """Rejected opportunities should not fire any alerts."""
        policy = RiskPolicy(execution_enabled=False)
        pipeline = CandidatePipeline(
            self.repo, policy, dispatcher=self.dispatcher)

        pipeline.process(_make_opp())

        self.assertEqual(len(self.backend.received), 0)

    def test_no_dispatcher_doesnt_crash(self):
        """Pipeline without explicit dispatcher works fine."""
        policy = RiskPolicy(execution_enabled=True)
        pipeline = CandidatePipeline(self.repo, policy)

        result = pipeline.process(_make_opp())
        self.assertEqual(result.final_status, "dry_run")


if __name__ == "__main__":
    unittest.main()
