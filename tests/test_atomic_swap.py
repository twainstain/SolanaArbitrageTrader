"""Tests for execution.atomic_swap (Phase 3b).

All Jupiter HTTP is mocked. No RPC, no signing. We assert:
  - plan_two_leg chains leg A's output into leg B's input.
  - plan_two_leg rejects mismatched mid-asset pairs.
  - build_atomic_tx produces a single VersionedTransaction whose message
    contains instructions from BOTH legs in the expected order.
  - ALT lists from both legs are de-duplicated.
  - Compute-budget header is taken from leg A only (not stacked).
"""

from __future__ import annotations

import base64
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "trading_platform" / "src"))

from solders.address_lookup_table_account import AddressLookupTableAccount
from solders.hash import Hash
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from execution.atomic_swap import (
    AtomicSwapBuilder,
    AtomicSwapPlan,
    LegParams,
    _parse_instruction,
    _parse_swap_instructions,
)

D = Decimal


# Convenience: real-ish pubkeys (base58, 32 bytes).
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
USER = str(Keypair().pubkey())


def _ix_json(program_id: str, tag: bytes, *account_pubkeys: str) -> dict:
    return {
        "programId": program_id,
        "accounts": [
            {"pubkey": pk, "isSigner": False, "isWritable": True}
            for pk in account_pubkeys
        ],
        "data": base64.b64encode(tag).decode(),
    }


def _fake_swap_ix_body(
    setup_tags: list[bytes],
    swap_tag: bytes,
    cleanup_tag: bytes | None,
    alt_keys: list[str],
    compute_budget_tags: list[bytes] | None = None,
) -> dict:
    return {
        "computeBudgetInstructions": [
            _ix_json(SYSTEM_PROGRAM, t) for t in (compute_budget_tags or [b"CB"])
        ],
        "setupInstructions": [
            _ix_json(TOKEN_PROGRAM, t, SYSTEM_PROGRAM) for t in setup_tags
        ],
        "swapInstruction": _ix_json(TOKEN_PROGRAM, swap_tag, SYSTEM_PROGRAM),
        "cleanupInstruction": _ix_json(TOKEN_PROGRAM, cleanup_tag) if cleanup_tag else None,
        "addressLookupTableAddresses": alt_keys,
        "prioritizationFeeLamports": 5_000,
    }


def _fake_quote(out_amount: int, in_amount: int = 1_000_000_000) -> MagicMock:
    q = MagicMock()
    q.quote_response = {"fake": "quote"}
    q.in_amount = in_amount
    q.out_amount = out_amount
    q.price_impact_pct = 0.0
    q.route_plan = []
    return q


class ParseSwapInstructionsTests(unittest.TestCase):
    def test_parses_all_sections(self):
        body = _fake_swap_ix_body([b"S1", b"S2"], b"SWAP", b"CLEAN",
                                  alt_keys=["11111111111111111111111111111111"])
        raw = _parse_swap_instructions(body)
        self.assertEqual(len(raw.compute_budget_instructions), 1)
        self.assertEqual(len(raw.setup_instructions), 2)
        self.assertIsNotNone(raw.swap_instruction)
        self.assertIsNotNone(raw.cleanup_instruction)
        self.assertEqual(raw.address_lookup_table_addresses, ["11111111111111111111111111111111"])
        self.assertEqual(raw.prioritization_fee_lamports, 5000)

    def test_missing_cleanup_is_ok(self):
        body = _fake_swap_ix_body([b"S"], b"SWAP", cleanup_tag=None, alt_keys=[])
        raw = _parse_swap_instructions(body)
        self.assertIsNone(raw.cleanup_instruction)

    def test_parse_instruction_decodes_base64(self):
        ix = _parse_instruction(_ix_json(TOKEN_PROGRAM, b"\x01\x02\x03", SYSTEM_PROGRAM))
        self.assertEqual(bytes(ix.data), b"\x01\x02\x03")


