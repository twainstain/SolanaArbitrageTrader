"""Latency tracker — records per-stage timing for every pipeline execution.

Writes to logs/latency.jsonl (one JSON line per opportunity) for analysis.
Tracks: RPC fetch time, scanner time, queue wait, pipeline stages, total.

Usage:
    tracker = LatencyTracker()
    tracker.start_scan()
    # ... fetch quotes ...
    tracker.mark("rpc_fetch")
    # ... scan ...
    tracker.mark("scanner")
    # ... queue wait ...
    tracker.mark("queue_wait")
    # ... pipeline ...
    tracker.record_pipeline(opp_id, pair, chain, buy_dex, sell_dex, spread, timings)
    tracker.flush()  # write to file

Analysis:
    PYTHONPATH=src python -c "from observability.latency_tracker import analyze_latency; analyze_latency()"
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LATENCY_FILE = _PROJECT_ROOT / "logs" / "latency.jsonl"


@dataclass
class ScanTiming:
    """Timing data for one scan cycle."""
    scan_index: int = 0
    started_at: float = 0.0
    marks: dict = field(default_factory=dict)


class LatencyTracker:
    """Thread-safe latency recorder.

    Records timing data per scan and per opportunity to latency.jsonl.
    """

    def __init__(self, output_path: str | Path | None = None) -> None:
        self._path = Path(output_path) if output_path else _LATENCY_FILE
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._scan = ScanTiming()
        self._scan_count = 0
        self._file = open(self._path, "a", encoding="utf-8")

    def start_scan(self) -> None:
        """Mark the start of a new scan cycle."""
        with self._lock:
            self._scan_count += 1
            self._scan = ScanTiming(
                scan_index=self._scan_count,
                started_at=time.monotonic(),
            )

    def mark(self, stage: str) -> None:
        """Record a timing mark for the current scan."""
        with self._lock:
            elapsed_ms = (time.monotonic() - self._scan.started_at) * 1000
            self._scan.marks[stage] = round(elapsed_ms, 2)

    def get_scan_marks(self) -> dict:
        """Return a snapshot of current scan marks (thread-safe copy)."""
        with self._lock:
            return dict(self._scan.marks)

    def record_pipeline(
        self,
        opp_id: str,
        pair: str,
        chain: str,
        buy_dex: str,
        sell_dex: str,
        spread_pct: float,
        net_profit: float,
        status: str,
        pipeline_timings: dict,
        scan_marks: dict | None = None,
    ) -> None:
        """Record a full pipeline execution with all timing data.

        Args:
            scan_marks: Snapshot of scan marks from when the opportunity was
                        queued. If None, falls back to current scan marks
                        (may be stale if a new scan has started).
        """
        # Build the record under the lock (reads shared _scan state),
        # then release the lock BEFORE file I/O.  write()+flush() can
        # block 10-100ms on slow storage — holding the lock during I/O
        # would stall the scanner and consumer threads.
        with self._lock:
            total_scan_ms = (time.monotonic() - self._scan.started_at) * 1000
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "scan_index": self._scan.scan_index,
                "opp_id": opp_id,
                "pair": pair,
                "chain": chain,
                "buy_dex": buy_dex,
                "sell_dex": sell_dex,
                "spread_pct": round(spread_pct, 4),
                "net_profit": round(net_profit, 8),
                "status": status,
                "scan_marks_ms": scan_marks if scan_marks is not None else dict(self._scan.marks),
                "pipeline_ms": {k: round(float(v), 2) for k, v in pipeline_timings.items()},
                "total_scan_to_result_ms": round(total_scan_ms, 2),
            }
        # File I/O outside lock — won't block other threads.
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()

    def record_scan_summary(
        self,
        quote_count: int,
        opp_count: int,
        rejected_count: int = 0,
        status: str = "no_opportunity",
    ) -> None:
        """Record scan-level summary for EVERY cycle (not just pipeline hits).

        Args:
            quote_count: number of quotes fetched from RPC
            opp_count: number of opportunities that passed all filters
            rejected_count: number of opportunities rejected by scanner
            status: overall scan result — "no_opportunity", "queued", "market_error"
        """
        # Same pattern as record_pipeline: build record under lock, I/O outside.
        with self._lock:
            total_ms = (time.monotonic() - self._scan.started_at) * 1000
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "scan_summary",
                "scan_index": self._scan.scan_index,
                "quote_count": quote_count,
                "opportunity_count": opp_count,
                "rejected_count": rejected_count,
                "status": status,
                "scan_marks_ms": dict(self._scan.marks),
                "total_scan_ms": round(total_ms, 2),
            }
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def analyze_latency(filepath: str | Path | None = None) -> None:
    """Analyze latency.jsonl and print a summary report.

    Shows: avg/p50/p95/max for each stage, per chain, per pair.
    """
    path = Path(filepath) if filepath else _LATENCY_FILE
    if not path.exists():
        print(f"No latency file found at {path}")
        return

    records = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    # Separate pipeline records from scan summaries.
    pipelines = [r for r in records if "opp_id" in r]
    summaries = [r for r in records if r.get("type") == "scan_summary"]

    if not pipelines:
        print("No pipeline records found.")
        return

    print(f"\n{'='*70}")
    print(f"  LATENCY ANALYSIS — {len(pipelines)} pipeline records, {len(summaries)} scans")
    print(f"{'='*70}\n")

    # Overall pipeline stage timings.
    stage_times: dict[str, list[float]] = defaultdict(list)
    for r in pipelines:
        for stage, ms in r.get("pipeline_ms", {}).items():
            stage_times[stage].append(ms)

    print("  Pipeline Stage Latency (ms):")
    print(f"  {'Stage':<15s} {'Avg':>8s} {'P50':>8s} {'P95':>8s} {'Max':>8s} {'Count':>8s}")
    print(f"  {'-'*55}")
    for stage in ["detect_ms", "price_ms", "risk_ms", "simulate_ms", "total_ms"]:
        vals = sorted(stage_times.get(stage, []))
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        p50 = vals[len(vals) // 2]
        p95 = vals[int(len(vals) * 0.95)]
        mx = vals[-1]
        print(f"  {stage:<15s} {avg:>8.2f} {p50:>8.2f} {p95:>8.2f} {mx:>8.2f} {len(vals):>8d}")

    # Per-chain breakdown.
    print(f"\n  Per-Chain Pipeline Total (ms):")
    print(f"  {'Chain':<15s} {'Avg':>8s} {'P95':>8s} {'Count':>8s}")
    print(f"  {'-'*40}")
    chain_totals: dict[str, list[float]] = defaultdict(list)
    for r in pipelines:
        chain = r.get("chain", "?")
        total = r.get("pipeline_ms", {}).get("total_ms", 0)
        chain_totals[chain].append(total)
    for chain, vals in sorted(chain_totals.items(), key=lambda x: -len(x[1])):
        vals.sort()
        avg = sum(vals) / len(vals)
        p95 = vals[int(len(vals) * 0.95)]
        print(f"  {chain:<15s} {avg:>8.2f} {p95:>8.2f} {len(vals):>8d}")

    # Scan-level timings.
    if summaries:
        print(f"\n  Scan-Level Timings (ms):")
        scan_totals = [s.get("total_scan_ms", 0) for s in summaries]
        scan_totals.sort()
        avg = sum(scan_totals) / len(scan_totals)
        p50 = scan_totals[len(scan_totals) // 2]
        p95 = scan_totals[int(len(scan_totals) * 0.95)]
        rpc = [s.get("scan_marks_ms", {}).get("rpc_fetch", 0) for s in summaries]
        rpc.sort()
        rpc_avg = sum(rpc) / len(rpc) if rpc else 0
        print(f"  Total scan:  avg={avg:.0f}ms  p50={p50:.0f}ms  p95={p95:.0f}ms")
        print(f"  RPC fetch:   avg={rpc_avg:.0f}ms")

    # Hottest opportunities (highest total latency).
    print(f"\n  Slowest Pipeline Executions:")
    slowest = sorted(pipelines, key=lambda r: r.get("pipeline_ms", {}).get("total_ms", 0), reverse=True)[:5]
    for r in slowest:
        print(f"  {r.get('opp_id','?')[:16]}  chain={r.get('chain','?'):<10s}  "
              f"total={r.get('pipeline_ms',{}).get('total_ms',0):.1f}ms  "
              f"spread={r.get('spread_pct',0):.2f}%")

    print()
