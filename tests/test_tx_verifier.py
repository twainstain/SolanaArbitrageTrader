"""TxVerifier tests — mocked getSignatureStatuses + getTransaction."""

from __future__ import annotations

from unittest.mock import MagicMock

from execution.verifier import TxVerifier


def _seq_rpc(responses: list) -> MagicMock:
    """Build a mock RPC whose ``_call`` returns each response in turn."""
    rpc = MagicMock()
    # responses is a list like: ("getSignatureStatuses", result) tuples OR
    # (method, result) pairs. We round-robin by method.
    statuses_iter = iter([r for m, r in responses if m == "getSignatureStatuses"])
    tx_iter = iter([r for m, r in responses if m == "getTransaction"])

    def _call(method, params):
        if method == "getSignatureStatuses":
            try:
                return next(statuses_iter)
            except StopIteration:
                return {"value": [None]}
        if method == "getTransaction":
            return next(tx_iter)
        return None

    rpc._call = MagicMock(side_effect=_call)
    return rpc


def test_confirmed_without_err_returns_included():
    rpc = _seq_rpc([
        ("getSignatureStatuses", {"value": [{"slot": 100, "err": None,
                                              "confirmationStatus": "confirmed"}]}),
        ("getTransaction", {"meta": {"fee": 5000}}),
    ])
    v = TxVerifier(rpc=rpc, timeout_seconds=1, poll_interval=0.01)
    r = v.verify("SIG")
    assert r.included is True
    assert r.reverted is False
    assert r.dropped is False
    assert r.confirmation_slot == 100
    assert r.fee_paid_lamports == 5000


def test_confirmed_with_err_is_reverted():
    rpc = _seq_rpc([
        ("getSignatureStatuses", {"value": [{"slot": 200, "err": {"InstructionError": [0, "Custom"]},
                                              "confirmationStatus": "confirmed"}]}),
    ])
    v = TxVerifier(rpc=rpc, timeout_seconds=1, poll_interval=0.01)
    r = v.verify("SIG")
    assert r.included is True
    assert r.reverted is True
    assert r.confirmation_slot == 200


def test_timeout_is_dropped():
    rpc = MagicMock()
    rpc._call = MagicMock(return_value={"value": [None]})   # always "not yet seen"
    v = TxVerifier(rpc=rpc, timeout_seconds=0.05, poll_interval=0.01)
    r = v.verify("SIG")
    assert r.dropped is True
    assert r.included is False


def test_get_transaction_failure_still_returns_included():
    # If getTransaction itself fails, we still know the tx was confirmed — we
    # just won't have a fee number.  Don't let that mark it as dropped.
    rpc = MagicMock()
    calls = {"n": 0}

    def _call(method, params):
        calls["n"] += 1
        if method == "getSignatureStatuses":
            return {"value": [{"slot": 300, "err": None,
                                "confirmationStatus": "finalized"}]}
        if method == "getTransaction":
            raise RuntimeError("rpc busy")
        return None

    rpc._call = MagicMock(side_effect=_call)
    v = TxVerifier(rpc=rpc, timeout_seconds=1, poll_interval=0.01)
    r = v.verify("SIG")
    assert r.included is True
    assert r.reverted is False
    assert r.fee_paid_lamports == 0
