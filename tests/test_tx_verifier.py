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


# ---------------------------------------------------------------------------
# Realized-profit parsing from balance deltas (Phase 3b).
# ---------------------------------------------------------------------------

from decimal import Decimal
from execution.verifier import _realized_profit_from_tx


class TestRealizedProfitFromTx:
    def test_native_sol_profit_adds_back_fee(self):
        """SOL profit: (post - pre) + fee. Example: 1 SOL in, 1.005 SOL out, 5000 lamport fee."""
        tx = {
            "transaction": {"message": {"accountKeys": ["WALLET", "OTHER"]}},
            "meta": {
                "fee": 5000,
                "preBalances":  [100 * 10**9,        0],    # wallet had 100 SOL
                "postBalances": [100 * 10**9 + 5_000_000 - 5000, 0],  # gained 0.005 SOL, paid 5000 lamports
            },
        }
        # delta_lamports = post - pre + fee = (100e9 + 5e6 - 5000) - 100e9 + 5000 = 5e6
        # in SOL = 0.005
        assert _realized_profit_from_tx(tx, "WALLET", None, 5000) == Decimal("0.005")

    def test_native_sol_profit_with_wsol_mint_takes_same_path(self):
        tx = {
            "transaction": {"message": {"accountKeys": ["WALLET"]}},
            "meta": {
                "fee": 0,
                "preBalances":  [10**9],            # 1 SOL
                "postBalances": [10**9 + 10**8],    # +0.1 SOL
            },
        }
        assert _realized_profit_from_tx(
            tx, "WALLET", "So11111111111111111111111111111111111111112", 0,
        ) == Decimal("0.1")

    def test_negative_profit_handles_loss(self):
        """Reverted or out-of-the-money trades show negative delta after fee."""
        tx = {
            "transaction": {"message": {"accountKeys": ["WALLET"]}},
            "meta": {
                "fee": 10000,
                "preBalances":  [10**9],
                "postBalances": [10**9 - 10000],   # exactly lost the fee, no arb profit
            },
        }
        # delta = post - pre + fee = -10000 + 10000 = 0 → profit 0.
        assert _realized_profit_from_tx(tx, "WALLET", None, 10000) == Decimal("0")

    def test_wallet_not_in_account_keys_returns_zero(self):
        tx = {
            "transaction": {"message": {"accountKeys": ["OTHER"]}},
            "meta": {"fee": 0, "preBalances": [0], "postBalances": [0]},
        }
        assert _realized_profit_from_tx(tx, "WALLET", None, 0) == Decimal("0")

    def test_spl_token_profit_uses_token_balances(self):
        """USDC base: pre=100 USDC, post=101.5 USDC → 1.5 USDC profit, no fee add-back."""
        usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        tx = {
            "transaction": {"message": {"accountKeys": ["WALLET", "USDC_ATA"]}},
            "meta": {
                "fee": 5000,
                "preTokenBalances": [{
                    "accountIndex": 1, "owner": "WALLET", "mint": usdc_mint,
                    "uiTokenAmount": {"amount": "100000000", "decimals": 6},
                }],
                "postTokenBalances": [{
                    "accountIndex": 1, "owner": "WALLET", "mint": usdc_mint,
                    "uiTokenAmount": {"amount": "101500000", "decimals": 6},
                }],
            },
        }
        assert _realized_profit_from_tx(tx, "WALLET", usdc_mint, 5000) == Decimal("1.5")

    def test_spl_skips_other_owners(self):
        """Ignore token balance entries for other owners."""
        usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        tx = {
            "transaction": {"message": {"accountKeys": ["WALLET"]}},
            "meta": {
                "fee": 0,
                "preTokenBalances": [{
                    "accountIndex": 0, "owner": "OTHER", "mint": usdc_mint,
                    "uiTokenAmount": {"amount": "100", "decimals": 6},
                }],
                "postTokenBalances": [{
                    "accountIndex": 0, "owner": "OTHER", "mint": usdc_mint,
                    "uiTokenAmount": {"amount": "200", "decimals": 6},
                }],
            },
        }
        assert _realized_profit_from_tx(tx, "WALLET", usdc_mint, 0) == Decimal("0")

    def test_dict_style_account_keys_supported(self):
        """Post-MessageV0 getTransaction may return accountKeys as list[dict]."""
        tx = {
            "transaction": {"message": {"accountKeys": [
                {"pubkey": "WALLET", "signer": True, "writable": True},
            ]}},
            "meta": {"fee": 5000, "preBalances": [10**9], "postBalances": [10**9 + 5000]},
        }
        # delta = 5000 + fee (5000) = 10000 lamports = 0.00001 SOL
        assert _realized_profit_from_tx(tx, "WALLET", None, 5000) == Decimal("0.00001")


class TestVerifyPopulatesRealizedProfit:
    def test_verify_with_wallet_and_mint_populates_actual_profit_base(self):
        tx = {
            "transaction": {"message": {"accountKeys": ["WALLET"]}},
            "meta": {
                "fee": 5000,
                "preBalances":  [10**9],
                "postBalances": [10**9 + 5_000_000 - 5000],
            },
        }
        rpc = _seq_rpc([
            ("getSignatureStatuses", {"value": [{"slot": 500, "err": None,
                                                  "confirmationStatus": "finalized"}]}),
            ("getTransaction", tx),
        ])
        v = TxVerifier(rpc=rpc, timeout_seconds=1, poll_interval=0.01)
        r = v.verify("SIG", wallet_pubkey="WALLET", base_mint=None)
        assert r.included is True
        assert r.actual_profit_base == Decimal("0.005")
        assert r.fee_paid_lamports == 5000

    def test_verify_without_wallet_leaves_profit_zero(self):
        """Legacy call-site without wallet_pubkey — backward compat, profit=0."""
        tx = {
            "transaction": {"message": {"accountKeys": ["WALLET"]}},
            "meta": {"fee": 5000, "preBalances": [10**9], "postBalances": [10**9 + 1]},
        }
        rpc = _seq_rpc([
            ("getSignatureStatuses", {"value": [{"slot": 500, "err": None,
                                                  "confirmationStatus": "confirmed"}]}),
            ("getTransaction", tx),
        ])
        v = TxVerifier(rpc=rpc, timeout_seconds=1, poll_interval=0.01)
        r = v.verify("SIG")   # no wallet/mint args → no delta parsing
        assert r.included is True
        assert r.actual_profit_base == Decimal("0")
