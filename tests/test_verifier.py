"""Tests for on-chain result verifier and PnL reconciliation."""

import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pipeline.verifier import OnChainVerifier, PnLReconciler

D = Decimal


from typing import Optional, List

def _make_receipt(
    status: int = 1,
    gas_used: int = 150_000,
    effective_gas_price: int = 30_000_000_000,  # 30 gwei
    block_number: int = 12345,
    logs: Optional[List] = None,
) -> dict:
    """Build a mock transaction receipt."""
    return {
        "status": status,
        "gasUsed": gas_used,
        "effectiveGasPrice": effective_gas_price,
        "blockNumber": block_number,
        "logs": logs or [],
    }


def _make_transfer_log(from_addr: str, to_addr: str, amount_raw: int) -> dict:
    """Build a mock ERC-20 Transfer log entry."""
    from web3 import Web3
    topic0 = Web3.keccak(text="Transfer(address,address,uint256)")
    # Pad addresses to 32 bytes (topics are 32 bytes).
    from_topic = bytes.fromhex(from_addr.lower().replace("0x", "").zfill(64))
    to_topic = bytes.fromhex(to_addr.lower().replace("0x", "").zfill(64))
    data = amount_raw.to_bytes(32, "big")
    return {
        "topics": [topic0, from_topic, to_topic],
        "data": data,
    }


class OnChainVerifierTests(unittest.TestCase):
    def setUp(self):
        self.w3 = MagicMock()
        self.contract = "0x1234567890abcdef1234567890abcdef12345678"
        self.verifier = OnChainVerifier(
            w3=self.w3, contract_address=self.contract, quote_decimals=6,
        )

    def test_included_and_not_reverted(self):
        receipt = _make_receipt(status=1, gas_used=150_000)
        self.w3.eth.get_transaction_receipt.return_value = receipt

        included, reverted, gas_used, profit = self.verifier.verify("0xabc")
        self.assertTrue(included)
        self.assertFalse(reverted)
        self.assertEqual(gas_used, 150_000)

    def test_reverted_transaction(self):
        receipt = _make_receipt(status=0, gas_used=21_000)
        self.w3.eth.get_transaction_receipt.return_value = receipt

        included, reverted, gas_used, profit = self.verifier.verify("0xdef")
        self.assertTrue(included)
        self.assertTrue(reverted)
        self.assertEqual(profit, D("0"))

    def test_receipt_not_found(self):
        self.w3.eth.get_transaction_receipt.return_value = None

        included, reverted, gas_used, profit = self.verifier.verify("0x000")
        self.assertFalse(included)
        self.assertFalse(reverted)
        self.assertEqual(gas_used, 0)
        self.assertEqual(profit, D("0"))

    def test_receipt_fetch_error(self):
        self.w3.eth.get_transaction_receipt.side_effect = Exception("RPC down")

        included, reverted, gas_used, profit = self.verifier.verify("0xfail")
        self.assertFalse(included)
        self.assertEqual(profit, D("0"))

    def test_extracts_profit_from_transfer_logs(self):
        # Transfer 100 USDC (6 decimals) to the contract.
        log = _make_transfer_log(
            from_addr="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            to_addr=self.contract,
            amount_raw=100_000_000,  # 100 USDC
        )
        receipt = _make_receipt(
            status=1, gas_used=200_000,
            effective_gas_price=0,  # zero gas for cleaner profit check
            logs=[log],
        )
        self.w3.eth.get_transaction_receipt.return_value = receipt

        included, reverted, gas_used, profit = self.verifier.verify("0xprofit")
        self.assertTrue(included)
        self.assertEqual(gas_used, 200_000)
        self.assertEqual(profit, D("100"))

    def test_gas_cost_subtracted_from_profit(self):
        receipt = _make_receipt(
            status=1, gas_used=100_000,
            effective_gas_price=50_000_000_000,  # 50 gwei
            logs=[],
        )
        self.w3.eth.get_transaction_receipt.return_value = receipt

        included, reverted, gas_used, profit = self.verifier.verify("0xgas")
        # Gas cost = 100_000 * 50 gwei = 5_000_000 gwei = 0.005 ETH
        expected_gas_cost = D("100000") * D("50000000000") / D(10**18)
        self.assertEqual(profit, -expected_gas_cost)


