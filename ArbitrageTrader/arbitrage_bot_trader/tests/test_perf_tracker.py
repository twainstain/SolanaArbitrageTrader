from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arbitrage_bot.perf_tracker import PerfReport, analyze_jsonl, analyze_all_logs


def _make_jsonl(records: list[dict]) -> str:
    """Write records to a temp JSONL file and return the path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, prefix="bot_")
    for r in records:
        f.write(json.dumps(r) + "\n")
    f.flush()
    f.close()
    return f.name


def _sample_scan(index: int, decision: str, net_profit: float = 0.01, flags: list = None) -> dict:
    opp = {
        "pair": "WETH/USDC", "buy_dex": "A", "sell_dex": "B",
        "net_profit_base": net_profit, "warning_flags": flags or [],
    } if decision != "no_opportunity" else None
    return {
        "event": "scan", "scan_index": index,
        "opportunity": opp, "decision": decision,
        "quotes": [], "timestamp": "2026-04-13T00:00:00",
    }


def _sample_execution(index: int, success: bool, realized: float = 0.01) -> dict:
    return {
        "event": "execution", "scan_index": index,
        "success": success, "realized_profit_base": realized if success else 0.0,
        "reason": "tx:0xabc" if success else "tx_reverted:0xdef",
        "opportunity": {"pair": "WETH/USDC"},
    }


class PerfReportPropertyTests(unittest.TestCase):
    def test_hit_rate(self) -> None:
        r = PerfReport(total_scans=10, opportunities_found=3)
        self.assertAlmostEqual(r.hit_rate, 0.3)

    def test_hit_rate_zero_scans(self) -> None:
        r = PerfReport(total_scans=0, opportunities_found=0)
        self.assertEqual(r.hit_rate, 0.0)

    def test_execution_success_rate(self) -> None:
        r = PerfReport(execution_success=8, execution_failed=2)
        self.assertAlmostEqual(r.execution_success_rate, 0.8)

    def test_revert_rate(self) -> None:
        r = PerfReport(execution_success=9, execution_failed=1)
        self.assertAlmostEqual(r.revert_rate, 0.1)

    def test_pnl_accuracy(self) -> None:
        r = PerfReport(total_expected_profit=1.0, total_realized_profit=0.85)
        self.assertAlmostEqual(r.pnl_accuracy, 0.85)

    def test_profit_per_scan(self) -> None:
        r = PerfReport(total_scans=100, total_realized_profit=0.5)
        self.assertAlmostEqual(r.profit_per_scan, 0.005)

    def test_to_dict_includes_derived(self) -> None:
        r = PerfReport(total_scans=10, opportunities_found=5, execution_success=3, execution_failed=1)
        d = r.to_dict()
        self.assertIn("hit_rate", d)
        self.assertIn("revert_rate", d)
        self.assertIn("pnl_accuracy", d)


class AnalyzeJsonlTests(unittest.TestCase):
    def test_counts_scans_and_opportunities(self) -> None:
        path = _make_jsonl([
            _sample_scan(1, "executed", 0.02),
            _sample_execution(1, True, 0.02),
            _sample_scan(2, "no_opportunity"),
            _sample_scan(3, "dry_run_skip", 0.01),
        ])
        report = analyze_jsonl(path)
        self.assertEqual(report.total_scans, 3)
        self.assertEqual(report.opportunities_found, 2)
        self.assertEqual(report.no_opportunity_scans, 1)
        self.assertEqual(report.dry_run_skipped, 1)

    def test_tracks_execution_success_and_failure(self) -> None:
        path = _make_jsonl([
            _sample_scan(1, "executed", 0.02),
            _sample_execution(1, True, 0.02),
            _sample_scan(2, "executed", 0.01),
            _sample_execution(2, False),
        ])
        report = analyze_jsonl(path)
        self.assertEqual(report.execution_success, 1)
        self.assertEqual(report.execution_failed, 1)
        self.assertAlmostEqual(report.total_realized_profit, 0.02)

    def test_tracks_expected_profit(self) -> None:
        path = _make_jsonl([
            _sample_scan(1, "executed", 0.05),
            _sample_execution(1, True, 0.04),
            _sample_scan(2, "executed", 0.03),
            _sample_execution(2, True, 0.025),
        ])
        report = analyze_jsonl(path)
        self.assertAlmostEqual(report.total_expected_profit, 0.08)
        self.assertAlmostEqual(report.total_realized_profit, 0.065)
        self.assertAlmostEqual(report.max_single_profit, 0.05)

    def test_counts_warning_flags(self) -> None:
        path = _make_jsonl([
            _sample_scan(1, "executed", 0.01, flags=["low_liquidity", "stale_quote"]),
            _sample_scan(2, "executed", 0.01, flags=["low_liquidity"]),
            _sample_scan(3, "no_opportunity"),
        ])
        report = analyze_jsonl(path)
        self.assertEqual(report.flag_counts["low_liquidity"], 2)
        self.assertEqual(report.flag_counts["stale_quote"], 1)

    def test_empty_file_returns_zeros(self) -> None:
        path = _make_jsonl([])
        report = analyze_jsonl(path)
        self.assertEqual(report.total_scans, 0)
        self.assertEqual(report.hit_rate, 0.0)

    def test_nonexistent_file_returns_zeros(self) -> None:
        report = analyze_jsonl("/tmp/does_not_exist_12345.jsonl")
        self.assertEqual(report.total_scans, 0)


class AnalyzeAllLogsTests(unittest.TestCase):
    def test_combines_multiple_files(self) -> None:
        import tempfile, os
        tmpdir = tempfile.mkdtemp()

        # Write two log files.
        for i, name in enumerate(["bot_2026-01-01_00-00-00.jsonl", "bot_2026-01-02_00-00-00.jsonl"]):
            path = os.path.join(tmpdir, name)
            with open(path, "w") as f:
                f.write(json.dumps(_sample_scan(1, "executed", 0.01)) + "\n")
                f.write(json.dumps(_sample_execution(1, True, 0.01)) + "\n")

        report = analyze_all_logs(tmpdir)
        self.assertEqual(report.total_scans, 2)
        self.assertEqual(report.execution_success, 2)
        self.assertAlmostEqual(report.total_realized_profit, 0.02)


if __name__ == "__main__":
    unittest.main()
