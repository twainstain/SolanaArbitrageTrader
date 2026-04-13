"""Parse and display arbitrage bot JSONL log files.

Reads a bot log file and displays scans, quotes, opportunities, executions,
and summary in a human-readable format.

Usage::

    # Parse the latest log
    PYTHONPATH=src python -m arbitrage_bot.log_parser

    # Parse a specific log file
    PYTHONPATH=src python -m arbitrage_bot.log_parser logs/bot_2026-04-13_00-10-53.jsonl

    # Show only opportunities (skip empty scans)
    PYTHONPATH=src python -m arbitrage_bot.log_parser --opportunities-only

    # Show full quote details
    PYTHONPATH=src python -m arbitrage_bot.log_parser --show-quotes

    # Export parsed output to a file
    PYTHONPATH=src python -m arbitrage_bot.log_parser --output data/parsed_report.txt
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from arbitrage_bot.log import LOG_DIR


def parse_log(filepath: str | Path) -> list[dict]:
    """Read a JSONL file and return a list of event dicts."""
    records = []
    for line in Path(filepath).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def format_scan(record: dict, show_quotes: bool = False) -> str:
    """Format a scan event into human-readable text."""
    lines = []
    ts = record.get("timestamp", "")
    idx = record.get("scan_index", "?")
    decision = record.get("decision", "?")
    quotes = record.get("quotes", [])
    opp = record.get("opportunity")

    lines.append(f"=== Scan {idx}  [{ts}] ===")
    lines.append(f"  Quotes: {len(quotes)}  Decision: {decision}")

    if show_quotes and quotes:
        # Group by pair.
        by_pair: dict[str, list] = defaultdict(list)
        for q in quotes:
            by_pair[q["pair"]].append(q)

        for pair, pqs in sorted(by_pair.items()):
            lines.append(f"  --- {pair} ({len(pqs)} venues) ---")
            for q in pqs:
                mid = (q["buy_price"] + q["sell_price"]) / 2
                vol = q.get("volume_usd", 0)
                liq = q.get("liquidity_usd", 0)
                extra = ""
                if vol > 0:
                    extra += f"  vol=${vol:,.0f}"
                if liq > 0:
                    extra += f"  liq=${liq:,.0f}"
                lines.append(
                    f"    {q['dex']:<14} mid=${mid:>12,.4f}  "
                    f"buy=${q['buy_price']:>12,.4f}  sell=${q['sell_price']:>12,.4f}{extra}"
                )

            # Compute best spread within this pair.
            if len(pqs) >= 2:
                cheapest = min(pqs, key=lambda x: x["buy_price"])
                priciest = max(pqs, key=lambda x: x["sell_price"])
                if cheapest["dex"] != priciest["dex"]:
                    spread = priciest["sell_price"] - cheapest["buy_price"]
                    pct = spread / cheapest["buy_price"] * 100
                    lines.append(
                        f"    Spread: buy {cheapest['dex']} -> sell {priciest['dex']} "
                        f"= ${spread:,.4f} ({pct:.4f}%)"
                    )

    if opp:
        flags = ", ".join(opp.get("warning_flags", [])) or "none"
        lines.append(f"  Opportunity:")
        lines.append(f"    {opp['pair']}  buy={opp['buy_dex']}  sell={opp['sell_dex']}")
        lines.append(
            f"    spread={opp.get('gross_spread_pct', 0):.4f}%  "
            f"net_profit={opp['net_profit_base']:.6f}"
        )
        lines.append(
            f"    cost_to_buy=${opp.get('cost_to_buy_quote', 0):,.4f}  "
            f"proceeds=${opp.get('proceeds_from_sell_quote', 0):,.4f}"
        )
        lines.append(
            f"    dex_fees=${opp.get('dex_fee_cost_quote', 0):,.4f}  "
            f"flash_fee=${opp.get('flash_loan_fee_quote', 0):,.4f}  "
            f"slippage=${opp.get('slippage_cost_quote', 0):,.4f}  "
            f"gas={opp.get('gas_cost_base', 0)}"
        )
        lines.append(f"    liq_score={opp.get('liquidity_score', 'n/a')}  flags=[{flags}]")

    return "\n".join(lines)


def format_execution(record: dict) -> str:
    """Format an execution event."""
    idx = record.get("scan_index", "?")
    success = record.get("success", False)
    reason = record.get("reason", "")
    profit = record.get("realized_profit_base", 0)
    status = "SUCCESS" if success else "FAILED"
    return f"  [exec {idx}] {status}  realized={profit:.6f}  reason={reason}"


def format_swap(record: dict) -> str:
    """Format a swap_detected event."""
    chain = record.get("chain", "?")
    block = record.get("block_number", "?")
    count = record.get("swap_count", 0)
    ts = record.get("timestamp", "")
    return f"  [swap] {count} swap(s) on {chain} at block {block}  [{ts}]"


def format_summary(record: dict) -> str:
    """Format a summary event."""
    lines = []
    lines.append("")
    lines.append(f"{'=' * 50}")
    lines.append(f"  SUMMARY ({record.get('mode', '?')})")
    lines.append(f"{'=' * 50}")
    lines.append(f"  Scans:                {record.get('total_scans', 0):>10}")
    lines.append(f"  Opportunities found:  {record.get('opportunities_found', 0):>10}")
    lines.append(f"  Executed:             {record.get('executed_count', 0):>10}")
    lines.append(f"  Total realized profit:{record.get('total_realized_profit', 0):>10.6f} {record.get('base_asset', '')}")
    return "\n".join(lines)


def run_parser(
    filepath: str,
    show_quotes: bool = False,
    opportunities_only: bool = False,
    output: str | None = None,
) -> None:
    """Parse and display a log file."""
    records = parse_log(filepath)

    if not records:
        print(f"No records found in {filepath}")
        return

    lines = []
    lines.append(f"Log file: {filepath}")
    lines.append(f"Records:  {len(records)}")
    lines.append("")

    for record in records:
        event = record.get("event")

        if event == "discovery":
            pair_names = [p["pair"] for p in record.get("pairs", [])]
            lines.append(f"=== Discovery  [{record.get('timestamp', '')}] ===")
            lines.append(f"  Discovered {record.get('pair_count', 0)} pair(s): {', '.join(pair_names)}")
            lines.append("")

        elif event == "discovery_detail":
            lines.append(
                f"  [LIVE] {record['pair']:<16} "
                f"{record['dex_count']} DEXs ({', '.join(record.get('dex_names', []))})  "
                f"vol=${record.get('total_volume_usd', 0):,.0f}  "
                f"chains={record.get('chains', [])}"
            )

        elif event == "scan":
            if opportunities_only and record.get("decision") == "no_opportunity":
                continue
            lines.append(format_scan(record, show_quotes=show_quotes))
            lines.append("")

        elif event == "execution":
            lines.append(format_execution(record))
            lines.append("")

        elif event == "swap_detected":
            lines.append(format_swap(record))

        elif event == "summary":
            lines.append(format_summary(record))
            lines.append("")

    text = "\n".join(lines)

    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(text, encoding="utf-8")
        print(f"Parsed output written to {output}")
    else:
        print(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse and display arbitrage bot JSONL log files."
    )
    parser.add_argument(
        "file",
        nargs="?",
        default=None,
        help="Path to a .jsonl log file. Default: latest file in logs/.",
    )
    parser.add_argument(
        "--show-quotes",
        action="store_true",
        help="Show full quote details for each scan.",
    )
    parser.add_argument(
        "--opportunities-only",
        action="store_true",
        help="Only show scans that found opportunities.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write parsed output to a file instead of stdout.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.file:
        filepath = args.file
    else:
        # Find the latest log file.
        files = sorted(LOG_DIR.glob("bot_*.jsonl"))
        if not files:
            print(f"No log files found in {LOG_DIR}")
            return
        filepath = str(files[-1])
        print(f"Using latest: {filepath}\n")

    run_parser(
        filepath=filepath,
        show_quotes=args.show_quotes,
        opportunities_only=args.opportunities_only,
        output=args.output,
    )


if __name__ == "__main__":
    main()
