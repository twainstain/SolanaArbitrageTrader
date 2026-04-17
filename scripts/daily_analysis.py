#!/usr/bin/env python3
"""Daily SolanaTrader analysis report.

Runs every 24h via cron (see ``scripts/install-cron.sh``).  Reads the DB
and produces:

  1. HTML + Markdown summary → ``data/reports/YYYY-MM-DD.html`` / ``.md``
  2. Optional email — if ``GMAIL_ADDRESS`` + ``GMAIL_APP_PASSWORD`` +
     ``GMAIL_RECIPIENT`` are configured in ``.env``, the HTML is emailed
     to the recipient.

The report covers the previous 24 hours (UTC) and includes:

  - Scan volume + opportunity funnel
  - Per-pair PnL + trade counts
  - Per-venue-route PnL
  - Top rejection reasons
  - Spread distribution (top routes)
  - Near-miss candidates (almost profitable)
  - Pipeline latency percentiles (from logs/latency.jsonl)
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "trading_platform" / "src"))

from core.env import load_env
from persistence.db import init_db
from persistence.repository import Repository

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_LATENCY_FILE = _PROJECT_ROOT / "logs" / "latency.jsonl"
_REPORTS_DIR = _PROJECT_ROOT / "data" / "reports"


def _latency_percentiles() -> dict[str, dict[str, float]]:
    """p50 / p95 / max per pipeline stage from the last 24h of latency.jsonl."""
    if not _LATENCY_FILE.exists():
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    stages: dict[str, list[float]] = defaultdict(list)
    for line in _LATENCY_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (rec.get("timestamp") or "") < cutoff:
            continue
        for stage, v in (rec.get("pipeline_ms") or {}).items():
            try:
                stages[stage].append(float(v))
            except (ValueError, TypeError):
                continue
        for stage, v in (rec.get("scan_marks_ms") or {}).items():
            try:
                stages[f"scan:{stage}"].append(float(v))
            except (ValueError, TypeError):
                continue
    out: dict[str, dict[str, float]] = {}
    for stage, vals in stages.items():
        vals.sort()
        n = len(vals)
        if n == 0:
            continue
        out[stage] = {
            "p50": vals[n // 2],
            "p95": vals[int(n * 0.95)] if n > 1 else vals[0],
            "max": vals[-1],
            "n":   n,
        }
    return out


def _build_report() -> tuple[str, str]:
    """Produce (markdown, html) for today's analysis."""
    repo = Repository(init_db())
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    analytics = repo.get_pnl_analytics(since=since)
    funnel = repo.get_opportunity_funnel()
    scan_filters = repo.get_scan_filter_breakdown(since=since)
    spread_dist = repo.get_spread_distribution(since=since)
    near_misses = repo.get_near_misses(threshold_sol=0.002, since=since, limit=10)
    latency = _latency_percentiles()

    # --- headline numbers ---
    per_pair = analytics.get("per_pair", [])
    total_trades = sum(r["trades"] for r in per_pair)
    total_wins = sum(r["wins"] for r in per_pair)
    total_profit = sum(r["net_profit"] for r in per_pair)
    total_detected = sum(funnel.values())
    approved = (
        funnel.get("approved", 0)
        + funnel.get("dry_run", 0)
        + funnel.get("simulation_approved", 0)
    )

    # --- Markdown report ---
    md = [f"# SolanaTrader daily report — {today}",
          "",
          f"_Covers the last 24 h (since {since})._",
          "",
          "## Headline",
          "",
          f"- Opportunities detected: **{total_detected}**",
          f"- Approved: **{approved}** / Rejected: **{funnel.get('rejected', 0)}**",
          f"- Executed trades: **{total_trades}** (wins: {total_wins})",
          f"- Realized PnL: **{total_profit:.6f} SOL**",
          ""]

    if per_pair:
        md += ["## Profit by pair",
               "",
               "| Pair | Trades | Wins | Net SOL | Avg SOL |",
               "|---|---:|---:|---:|---:|"]
        for r in per_pair:
            md.append(
                f"| {r['pair']} | {r['trades']} | {r['wins']} | "
                f"{r['net_profit']:+.6f} | {r['avg_profit']:+.6f} |"
            )
        md.append("")

    if analytics.get("per_venue"):
        md += ["## Profit by venue route",
               "",
               "| Buy → Sell | Trades | Wins | Net SOL |",
               "|---|---:|---:|---:|"]
        for r in analytics["per_venue"][:10]:
            md.append(
                f"| {r['buy_venue']} → {r['sell_venue']} | {r['trades']} | "
                f"{r['wins']} | {r['net_profit']:+.6f} |"
            )
        md.append("")

    if scan_filters:
        md += ["## Scan filter breakdown",
               "",
               "| Reason | Count | Avg spread | Best net SOL |",
               "|---|---:|---:|---:|"]
        for r in scan_filters:
            md.append(
                f"| {r['filter_reason']} | {r['cnt']} | "
                f"{(r['avg_spread'] or 0):.4f}% | {(r['best_net_profit'] or 0):+.6f} |"
            )
        md.append("")

    if spread_dist:
        md += ["## Top observed spreads",
               "",
               "| Pair | Route | Samples | Avg | Max |",
               "|---|---|---:|---:|---:|"]
        for r in spread_dist[:10]:
            md.append(
                f"| {r['pair']} | {r['buy_venue']} → {r['sell_venue']} | "
                f"{r['samples']} | {(r['avg_spread'] or 0):.4f}% | {(r['max_spread'] or 0):.4f}% |"
            )
        md.append("")

    if near_misses:
        md += ["## Near misses (almost profitable)",
               "",
               "| Time | Pair | Route | Spread | Net SOL |",
               "|---|---|---|---:|---:|"]
        for r in near_misses:
            md.append(
                f"| {(r.get('scan_ts') or '')[11:19]} | {r['pair']} | "
                f"{r['buy_venue']} → {r['sell_venue']} | "
                f"{(r['spread'] or 0):.4f}% | {(r['net_profit'] or 0):+.6f} |"
            )
        md.append("")

    if latency:
        md += ["## Pipeline latency (ms)",
               "",
               "| Stage | p50 | p95 | max | n |",
               "|---|---:|---:|---:|---:|"]
        for stage, v in sorted(latency.items()):
            md.append(f"| {stage} | {v['p50']:.0f} | {v['p95']:.0f} | {v['max']:.0f} | {v['n']} |")
        md.append("")

    md += [f"_Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}_"]
    md_text = "\n".join(md)

    # --- HTML version (plain-styled, email-friendly) ---
    html = _md_to_html(md_text, title=f"SolanaTrader — {today}")
    return md_text, html


