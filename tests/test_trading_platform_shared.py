"""Focused tests for the extracted trading_platform package."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_platform.config.env import find_env_file, load_env
from trading_platform.pipeline.base_pipeline import BasePipeline
from trading_platform.risk.base_policy import RiskVerdict, RuleBasedPolicy


class AlwaysApproveRule:
    name = "always_approve"

    def evaluate(self, candidate, context):  # noqa: ANN001
        return RiskVerdict(True, "ok")


class AlwaysRejectRule:
    name = "always_reject"

    def evaluate(self, candidate, context):  # noqa: ANN001
        return RiskVerdict(False, "rejected")


class DummyPipeline(BasePipeline):
    def __init__(self) -> None:
        super().__init__()
        self.approved_called = False
        self.rejected_called = False

    def detect(self, candidate):  # noqa: ANN001
        return "candidate-1"

    def price(self, candidate_id, candidate):  # noqa: ANN001
        return None

    def evaluate_risk(self, candidate):  # noqa: ANN001
        return candidate

    def on_approved(self, candidate_id, candidate):  # noqa: ANN001
        self.approved_called = True

    def on_rejected(self, candidate_id, reason, candidate):  # noqa: ANN001
        self.rejected_called = True


class TradingPlatformRiskTests(unittest.TestCase):
    def test_simulation_mode_returns_approved_verdict_with_flag(self) -> None:
        policy = RuleBasedPolicy(rules=[AlwaysApproveRule()], simulation_mode=True)

        verdict = policy.evaluate(candidate={"id": 1})

        self.assertTrue(verdict.approved)
        self.assertEqual(verdict.reason, "simulation_approved")
        self.assertTrue(verdict.details["simulation"])

    def test_real_rejection_still_rejects_in_simulation_mode(self) -> None:
        policy = RuleBasedPolicy(rules=[AlwaysRejectRule()], simulation_mode=True)

        verdict = policy.evaluate(candidate={"id": 1})

        self.assertFalse(verdict.approved)
        self.assertEqual(verdict.reason, "rejected")

    def test_pipeline_accepts_simulation_approved_verdict(self) -> None:
        pipeline = DummyPipeline()

        result = pipeline.process(RiskVerdict(True, "simulation_approved", {"simulation": True}))

        self.assertEqual(result.final_status, "dry_run")
        self.assertTrue(pipeline.approved_called)
        self.assertFalse(pipeline.rejected_called)


class TradingPlatformEnvTests(unittest.TestCase):
    def test_find_env_file_searches_from_start_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "a" / "b"
            nested.mkdir(parents=True)
            env_file = root / ".env"
            env_file.write_text("SHARED_PLATFORM_TEST=from_env\n", encoding="utf-8")

            resolved = find_env_file(start_dir=nested)

            self.assertEqual(resolved, env_file.resolve())

    def test_load_env_returns_loaded_path(self) -> None:
        key = "SHARED_PLATFORM_LOAD_ENV_TEST"
        old = os.environ.pop(key, None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env_file = Path(tmp) / ".env"
                env_file.write_text(f"{key}=loaded\n", encoding="utf-8")

                resolved = load_env(start_dir=tmp)

                self.assertEqual(resolved, env_file.resolve())
                self.assertEqual(os.environ.get(key), "loaded")
        finally:
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
