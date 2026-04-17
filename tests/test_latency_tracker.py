"""Latency tracker tests — per-stage timings must be captured per scan."""

import json
from pathlib import Path

from observability.latency_tracker import LatencyTracker


def test_records_scan_and_pipeline_lines(tmp_path):
    path = tmp_path / "latency.jsonl"
    tracker = LatencyTracker(output_path=path)

    tracker.start_scan()
    tracker.mark("rpc_fetch")
    tracker.mark("scanner")
    tracker.record_pipeline(
        opp_id="opp_test123",
        pair="SOL/USDC",
        buy_venue="Jupiter-Direct",
        sell_venue="Jupiter-Best",
        spread_pct=0.45,
        net_profit=0.009,
        status="dry_run",
        pipeline_timings={"detect_ms": 1.2, "price_ms": 0.5, "risk_ms": 0.3, "total_ms": 5.1},
    )
    tracker.record_scan_summary(
        quote_count=2, opp_count=1, rejected_count=0, status="queued",
    )
    tracker.close()

    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2
    pipeline_record = json.loads(lines[0])
    assert pipeline_record["opp_id"] == "opp_test123"
    assert pipeline_record["buy_venue"] == "Jupiter-Direct"
    assert pipeline_record["pair"] == "SOL/USDC"
    assert "pipeline_ms" in pipeline_record
    assert "rpc_fetch" in pipeline_record["scan_marks_ms"]

    summary = json.loads(lines[1])
    assert summary["type"] == "scan_summary"
    assert summary["opportunity_count"] == 1


def test_scan_marks_snapshot_is_stable_under_new_scan(tmp_path):
    path = tmp_path / "latency.jsonl"
    tracker = LatencyTracker(output_path=path)
    tracker.start_scan()
    tracker.mark("rpc_fetch")
    marks = tracker.get_scan_marks()
    # New scan should not mutate the snapshot the caller already captured.
    tracker.start_scan()
    assert "rpc_fetch" in marks
    tracker.close()