def _md_to_html(md: str, title: str) -> str:
    """Minimal Markdown → HTML for email.  Handles headings + tables only."""
    lines = md.splitlines()
    html_lines = [
        "<!doctype html>",
        "<html><head><meta charset='utf-8'>",
        f"<title>{title}</title>",
        "<style>",
        "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;",
        "       background: #f7f8fa; color: #1a1a1a; padding: 24px; }",
        "h1 { font-size: 22px; margin-bottom: 4px; }",
        "h2 { font-size: 15px; color: #494fdf; margin-top: 28px; border-bottom: 1px solid #e5e7eb; padding-bottom: 6px; }",
        "table { border-collapse: collapse; width: 100%; margin-top: 8px; background: #fff;",
        "        border-radius: 8px; overflow: hidden; }",
        "th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #eef1f4; font-size: 13px; }",
        "th { background: #f0f3f7; color: #5a6470; font-weight: 600; }",
        "em { color: #5a6470; font-size: 12px; }",
        "</style></head><body>",
    ]
    in_table = False
    for line in lines:
        if line.startswith("# "):
            html_lines.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("|") and "---" in line:
            # separator row (the second line of a markdown table) — skip
            continue
        elif line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not in_table:
                html_lines.append("<table><tr>" + "".join(f"<th>{c}</th>" for c in cells) + "</tr>")
                in_table = True
            else:
                html_lines.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        else:
            if in_table:
                html_lines.append("</table>")
                in_table = False
            if line.startswith("_") and line.endswith("_"):
                html_lines.append(f"<p><em>{line.strip('_')}</em></p>")
            elif line.startswith("- "):
                html_lines.append(f"<p>{line[2:]}</p>")
            elif line.strip():
                html_lines.append(f"<p>{line}</p>")
    if in_table:
        html_lines.append("</table>")
    html_lines.append("</body></html>")
    return "\n".join(html_lines)


def _maybe_email(html: str, subject: str) -> bool:
    gmail = os.environ.get("GMAIL_ADDRESS", "")
    app_pw = os.environ.get("GMAIL_APP_PASSWORD", "")
    recipient = os.environ.get("GMAIL_RECIPIENT") or gmail
    if not (gmail and app_pw and recipient):
        return False
    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = gmail
        msg["To"] = recipient
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(gmail, app_pw)
            s.send_message(msg)
        return True
    except Exception as exc:
        print(f"[daily_analysis] email failed: {exc}", file=sys.stderr)
        return False


def main() -> None:
    load_env()
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md_text, html_text = _build_report()

    md_path = _REPORTS_DIR / f"{today}.md"
    html_path = _REPORTS_DIR / f"{today}.html"
    md_path.write_text(md_text)
    html_path.write_text(html_text)
    print(f"[daily_analysis] wrote {md_path}")
    print(f"[daily_analysis] wrote {html_path}")

    sent = _maybe_email(html_text, subject=f"SolanaTrader daily — {today}")
    print(f"[daily_analysis] email sent: {sent}")


if __name__ == "__main__":
    main()
