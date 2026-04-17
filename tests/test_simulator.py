"""PreflightSimulator tests — mocked RPC, no signing, no network."""

from __future__ import annotations

from unittest.mock import MagicMock

from execution.simulator import PreflightSimulator


def _rpc(result):
    rpc = MagicMock()
    rpc._call = MagicMock(return_value=result)
    return rpc


def test_null_err_means_ok():
    sim = PreflightSimulator(rpc=_rpc({"value": {"err": None, "logs": []}}))
    ok, reason = sim.simulate_raw(b"signed_tx_bytes")
    assert ok is True
    assert reason == "ok"


def test_non_null_err_means_reject():
    sim = PreflightSimulator(rpc=_rpc({"value": {"err": "InstructionError", "logs": []}}))
    ok, reason = sim.simulate_raw(b"signed_tx_bytes")
    assert ok is False
    assert reason.startswith("simulation_failed")


def test_slippage_log_pattern_extracted():
    rpc = _rpc({
        "value": {
            "err": {"InstructionError": [0, "Custom"]},
            "logs": ["Program log: slippage tolerance exceeded"],
        },
    })
    sim = PreflightSimulator(rpc=rpc)
    ok, reason = sim.simulate_raw(b"tx")
    assert ok is False
    assert "slippage_tolerance_exceeded" in reason


def test_empty_result_is_rejection():
    sim = PreflightSimulator(rpc=_rpc(None))
    ok, reason = sim.simulate_raw(b"tx")
    assert ok is False
    assert reason == "empty_simulation_result"


def test_rpc_error_is_caught():
    rpc = MagicMock()
    rpc._call = MagicMock(side_effect=RuntimeError("boom"))
    sim = PreflightSimulator(rpc=rpc)
    ok, reason = sim.simulate_raw(b"tx")
    assert ok is False
    assert "rpc_error" in reason


def test_no_signed_tx_provider_rejects():
    sim = PreflightSimulator(rpc=_rpc({"value": {"err": None, "logs": []}}))
    opp = MagicMock()
    ok, reason = sim.simulate(opp)
    assert ok is False
    assert "signed_tx_provider" in reason
