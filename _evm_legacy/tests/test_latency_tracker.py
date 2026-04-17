"""Tests for the latency tracker and analysis."""

import json
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from observability.latency_tracker import LatencyTracker, analyze_latency


class LatencyTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, prefix="latency_")
        self.tracker = LatencyTracker(output_path=self.tmp.name)

    def tearDown(self):
        self.tracker.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_records_pipeline(self):
        self.tracker.start_scan()
        self.tracker.mark("rpc_fetch")
        self.tracker.record_pipeline(
            opp_id="opp_test123", pair="WETH/USDC", chain="ethereum",
            buy_dex="Uniswap", sell_dex="Sushi",
            spread_pct=0.15, net_profit=0.002,
            status="rejected",
            pipeline_timings={"detect_ms": "0.5", "price_ms": "0.3", "total_ms": "1.2"},
        )
        # Read file.
        lines = Path(self.tmp.name).read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["opp_id"], "opp_test123")
        self.assertEqual(record["chain"], "ethereum")
        self.assertEqual(record["spread_pct"], 0.15)
        self.assertIn("rpc_fetch", record["scan_marks_ms"])
        self.assertIn("detect_ms", record["pipeline_ms"])

    def test_records_scan_summary(self):
        self.tracker.start_scan()
        self.tracker.mark("rpc_fetch")
        self.tracker.mark("scanner")
        self.tracker.record_scan_summary(quote_count=10, opp_count=5)
        lines = Path(self.tmp.name).read_text().strip().splitlines()
        record = json.loads(lines[0])
        self.assertEqual(record["type"], "scan_summary")
        self.assertEqual(record["quote_count"], 10)
        self.assertIn("rpc_fetch", record["scan_marks_ms"])

    def test_multiple_scans(self):
        for i in range(3):
            self.tracker.start_scan()
            self.tracker.record_pipeline(
                opp_id=f"opp_{i}", pair="WETH/USDC", chain="ethereum",
                buy_dex="Uni", sell_dex="Sushi",
                spread_pct=float(i), net_profit=0.001,
                status="rejected", pipeline_timings={"total_ms": str(i)},
            )
        lines = Path(self.tmp.name).read_text().strip().splitlines()
        self.assertEqual(len(lines), 3)

    def test_scan_index_increments(self):
        self.tracker.start_scan()
        self.tracker.record_pipeline(
            opp_id="opp_1", pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_pct=1.0, net_profit=0.01,
            status="rejected", pipeline_timings={"total_ms": "1"},
        )
        self.tracker.start_scan()
        self.tracker.record_pipeline(
            opp_id="opp_2", pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_pct=2.0, net_profit=0.02,
            status="rejected", pipeline_timings={"total_ms": "2"},
        )
        lines = Path(self.tmp.name).read_text().strip().splitlines()
        r1 = json.loads(lines[0])
        r2 = json.loads(lines[1])
        self.assertEqual(r1["scan_index"], 1)
        self.assertEqual(r2["scan_index"], 2)

    def test_marks_are_cumulative(self):
        self.tracker.start_scan()
        import time
        self.tracker.mark("step1")
        self.tracker.mark("step2")
        self.tracker.record_scan_summary(quote_count=5, opp_count=1)
        lines = Path(self.tmp.name).read_text().strip().splitlines()
        record = json.loads(lines[0])
        marks = record["scan_marks_ms"]
        # step2 should be >= step1 (cumulative from scan start)
        self.assertGreaterEqual(marks["step2"], marks["step1"])

    def test_per_chain_tracking(self):
        self.tracker.start_scan()
        for chain in ["ethereum", "arbitrum", "optimism"]:
            self.tracker.record_pipeline(
                opp_id=f"opp_{chain}", pair="WETH/USDC", chain=chain,
                buy_dex="Uni", sell_dex="Sushi",
                spread_pct=1.0, net_profit=0.005,
                status="rejected", pipeline_timings={"total_ms": "1"},
            )
        lines = Path(self.tmp.name).read_text().strip().splitlines()
        chains = [json.loads(l)["chain"] for l in lines]
        self.assertEqual(chains, ["ethereum", "arbitrum", "optimism"])


class AnalyzeLatencyTests(unittest.TestCase):
    def test_analyze_empty_file(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        Path(tmp.name).write_text("")
        # Should not crash.
        analyze_latency(tmp.name)
        Path(tmp.name).unlink(missing_ok=True)

    def test_analyze_with_data(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w")
        for i in range(5):
            record = {
                "opp_id": f"opp_{i}", "pair": "WETH/USDC",
                "chain": "ethereum" if i < 3 else "arbitrum",
                "buy_dex": "Uni", "sell_dex": "Sushi",
                "spread_pct": 0.1 * i, "net_profit": 0.001 * i,
                "status": "rejected",
                "scan_marks_ms": {"rpc_fetch": 100 + i * 10},
                "pipeline_ms": {"detect_ms": 0.5, "price_ms": 0.3, "total_ms": 1.0 + i * 0.5},
                "total_scan_to_result_ms": 150 + i * 20,
            }
            tmp.write(json.dumps(record) + "\n")
        tmp.flush()
        # Should print report without crashing.
        analyze_latency(tmp.name)
        Path(tmp.name).unlink(missing_ok=True)

    def test_analyze_nonexistent_file(self):
        # Should not crash.
        analyze_latency("/tmp/nonexistent_latency_file_12345.jsonl")


if __name__ == "__main__":
    unittest.main()