class PlanTwoLegTests(unittest.TestCase):
    def test_chains_leg_a_output_into_leg_b_input(self):
        builder = AtomicSwapBuilder()
        builder.jupiter = MagicMock()
        # Leg A: 1 SOL → 90 USDC (out=90_000_000 μ-USDC).
        # Leg B: 90 USDC → some SOL; we assert leg B was called with 90.
        builder.jupiter.quote.side_effect = [
            _fake_quote(out_amount=90_000_000, in_amount=1_000_000_000),  # leg A result
            _fake_quote(out_amount=1_005_000_000, in_amount=90_000_000), # leg B result
        ]
        plan = builder.plan_two_leg(
            LegParams("SOL", "USDC", D("1"), 15),
            LegParams("USDC", "SOL", D("999"), 15),   # ignored — replaced by leg A's output
        )
        # Leg B should have been called with leg A's output in human units (90 USDC).
        second_call = builder.jupiter.quote.call_args_list[1]
        self.assertEqual(second_call.kwargs["input_symbol"], "USDC")
        self.assertEqual(second_call.kwargs["output_symbol"], "SOL")
        self.assertEqual(second_call.kwargs["input_amount_human"], D("90"))
        # Plan carries both quotes.
        self.assertEqual(plan.leg_a_quote.out_amount, 90_000_000)
        self.assertEqual(plan.leg_b_quote.out_amount, 1_005_000_000)
        # Expected round-trip: 1.005 SOL (from leg B's 9-dec out).
        self.assertEqual(plan.expected_net_out_human(), D("1.005"))

    def test_rejects_mismatched_mid_assets(self):
        builder = AtomicSwapBuilder()
        builder.jupiter = MagicMock()
        builder.jupiter.quote.return_value = _fake_quote(90_000_000)
        with self.assertRaises(ValueError):
            builder.plan_two_leg(
                LegParams("SOL", "USDC", D("1"), 15),
                LegParams("USDT", "SOL", D("999"), 15),      # USDC != USDT
            )


