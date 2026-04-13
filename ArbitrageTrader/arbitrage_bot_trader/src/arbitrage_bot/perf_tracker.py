"""Phase 4 performance tracker — track revert rate, realized vs expected PnL.

Reads from logs/*.jsonl and computes:
  - total scans, opportunities found, executed, skipped, reverted
  - hit rate (opportunities / scans)
  - execution success rate
  - revert rate
  - realized vs expected PnL comparison
  - profit per scan average
  - warning flag frequency

Can also be imported and used programmatically to track live performance.

Usage::

    # Analyze all log files
    PYTHONPATH=src python -m arbitrage_bot.perf_tracker

    # Analyze a specific log file
    PYTHONPATH=src python -m arbitrage_bot.perf_tracker --file logs/bot_2026-04-13_02-57-00.jsonl

    # Export to JSON
    PYTHONPATH=src python -m arbitrage_bot.perf_tracker --output data/perf_report.json
"""

from __future__ import annotations

import argparse
import glob
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

from arbitrage_bot.env import load_env
from arbitrage_bot.log import get_logger, LOG_DIR

logger = get_logger(__name__)


@dataclass
class PerfReport:
    """Aggregated performance metrics from one or more bot runs."""
    # Scan metrics
    total_scans: int = 0
    opportunities_found: int = 0
    no_opportunity_scans: int = 0

    # Execution metrics
    executed_count: int = 0
    execution_success: int = 0
    execution_failed: int = 0
    simulation_failed: int = 0
    dry_run_skipped: int = 0

    # PnL metrics
    total_expected_profit: float = 0.0
    total_realized_profit: float = 0.0
    max_single_profit: float = 0.0

    # Warning flag frequency
    flag_counts: dict = field(default_factory=dict)

    # Derived metrics (computed after aggregation)
    @property
    def hit_rate(self) -> float:
        """Fraction of scans that found an opportunity."""
        return self.opportunities_found / self.total_scans if self.total_scans > 0 else 0.0

    @property
    def execution_success_rate(self) -> float:
        """Fraction of executions that succeeded."""
        total = self.execution_success + self.execution_failed
        return self.execution_success / total if total > 0 else 0.0

    @property
    def revert_rate(self) -> float:
        """Fraction of executions that reverted (failed on-chain)."""
        total = self.execution_success + self.execution_failed
        return self.execution_failed / total if total > 0 else 0.0

    @property
    def simulation_reject_rate(self) -> float:
        """Fraction of attempted executions rejected by simulation."""
        total = self.execution_success + self.execution_failed + self.simulation_failed
        return self.simulation_failed / total if total > 0 else 0.0

    @property
    def pnl_accuracy(self) -> float:
        """Realized / expected PnL ratio (1.0 = perfect prediction)."""
        if self.total_expected_profit > 0:
            return self.total_realized_profit / self.total_expected_profit
        return 0.0

    @property
    def profit_per_scan(self) -> float:
        """Average realized profit per scan."""
        return self.total_realized_profit / self.total_scans if self.total_scans > 0 else 0.0

    def to_dict(self) -> dict:
        """Serialize to dict including derived properties."""
        d = {
            "total_scans": self.total_scans,
            "opportunities_found": self.opportunities_found,
            "no_opportunity_scans": self.no_opportunity_scans,
            "executed_count": self.executed_count,
            "execution_success": self.execution_success,
            "execution_failed": self.execution_failed,
            "simulation_failed": self.simulation_failed,
            "dry_run_skipped": self.dry_run_skipped,
            "total_expected_profit": self.total_expected_profit,
            "total_realized_profit": self.total_realized_profit,
            "max_single_profit": self.max_single_profit,
            "hit_rate": self.hit_rate,
            "execution_success_rate": self.execution_success_rate,
            "revert_rate": self.revert_rate,
            "simulation_reject_rate": self.simulation_reject_rate,
            "pnl_accuracy": self.pnl_accuracy,
            "profit_per_scan": self.profit_per_scan,
            "flag_counts": self.flag_counts,
        }
        return d


