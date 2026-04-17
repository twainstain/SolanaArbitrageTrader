"""RpcSubmitter tests — mocked RPC, no network."""

from __future__ import annotations

from unittest.mock import MagicMock

from execution.submitter import RpcSubmitter


def _rpc(result):
    rpc = MagicMock()
    rpc._call = MagicMock(return_value=result)
    return rpc


def test_returns_submission_ref_with_signature():
    sub = RpcSubmitter(rpc=_rpc("5SigBase58"), signed_tx_provider=lambda opp: b"bytes")
    ref = sub.submit_raw(b"bytes")
    assert ref.signature == "5SigBase58"
    assert ref.kind == "rpc"
    assert ref.metadata["preflight_skipped"] is True


def test_submit_propagates_rpc_error():
    rpc = MagicMock()
    rpc._call = MagicMock(side_effect=RuntimeError("rate limited"))
    sub = RpcSubmitter(rpc=rpc, signed_tx_provider=lambda opp: b"bytes")
    import pytest
    with pytest.raises(RuntimeError, match="rate limited"):
        sub.submit_raw(b"bytes")


def test_metadata_includes_opportunity_fields():
    from core.models import Opportunity
    from decimal import Decimal
    D = Decimal
    opp = Opportunity(
        pair="SOL/USDC", buy_venue="V1", sell_venue="V2",
        trade_size=D("1"), cost_to_buy_quote=D("165"),
        proceeds_from_sell_quote=D("166"),
        gross_profit_quote=D("1"), net_profit_quote=D("1"),
        net_profit_base=D("0.009"),
    )
    sub = RpcSubmitter(rpc=_rpc("SIG"), signed_tx_provider=lambda o: b"bytes")
    ref = sub.submit_raw(b"bytes", opportunity=opp)
    assert ref.metadata["pair"] == "SOL/USDC"
    assert ref.metadata["buy_venue"] == "V1"
    assert ref.metadata["sell_venue"] == "V2"
