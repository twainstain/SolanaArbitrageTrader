"""Solana RPC helper tests (mocked HTTP, no network)."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest

from market.solana_rpc import SolanaRPC, parse_spl_token_amount


def _mock_response(result: dict) -> MagicMock:
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json = MagicMock(return_value={"jsonrpc": "2.0", "id": 1, "result": result})
    return r


def test_get_multiple_accounts_decodes_base64():
    rpc = SolanaRPC(url="http://mock")
    payload = {
        "context": {"slot": 12345},
        "value": [
            {
                "lamports": 1_000_000,
                "owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                "data": [base64.b64encode(b"hello world").decode(), "base64"],
                "executable": False,
                "rentEpoch": 0,
            },
            None,
        ],
    }
    rpc._session.post = MagicMock(return_value=_mock_response(payload))
    accounts = rpc.get_multiple_accounts(["pk1", "pk2"])
    assert len(accounts) == 2
    assert accounts[0] is not None
    assert accounts[0].data == b"hello world"
    assert accounts[0].slot == 12345
    assert accounts[0].lamports == 1_000_000
    assert accounts[1] is None


def test_get_multiple_accounts_batches_over_100():
    rpc = SolanaRPC(url="http://mock")
    # 150 pubkeys → 2 batches (100 + 50)
    call_count = {"n": 0}

    def post(url, json, timeout):
        call_count["n"] += 1
        batch_size = len(json["params"][0])
        return _mock_response({
            "context": {"slot": 1},
            "value": [None] * batch_size,
        })

    rpc._session.post = MagicMock(side_effect=post)
    rpc.get_multiple_accounts([f"pk{i}" for i in range(150)])
    assert call_count["n"] == 2


def test_rpc_error_raises():
    rpc = SolanaRPC(url="http://mock")
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json = MagicMock(return_value={"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "boom"}})
    rpc._session.post = MagicMock(return_value=r)
    with pytest.raises(RuntimeError, match="boom"):
        rpc.get_slot()


def test_parse_spl_token_amount():
    # SPL token account: 64 bytes of (mint+owner), then 8-byte u64 amount.
    data = b"\x00" * 64 + (12345678).to_bytes(8, "little") + b"\x00" * 100
    assert parse_spl_token_amount(data) == 12345678


def test_parse_spl_token_amount_too_short():
    with pytest.raises(ValueError):
        parse_spl_token_amount(b"\x00" * 32)