def analyze_jsonl(filepath: str | Path) -> PerfReport:
    """Analyze a single JSONL log file and return a PerfReport."""
    report = PerfReport()
    path = Path(filepath)
    if not path.exists():
        return report

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        event = record.get("event")

        if event == "scan":
            report.total_scans += 1
            decision = record.get("decision", "")
            opp = record.get("opportunity")

            if decision == "no_opportunity":
                report.no_opportunity_scans += 1
            else:
                report.opportunities_found += 1

            if opp:
                expected = opp.get("net_profit_base", 0)
                report.total_expected_profit += expected
                report.max_single_profit = max(report.max_single_profit, expected)

                # Count warning flags.
                for flag in opp.get("warning_flags", []):
                    report.flag_counts[flag] = report.flag_counts.get(flag, 0) + 1

            if decision == "dry_run_skip":
                report.dry_run_skipped += 1
            elif decision.startswith("simulation_failed"):
                report.simulation_failed += 1

        elif event == "execution":
            report.executed_count += 1
            if record.get("success"):
                report.execution_success += 1
                report.total_realized_profit += record.get("realized_profit_base", 0)
            else:
                reason = record.get("reason", "")
                if "simulation_failed" in reason:
                    report.simulation_failed += 1
                else:
                    report.execution_failed += 1

    return report


def analyze_all_logs(log_dir: str | Path | None = None) -> PerfReport:
    """Analyze all JSONL files in the log directory."""
    directory = Path(log_dir) if log_dir else LOG_DIR
    combined = PerfReport()

    for filepath in sorted(directory.glob("bot_*.jsonl")):
        single = analyze_jsonl(filepath)
        combined.total_scans += single.total_scans
        combined.opportunities_found += single.opportunities_found
        combined.no_opportunity_scans += single.no_opportunity_scans
        combined.executed_count += single.executed_count
        combined.execution_success += single.execution_success
        combined.execution_failed += single.execution_failed
        combined.simulation_failed += single.simulation_failed
        combined.dry_run_skipped += single.dry_run_skipped
        combined.total_expected_profit += single.total_expected_profit
        combined.total_realized_profit += single.total_realized_profit
        combined.max_single_profit = max(combined.max_single_profit, single.max_single_profit)
        for flag, count in single.flag_counts.items():
            combined.flag_counts[flag] = combined.flag_counts.get(flag, 0) + count

    return combined


def print_report(report: PerfReport) -> None:
    """Print a human-readable performance report."""
    print("=" * 60)
    print("  PERFORMANCE REPORT")
    print("=" * 60)
    print()
    print(f"  Scans:                {report.total_scans:>10}")
    print(f"  Opportunities found:  {report.opportunities_found:>10}")
    print(f"  No opportunity:       {report.no_opportunity_scans:>10}")
    print(f"  Hit rate:             {report.hit_rate:>10.1%}")
    print()
    print(f"  Executed:             {report.executed_count:>10}")
    print(f"  Success:              {report.execution_success:>10}")
    print(f"  Reverted:             {report.execution_failed:>10}")
    print(f"  Simulation rejected:  {report.simulation_failed:>10}")
    print(f"  Dry-run skipped:      {report.dry_run_skipped:>10}")
    print(f"  Success rate:         {report.execution_success_rate:>10.1%}")
    print(f"  Revert rate:          {report.revert_rate:>10.1%}")
    print()
    print(f"  Expected PnL:         {report.total_expected_profit:>10.6f}")
    print(f"  Realized PnL:         {report.total_realized_profit:>10.6f}")
    print(f"  PnL accuracy:         {report.pnl_accuracy:>10.1%}")
    print(f"  Max single profit:    {report.max_single_profit:>10.6f}")
    print(f"  Profit per scan:      {report.profit_per_scan:>10.6f}")

    if report.flag_counts:
        print()
        print("  Warning flags:")
        for flag, count in sorted(report.flag_counts.items(), key=lambda x: -x[1]):
            print(f"    {flag:<25} {count:>6}")
    print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze bot performance from JSONL logs."
    )
    parser.add_argument(
        "--file",
        default=None,
        help="Analyze a specific JSONL log file. Default: all files in logs/.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Export report to JSON file (e.g. data/perf_report.json).",
    )
    return parser


def main() -> None:
    load_env()
    args = build_parser().parse_args()

    if args.file:
        print(f"Analyzing {args.file}...\n")
        report = analyze_jsonl(args.file)
    else:
        log_files = sorted(LOG_DIR.glob("bot_*.jsonl"))
        print(f"Analyzing {len(log_files)} log file(s) in {LOG_DIR}...\n")
        report = analyze_all_logs()

    print_report(report)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        print(f"Report exported to {output_path}")


if __name__ == "__main__":
    main()