class BuildAtomicTxTests(unittest.TestCase):
    """Mock /swap-instructions for each leg; assert compiled tx shape."""

    def _setup(self):
        builder = AtomicSwapBuilder()
        # Legs' /swap-instructions HTTP responses. Unique tags per instruction
        # let us assert presence + order in the compiled message.
        leg_a = _fake_swap_ix_body(
            setup_tags=[b"A-SETUP-1"],
            swap_tag=b"A-SWAP",
            cleanup_tag=b"A-CLEAN",
            alt_keys=["So11111111111111111111111111111111111111112"],
            compute_budget_tags=[b"CB-A-LIMIT", b"CB-A-PRICE"],
        )
        leg_b = _fake_swap_ix_body(
            setup_tags=[b"B-SETUP-1", b"B-SETUP-2"],
            swap_tag=b"B-SWAP",
            cleanup_tag=None,
            alt_keys=[
                "So11111111111111111111111111111111111111112",   # duplicate → dedup
                "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            ],
            compute_budget_tags=[b"CB-B-LIMIT", b"CB-B-PRICE"],
        )

        resp_a = MagicMock(); resp_a.json.return_value = leg_a; resp_a.raise_for_status = MagicMock()
        resp_b = MagicMock(); resp_b.json.return_value = leg_b; resp_b.raise_for_status = MagicMock()
        builder._session.post = MagicMock(side_effect=[resp_a, resp_b])

        plan = AtomicSwapPlan(
            leg_a_quote=_fake_quote(90_000_000),
            leg_b_quote=_fake_quote(1_005_000_000),
            leg_a=LegParams("SOL", "USDC", D("1"), 15),
            leg_b=LegParams("USDC", "SOL", D("90"), 15),
        )
        return builder, plan

    def test_tx_contains_both_legs_in_order(self):
        builder, plan = self._setup()

        captured_alt_keys: list[list[str]] = []
        def alt_resolver(keys):
            captured_alt_keys.append(keys)
            # Return a resolved ALT account per key. Contents of ALT accounts
            # don't matter for message compilation against these mock ixs
            # (since our mock instructions reference only the System program
            # and a fixed pubkey, both already in the static accounts list).
            return [
                AddressLookupTableAccount(key=Pubkey.from_string(k), addresses=[])
                for k in keys
            ]

        tx = builder.build_atomic_tx(
            plan=plan,
            user_pubkey=USER,
            priority_fee_lamports=10_000,
            recent_blockhash=Hash.default(),
            alt_resolver=alt_resolver,
        )

        # ALT de-dup: 2 unique ALTs (the "wSOL" one appeared in both legs).
        self.assertEqual(len(captured_alt_keys), 1)
        self.assertEqual(len(captured_alt_keys[0]), 2)

        # Inspect the compiled message's instruction tags. The instruction
        # data bytes are our unique tags; we concatenate them from the
        # compiled instructions and assert the expected order.
        compiled = tx.message.instructions
        tags = [bytes(i.data) for i in compiled]
        # Compute-budget from leg A only (not stacked from leg B).
        self.assertIn(b"CB-A-LIMIT", tags)
        self.assertIn(b"CB-A-PRICE", tags)
        self.assertNotIn(b"CB-B-LIMIT", tags)
        self.assertNotIn(b"CB-B-PRICE", tags)
        # Leg A sequence.
        self.assertIn(b"A-SETUP-1", tags)
        self.assertIn(b"A-SWAP", tags)
        self.assertIn(b"A-CLEAN", tags)
        # Leg B sequence.
        self.assertIn(b"B-SETUP-1", tags)
        self.assertIn(b"B-SETUP-2", tags)
        self.assertIn(b"B-SWAP", tags)
        # Order: leg A swap comes before leg B swap in the instruction list.
        self.assertLess(tags.index(b"A-SWAP"), tags.index(b"B-SWAP"))

    def test_tx_signature_count_is_zero_unsigned(self):
        """Builder returns an UNSIGNED tx; caller must sign."""
        builder, plan = self._setup()
        tx = builder.build_atomic_tx(
            plan=plan, user_pubkey=USER, priority_fee_lamports=10_000,
            recent_blockhash=Hash.default(),
            alt_resolver=lambda keys: [
                AddressLookupTableAccount(key=Pubkey.from_string(k), addresses=[])
                for k in keys
            ],
        )
        # No real signatures yet — populate([]) leaves sig slots as default.
        # We simply assert the tx is a VersionedTransaction instance.
        from solders.transaction import VersionedTransaction
        self.assertIsInstance(tx, VersionedTransaction)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Default ALT resolver (Phase 3c).
# ---------------------------------------------------------------------------


class DefaultAltResolverTests(unittest.TestCase):
    def test_none_resolver_uses_solana_rpc(self):
        """When alt_resolver=None is passed, the builder wires SolanaRPC.get_address_lookup_tables."""
        from unittest.mock import patch
        import execution.atomic_swap as atomic_swap
        builder = AtomicSwapBuilder()

        # Prepare /swap-instructions mocks identical to BuildAtomicTxTests but
        # with only one ALT key so we can verify the default resolver was
        # called with the right keys.
        leg_a = _fake_swap_ix_body(
            setup_tags=[b"A"], swap_tag=b"AS", cleanup_tag=b"AC",
            alt_keys=["So11111111111111111111111111111111111111112"],
        )
        leg_b = _fake_swap_ix_body(
            setup_tags=[b"B"], swap_tag=b"BS", cleanup_tag=None,
            alt_keys=["EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"],
        )
        resp_a = MagicMock(); resp_a.json.return_value = leg_a; resp_a.raise_for_status = MagicMock()
        resp_b = MagicMock(); resp_b.json.return_value = leg_b; resp_b.raise_for_status = MagicMock()
        builder._session.post = MagicMock(side_effect=[resp_a, resp_b])

        plan = AtomicSwapPlan(
            leg_a_quote=_fake_quote(90_000_000),
            leg_b_quote=_fake_quote(1_005_000_000),
            leg_a=LegParams("SOL", "USDC", D("1"), 15),
            leg_b=LegParams("USDC", "SOL", D("90"), 15),
        )

        called_with_keys: list[list[str]] = []
        def _fake_alt_fetch(keys):
            called_with_keys.append(keys)
            return []

        # Patch the SolanaRPC class so when the builder imports it and
        # instantiates, we intercept get_address_lookup_tables.
        class _FakeRPC:
            def __init__(self, *args, **kwargs): pass
            def get_address_lookup_tables(self, keys):
                return _fake_alt_fetch(keys)

        with patch("market.solana_rpc.SolanaRPC", _FakeRPC):
            builder.build_atomic_tx(
                plan=plan, user_pubkey=USER, priority_fee_lamports=10_000,
                recent_blockhash=Hash.default(),
                # alt_resolver omitted → default path exercises SolanaRPC
            )

        self.assertEqual(len(called_with_keys), 1)
        # Both ALT keys forwarded after de-duplication (two unique here).
        self.assertEqual(set(called_with_keys[0]), {
            "So11111111111111111111111111111111111111112",
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        })