class PnLReconcilerTests(unittest.TestCase):
    def setUp(self):
        self.reconciler = PnLReconciler(deviation_threshold_pct=20.0)

    def test_no_deviation(self):
        report = self.reconciler.reconcile(
            opp_id="opp_123",
            expected_profit=D("0.01"),
            actual_profit=D("0.01"),
            gas_used=150_000,
            estimated_gas=D("150000"),
        )
        self.assertEqual(report["deviation_pct"], 0.0)
        self.assertFalse(report["significant_deviation"])

    def test_small_deviation_not_flagged(self):
        report = self.reconciler.reconcile(
            opp_id="opp_124",
            expected_profit=D("0.01"),
            actual_profit=D("0.009"),  # 10% less
            gas_used=150_000,
            estimated_gas=D("150000"),
        )
        self.assertEqual(report["deviation_pct"], -10.0)
        self.assertFalse(report["significant_deviation"])

    def test_large_deviation_flagged(self):
        report = self.reconciler.reconcile(
            opp_id="opp_125",
            expected_profit=D("0.01"),
            actual_profit=D("0.005"),  # 50% less
            gas_used=150_000,
            estimated_gas=D("150000"),
        )
        self.assertEqual(report["deviation_pct"], -50.0)
        self.assertTrue(report["significant_deviation"])

    def test_positive_deviation(self):
        report = self.reconciler.reconcile(
            opp_id="opp_126",
            expected_profit=D("0.01"),
            actual_profit=D("0.015"),  # 50% more
            gas_used=100_000,
            estimated_gas=D("150000"),
        )
        self.assertEqual(report["deviation_pct"], 50.0)
        self.assertTrue(report["significant_deviation"])

    def test_zero_expected_profit(self):
        report = self.reconciler.reconcile(
            opp_id="opp_127",
            expected_profit=D("0"),
            actual_profit=D("0.001"),
            gas_used=150_000,
            estimated_gas=D("150000"),
        )
        self.assertEqual(report["deviation_pct"], 0.0)

    def test_gas_deviation_tracked(self):
        report = self.reconciler.reconcile(
            opp_id="opp_128",
            expected_profit=D("0.01"),
            actual_profit=D("0.01"),
            gas_used=200_000,
            estimated_gas=D("150000"),
        )
        self.assertAlmostEqual(report["gas_deviation_pct"], 33.33, places=1)

    def test_summary_aggregation(self):
        self.reconciler.reconcile("a", D("0.01"), D("0.01"), 100, D("100"))
        self.reconciler.reconcile("b", D("0.01"), D("0.005"), 100, D("100"))
        self.reconciler.reconcile("c", D("0.01"), D("0.012"), 100, D("100"))

        summary = self.reconciler.summary
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["significant_deviations"], 1)

    def test_recent_reconciliations_capped(self):
        for i in range(110):
            self.reconciler.reconcile(f"opp_{i}", D("1"), D("1"), 100, D("100"))
        self.assertEqual(len(self.reconciler.recent_reconciliations), 100)


class ScanMarksSnapshotTests(unittest.TestCase):
    """Test that scan_marks are properly snapshotted for the consumer thread."""

    def test_queued_candidate_carries_scan_marks(self):
        from pipeline.queue import CandidateQueue, QueuedCandidate
        from models import Opportunity, ZERO

        opp = Opportunity(
            pair="WETH/USDC", buy_dex="Uni", sell_dex="Sushi",
            trade_size=D("1"), cost_to_buy_quote=D("2200"),
            proceeds_from_sell_quote=D("2210"),
            gross_profit_quote=D("10"), net_profit_quote=D("5"),
            net_profit_base=D("0.002"), gross_spread_pct=D("0.45"),
            dex_fee_cost_quote=D("2"), flash_loan_fee_quote=D("1"),
            slippage_cost_quote=D("0.5"), gas_cost_base=D("0.001"),
            is_actionable=True,
        )

        marks = {"rpc_fetch": 1234.56, "scanner": 1235.00}
        q = CandidateQueue(max_size=10)
        q.push(opp, priority=1.0, scan_marks=marks)

        candidate = q.pop()
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.scan_marks["rpc_fetch"], 1234.56)
        self.assertEqual(candidate.scan_marks["scanner"], 1235.00)

    def test_latency_tracker_get_scan_marks(self):
        import tempfile
        from observability.latency_tracker import LatencyTracker

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            tracker = LatencyTracker(output_path=f.name)

        tracker.start_scan()
        # Simulate marks.
        tracker.mark("rpc_fetch")
        tracker.mark("scanner")

        marks = tracker.get_scan_marks()
        self.assertIn("rpc_fetch", marks)
        self.assertIn("scanner", marks)
        self.assertGreaterEqual(marks["rpc_fetch"], 0)

        # Verify it's a copy — new scan shouldn't affect snapshot.
        tracker.start_scan()
        self.assertIn("rpc_fetch", marks)  # snapshot unchanged
        tracker.close()
        Path(f.name).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
