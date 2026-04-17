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


# ---------------------------------------------------------------------------
# Address Lookup Table fetcher (Phase 3c).
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock, patch
from solders.pubkey import Pubkey

from market.solana_rpc import (
    AccountInfo,
    SolanaRPC,
    parse_alt_addresses,
    _LOOKUP_TABLE_META_SIZE,
    _ALT_PROGRAM_ID,
)


def _fake_alt_bytes(addresses: list[Pubkey]) -> bytes:
    """Build a valid-enough ALT account body for parser tests."""
    header = bytes(_LOOKUP_TABLE_META_SIZE)
    return header + b"".join(bytes(p) for p in addresses)


class TestParseAltAddresses:
    def test_empty_when_data_shorter_than_header(self):
        assert parse_alt_addresses(b"\x00" * 10) == []

    def test_empty_table_returns_empty(self):
        assert parse_alt_addresses(bytes(_LOOKUP_TABLE_META_SIZE)) == []

    def test_decodes_packed_addresses(self):
        addrs = [Pubkey.new_unique() for _ in range(3)]
        parsed = parse_alt_addresses(_fake_alt_bytes(addrs))
        assert parsed == addrs

    def test_partial_pubkey_at_tail_returns_empty(self):
        addrs = [Pubkey.new_unique()]
        data = _fake_alt_bytes(addrs) + b"\x00\x01\x02"    # 3 extra bytes
        assert parse_alt_addresses(data) == []


class TestGetAddressLookupTables:
    def test_empty_input_returns_empty(self):
        rpc = SolanaRPC(url="http://localhost/nope")
        rpc.get_multiple_accounts = MagicMock(return_value=[])
        assert rpc.get_address_lookup_tables([]) == []

    def test_missing_account_is_skipped(self):
        rpc = SolanaRPC(url="http://localhost/nope")
        rpc.get_multiple_accounts = MagicMock(return_value=[None])
        assert rpc.get_address_lookup_tables(["11111111111111111111111111111111"]) == []

    def test_wrong_owner_is_skipped(self):
        rpc = SolanaRPC(url="http://localhost/nope")
        rpc.get_multiple_accounts = MagicMock(return_value=[
            AccountInfo(
                pubkey="11111111111111111111111111111111",
                owner="22222222222222222222222222222222",      # not ALT program
                lamports=0,
                data=_fake_alt_bytes([Pubkey.new_unique()]),
            ),
        ])
        assert rpc.get_address_lookup_tables(["11111111111111111111111111111111"]) == []

    def test_valid_alt_is_returned(self):
        rpc = SolanaRPC(url="http://localhost/nope")
        addrs = [Pubkey.new_unique() for _ in range(2)]
        alt_key = "11111111111111111111111111111111"
        rpc.get_multiple_accounts = MagicMock(return_value=[
            AccountInfo(
                pubkey=alt_key,
                owner=_ALT_PROGRAM_ID,
                lamports=0,
                data=_fake_alt_bytes(addrs),
            ),
        ])
        result = rpc.get_address_lookup_tables([alt_key])
        assert len(result) == 1
        assert str(result[0].key) == alt_key
        assert list(result[0].addresses) == addrs

    def test_mixed_keys_drop_invalid_keep_valid(self):
        rpc = SolanaRPC(url="http://localhost/nope")
        good_addrs = [Pubkey.new_unique()]
        rpc.get_multiple_accounts = MagicMock(return_value=[
            None,                                               # missing
            AccountInfo(
                pubkey="11111111111111111111111111111111",
                owner="OTHER",
                lamports=0,
                data=_fake_alt_bytes([Pubkey.new_unique()]),
            ),                                                   # wrong owner
            AccountInfo(
                pubkey="11111111111111111111111111111111",
                owner=_ALT_PROGRAM_ID,
                lamports=0,
                data=_fake_alt_bytes(good_addrs),
            ),                                                   # valid
            AccountInfo(
                pubkey="11111111111111111111111111111111",
                owner=_ALT_PROGRAM_ID,
                lamports=0,
                data=bytes(_LOOKUP_TABLE_META_SIZE),              # empty ALT → drop
            ),
        ])
        result = rpc.get_address_lookup_tables(["k1", "k2", "11111111111111111111111111111111", "k4"])
        assert len(result) == 1
        assert list(result[0].addresses) == good_addrs


# ---------------------------------------------------------------------------
# Multi-endpoint failover (Phase perf).
# ---------------------------------------------------------------------------


class TestRpcFailover:
    def test_single_endpoint_behaves_like_before(self):
        rpc = SolanaRPC(url="http://primary/")
        # Mock the session's post so we can control the response.
        def _ok_post(url, json=None, timeout=None):
            r = MagicMock()
            r.status_code = 200
            r.raise_for_status = MagicMock()
            r.json = MagicMock(return_value={"jsonrpc": "2.0", "id": 1, "result": 42})
            return r
        rpc._session.post = MagicMock(side_effect=_ok_post)
        assert rpc._call("getFoo", []) == 42

    def test_rotates_on_network_error(self):
        import requests as _r
        rpc = SolanaRPC(urls=["http://primary/", "http://fallback/"])
        calls: list[str] = []
        def _post(url, json=None, timeout=None):
            calls.append(url)
            if url == "http://primary/":
                raise _r.ConnectionError("connection refused")
            r = MagicMock()
            r.status_code = 200
            r.raise_for_status = MagicMock()
            r.json = MagicMock(return_value={"jsonrpc": "2.0", "id": 1, "result": "rescued"})
            return r
        rpc._session.post = MagicMock(side_effect=_post)
        assert rpc._call("getFoo", []) == "rescued"
        assert calls == ["http://primary/", "http://fallback/"]
        # Primary got one error charged; fallback clean.
        assert rpc._endpoint_errors == [1, 0]
        # After success on fallback, the sticky URL is fallback.
        assert rpc.url == "http://fallback/"

    def test_all_endpoints_fail_raises(self):
        import requests as _r
        rpc = SolanaRPC(urls=["http://a/", "http://b/"])
        rpc._session.post = MagicMock(side_effect=_r.ConnectionError("boom"))
        try:
            rpc._call("getFoo", [])
        except RuntimeError as exc:
            assert "all 2 endpoint(s) failed" in str(exc)
        else:
            assert False, "expected RuntimeError when every endpoint fails"
        assert rpc._endpoint_errors == [1, 1]

    def test_jsonrpc_error_does_not_rotate(self):
        """A server-level JSON-RPC error (bad params, etc.) is a request bug,
        not an endpoint problem — don't rotate to fallback."""
        rpc = SolanaRPC(urls=["http://a/", "http://b/"])
        def _post(url, json=None, timeout=None):
            r = MagicMock()
            r.status_code = 200
            r.raise_for_status = MagicMock()
            r.json = MagicMock(return_value={"error": {"code": -32602, "message": "invalid params"}})
            return r
        rpc._session.post = MagicMock(side_effect=_post)
        try:
            rpc._call("getFoo", [])
        except RuntimeError as exc:
            assert "invalid params" in str(exc)
        else:
            assert False, "expected RuntimeError"
        # No failover attempted.
        assert rpc._endpoint_errors == [0, 0]
