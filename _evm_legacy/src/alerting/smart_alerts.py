"""Smart alerting rules — Telegram/Discord for big wins, hourly + daily email summaries.

Rules:
  - Spread > 5%: Immediate Telegram + Discord alert (big wins only)
  - Every hour: Email-only aggregate report with dashboard link
  - Every 24h: Email-only daily summary with full stats
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from threading import Thread

from alerting.discord import DiscordAlert
from alerting.telegram import TelegramAlert
from alerting.gmail import GmailAlert
from persistence.repository import Repository

logger = logging.getLogger(__name__)

D = Decimal
# Alert threshold for immediate Telegram + Discord notification.
# Any opportunity with spread >= this triggers a real-time alert.
# Set to 0.8% — below this, spreads are too thin to be noteworthy
# and would create alert noise.
BIG_WIN_THRESHOLD_PCT = D("0.8")

# Intervals in seconds.
HOURLY_INTERVAL = 3600.0
DAILY_INTERVAL = 86400.0

# ── HTML color helpers ────────────────────────────────────────────────

_GREEN = "#00a87e"      # Revolut teal — success/profit
_RED = "#e23b4a"        # Revolut danger — loss/revert
_YELLOW = "#ec7e00"     # Revolut warning — pending
_GRAY = "#8d969e"       # Revolut cool gray — muted
_WHITE = "#ffffff"
_DARK_BG = "#191c1f"    # Revolut dark
_CARD_BG = "#242729"    # Card surface
_BORDER = "#2e3236"     # Subtle border


def _clr(val: float | int | str | None, positive_good: bool = True) -> str:
    """Return a color hex for a numeric value."""
    if val is None:
        return _GRAY
    try:
        v = float(val)
    except (ValueError, TypeError):
        return _WHITE
    if v > 0:
        return _GREEN if positive_good else _RED
    if v < 0:
        return _RED if positive_good else _GREEN
    return _GRAY


def _colored(val: str, color: str) -> str:
    return f'<span style="color:{color};font-weight:bold">{val}</span>'


def _row(label: str, value: str, color: str = _WHITE, indent: int = 0) -> str:
    pad = "&nbsp;" * (indent * 4)
    return (
        f'<tr>'
        f'<td style="padding:4px 12px;color:{_GRAY};white-space:nowrap">{pad}{label}</td>'
        f'<td style="padding:4px 12px;color:{color};font-weight:bold;text-align:right;word-break:break-all">{value}</td>'
        f'</tr>'
    )


def _section_header(title: str) -> str:
    return (
        f'<tr><td colspan="2" style="padding:12px 12px 4px;font-size:14px;'
        f'font-weight:bold;color:{_WHITE};border-bottom:1px solid {_BORDER}">'
        f'{title}</td></tr>'
    )


def _html_wrapper(title: str, body: str, dashboard_url: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:{_DARK_BG};font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;-webkit-text-size-adjust:100%;letter-spacing:0.16px">
<div style="max-width:600px;margin:12px auto;background:{_CARD_BG};border-radius:20px;overflow:hidden">
  <div style="background:{_BORDER};padding:16px 20px">
    <h2 style="margin:0;color:{_WHITE};font-size:16px;font-weight:600;letter-spacing:-0.16px">{title}</h2>
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:13px" cellpadding="0" cellspacing="0">
    {body}
  </table>
  <div style="padding:16px 20px;text-align:center;border-top:1px solid {_BORDER}">
    <a href="{dashboard_url}" style="text-decoration:none;font-size:14px;font-weight:600;display:inline-block;padding:10px 32px;background:#494fdf;border-radius:9999px;color:#ffffff">Open Dashboard</a>
  </div>
</div>
</body>
</html>"""


# ── Wallet helper ─────────────────────────────────────────────────────

def _format_eth(value: float | None) -> str:
    if value is None:
        return "error"
    return f"{value:.6f} ETH"


def _fetch_wallet_data() -> dict:
    """Return wallet data dict: {address, balances}."""
    try:
        from observability.wallet import get_wallet_balances
        return get_wallet_balances()
    except Exception as exc:
        logger.debug("Wallet balance fetch failed: %s", exc)
        return {"address": "", "balances": {}}


def _wallet_plain(wb: dict) -> str:
    if not wb["address"]:
        return "  (wallet not configured)\n"
    lines = [f"  Address: {wb['address']}"]
    total = 0.0
    for chain, bal in wb["balances"].items():
        lines.append(f"  {chain:>12}: {_format_eth(bal)}")
        if bal is not None:
            total += bal
    lines.append(f"  {'total':>12}: {_format_eth(total)}")
    return "\n".join(lines) + "\n"


def _wallet_html(wb: dict) -> str:
    if not wb["address"]:
        return _row("Wallet", "not configured", _GRAY)
    rows = _row("Address", wb["address"][:10] + "..." + wb["address"][-6:], _WHITE)
    total = 0.0
    for chain, bal in wb["balances"].items():
        bal_str = _format_eth(bal)
        color = _GREEN if bal is not None and bal > 0.005 else (_YELLOW if bal is not None else _RED)
        rows += _row(chain.capitalize(), bal_str, color, indent=1)
        if bal is not None:
            total += bal
    total_color = _GREEN if total > 0.01 else (_YELLOW if total > 0 else _RED)
    rows += _row("Total", _format_eth(total), total_color, indent=1)
    return rows


# ── Chain stats helper ────────────────────────────────────────────────

def _chain_plain(chain_stats: dict[str, dict]) -> str:
    if not chain_stats:
        return "  (no chain data)\n"
    lines = []
    for chain, stats in sorted(chain_stats.items(), key=lambda x: x[1]["total"], reverse=True):
        total = stats["total"]
        rejected = stats.get("rejected", 0)
        approved = stats.get("approved", 0) + stats.get("simulation_approved", 0)
        included = stats.get("included", 0)
        lines.append(f"  {chain:>12}: {total} detected, {approved} actionable, {included} included, {rejected} rejected")
    return "\n".join(lines) + "\n"


def _chain_html(chain_stats: dict[str, dict]) -> str:
    if not chain_stats:
        return _row("Chains", "no data", _GRAY)
    rows = ""
    for chain, stats in sorted(chain_stats.items(), key=lambda x: x[1]["total"], reverse=True):
        total = stats["total"]
        rejected = stats.get("rejected", 0)
        approved = stats.get("approved", 0) + stats.get("simulation_approved", 0)
        included = stats.get("included", 0)
        summary = (
            f'{_colored(str(total), _WHITE)} det | '
            f'{_colored(str(approved), _GREEN)} act | '
            f'{_colored(str(included), _GREEN if included else _GRAY)} inc | '
            f'{_colored(str(rejected), _RED if rejected else _GRAY)} rej'
        )
        rows += f'<tr><td style="padding:4px 12px;color:{_GRAY}">&nbsp;&nbsp;&nbsp;&nbsp;{chain}</td><td style="padding:4px 12px;text-align:right">{summary}</td></tr>'
    return rows


# ── Execution stats helper ────────────────────────────────────────────

def _exec_plain(exec_stats: dict) -> str:
    if not exec_stats or exec_stats.get("total_trades", 0) == 0:
        return "  No executed trades\n"
    lines = [
        f"  Trades:       {exec_stats.get('total_trades', 0)}",
        f"  Successful:   {exec_stats.get('successful', 0)}",
        f"  Reverted:     {exec_stats.get('reverted', 0)}",
        f"  Not included: {exec_stats.get('not_included', 0)}",
        f"  Net profit:   {exec_stats.get('total_profit', 0):.6f}",
        f"  Gas cost:     {exec_stats.get('total_gas_cost', 0):.6f}",
        f"  Gas used:     {exec_stats.get('total_gas_used', 0):,}",
    ]
    return "\n".join(lines) + "\n"


def _exec_html(exec_stats: dict) -> str:
    if not exec_stats or exec_stats.get("total_trades", 0) == 0:
        return _row("Trades", "none", _GRAY)
    rows = ""
    rows += _row("Trades", str(exec_stats.get("total_trades", 0)), _WHITE)
    successful = exec_stats.get("successful", 0) or 0
    reverted = exec_stats.get("reverted", 0) or 0
    not_included = exec_stats.get("not_included", 0) or 0
    rows += _row("Successful", str(successful), _GREEN if successful else _GRAY, indent=1)
    rows += _row("Reverted", str(reverted), _RED if reverted else _GRAY, indent=1)
    rows += _row("Not included", str(not_included), _YELLOW if not_included else _GRAY, indent=1)
    profit = exec_stats.get("total_profit", 0) or 0
    gas_cost = exec_stats.get("total_gas_cost", 0) or 0
    rows += _row("Net profit", f"{profit:.6f}", _clr(profit), indent=1)
    rows += _row("Gas cost", f"{gas_cost:.6f}", _RED if gas_cost > 0 else _GRAY, indent=1)
    gas_used = exec_stats.get("total_gas_used", 0) or 0
    rows += _row("Gas used", f"{gas_used:,}", _WHITE, indent=1)
    return rows


# ══════════════════════════════════════════════════════════════════════
# SmartAlerter
# ══════════════════════════════════════════════════════════════════════

class SmartAlerter:
    """Applies alerting rules on top of the dispatcher.

    - Telegram + Discord: immediate alert for spreads > 5%
    - Gmail: hourly aggregate report
    - Gmail: daily comprehensive summary
    """

    def __init__(
        self,
        repo: Repository,
        telegram: TelegramAlert | None = None,
        discord: DiscordAlert | None = None,
        gmail: GmailAlert | None = None,
        dashboard_url: str = "http://localhost:8000/dashboard",
        email_interval_seconds: float = HOURLY_INTERVAL,
    ) -> None:
        self.repo = repo
        self.telegram = telegram or TelegramAlert()
        self.discord = discord or DiscordAlert()
        self.gmail = gmail or GmailAlert()
        self.dashboard_url = dashboard_url
        self.email_interval = email_interval_seconds
        # Send first hourly report 5 minutes after startup.
        self._last_email_at: float = time.time() - email_interval_seconds + 300
        # Daily report: send at 9:00 AM EST covering the previous 24 hours.
        # Track which date we last sent for, not a time interval.
        self._last_daily_date: str = ""  # "YYYY-MM-DD" of the last daily report sent
        self._daily_hour_est: int = 9    # hour in EST to send (9 = 9:00 AM)
        self._hourly_thread: Thread | None = None
        self._running = False

    def check_opportunity(self, spread_pct: Decimal, pair: str,
                          buy_dex: str, sell_dex: str, chain: str,
                          net_profit: float, opp_id: str = "") -> None:
        """Check if an opportunity warrants an immediate alert."""
        if spread_pct < BIG_WIN_THRESHOLD_PCT:
            return

        from alerting.dispatcher import opp_dashboard_url
        opp_link = opp_dashboard_url(opp_id, self.dashboard_url) if opp_id else self.dashboard_url

        msg = (
            f"BIG SPREAD: {pair}\n"
            f"Chain: {chain}\n"
            f"Buy: {buy_dex} -> Sell: {sell_dex}\n"
            f"Spread: {float(spread_pct):.2f}%\n"
            f"Net profit: {net_profit:.6f}\n"
            f"\nDashboard: {opp_link}"
        )
        details = {
            "pair": pair, "chain": chain,
            "buy_dex": buy_dex, "sell_dex": sell_dex,
            "spread_pct": f"{float(spread_pct):.2f}%",
            "net_profit": f"{net_profit:.6f}",
            "dashboard_link": opp_link,
        }
        if opp_id:
            details["opp_id"] = opp_id

        if self.telegram.configured:
            self.telegram.send("opportunity_found", msg)
            logger.info("Telegram alert sent for %.2f%% spread on %s", float(spread_pct), pair)

        if self.discord.configured:
            self.discord.send("opportunity_found", msg, details)
            logger.info("Discord alert sent for %.2f%% spread on %s", float(spread_pct), pair)

    # ------------------------------------------------------------------
    # Hourly report
    # ------------------------------------------------------------------

    def send_hourly_report(self) -> None:
        """Send an hourly aggregate report via email."""
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        since = (now - timedelta(hours=1)).isoformat()

        total = self.repo.count_opportunities_since(since)
        approved = self.repo.count_opportunities_since(since, status="approved")
        rejected = self.repo.count_opportunities_since(since, status="rejected")
        sim_approved = self.repo.count_opportunities_since(since, status="simulation_approved")
        dry_run = self.repo.count_opportunities_since(since, status="dry_run")
        included = self.repo.count_opportunities_since(since, status="included")

        pnl = self.repo.get_pnl_summary()
        exec_stats = self.repo.get_execution_stats(since)
        chain_stats = self.repo.get_chain_opportunity_stats(since)
        wb = _fetch_wallet_data()

        actionable_hour = sim_approved + approved + included
        actionable_pct = f" ({actionable_hour * 100 // total}%)" if total > 0 else ""

        # ── Plain text ──
        plain = (
            f"Hourly Arbitrage Report — {now.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"{'='*55}\n\n"
            f"WALLET:\n{_wallet_plain(wb)}\n"
            f"LAST HOUR:\n"
            f"  Detected:            {total}\n"
            f"  Actionable:          {actionable_hour}{actionable_pct}\n"
            f"    Sim approved:      {sim_approved}\n"
            f"    Approved (live):   {approved}\n"
            f"    Included on-chain: {included}\n"
            f"  Rejected:            {rejected}\n"
            f"  Dry-run:             {dry_run}\n\n"
            f"EXECUTION (last hour):\n{_exec_plain(exec_stats)}\n"
            f"PER CHAIN (last hour):\n{_chain_plain(chain_stats)}\n"
            f"PNL (all time):\n"
            f"  Total profit: {pnl.get('total_profit', 0)}\n"
            f"  Successful:   {pnl.get('successful', 0)}\n"
            f"  Reverted:     {pnl.get('reverted', 0)}\n\n"
            f"Dashboard: {self.dashboard_url}\n"
        )

        # ── HTML ──
        title = f"Hourly Report — {now.strftime('%Y-%m-%d %H:%M UTC')}"
        body = _section_header("Wallet")
        body += _wallet_html(wb)

        body += _section_header("Last Hour — Opportunities")
        body += _row("Detected", str(total), _WHITE)
        body += _row("Actionable", f"{actionable_hour}{actionable_pct}", _GREEN if actionable_hour else _GRAY)
        body += _row("Sim approved", str(sim_approved), _GREEN if sim_approved else _GRAY, indent=1)
        body += _row("Approved (live)", str(approved), _GREEN if approved else _GRAY, indent=1)
        body += _row("Included", str(included), _GREEN if included else _GRAY, indent=1)
        body += _row("Rejected", str(rejected), _RED if rejected else _GRAY)
        body += _row("Dry-run", str(dry_run), _YELLOW if dry_run else _GRAY)

        body += _section_header("Last Hour — Execution")
        body += _exec_html(exec_stats)

        body += _section_header("Last Hour — Per Chain")
        body += _chain_html(chain_stats)

        total_profit = pnl.get("total_profit", 0) or 0
        body += _section_header("PnL (all time)")
        body += _row("Total profit", f"{total_profit:.6f}", _clr(total_profit))
        body += _row("Successful", str(pnl.get("successful", 0)), _GREEN if pnl.get("successful") else _GRAY)
        body += _row("Reverted", str(pnl.get("reverted", 0)), _RED if pnl.get("reverted") else _GRAY)

        html = _html_wrapper(title, body, self.dashboard_url)

        details = {
            "last_hour_detected": total,
            "last_hour_actionable": actionable_hour,
            "last_hour_rejected": rejected,
            "last_hour_included": included,
            "all_time_profit": str(pnl.get("total_profit", 0)),
            "dashboard": self.dashboard_url,
        }

        if self.gmail.configured:
            ok = self.gmail.send("hourly_summary", plain, details, html_body=html)
            if ok:
                logger.info("Hourly email report sent")
            else:
                logger.error("Hourly email report FAILED to send")
        else:
            logger.warning("Hourly report skipped — Gmail not configured")

        self._last_email_at = time.time()

    def maybe_send_hourly(self) -> None:
        """Check if it's time to send the hourly report."""
        if time.time() - self._last_email_at >= self.email_interval:
            self.send_hourly_report()

    # ------------------------------------------------------------------
    # Daily report
    # ------------------------------------------------------------------

    def send_daily_report(self) -> None:
        """Send a daily comprehensive summary via email."""
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        since_24h = (now - timedelta(hours=24)).isoformat()

        # 24h opportunity counts.
        total_24h = self.repo.count_opportunities_since(since_24h)
        approved_24h = self.repo.count_opportunities_since(since_24h, status="approved")
        rejected_24h = self.repo.count_opportunities_since(since_24h, status="rejected")
        sim_approved_24h = self.repo.count_opportunities_since(since_24h, status="simulation_approved")
        dry_run_24h = self.repo.count_opportunities_since(since_24h, status="dry_run")
        included_24h = self.repo.count_opportunities_since(since_24h, status="included")
        submitted_24h = self.repo.count_opportunities_since(since_24h, status="submitted")
        reverted_24h = self.repo.count_opportunities_since(since_24h, status="reverted")

        actionable_24h = sim_approved_24h + approved_24h + included_24h
        actionable_pct = f" ({actionable_24h * 100 // total_24h}%)" if total_24h > 0 else ""

        exec_stats_24h = self.repo.get_execution_stats(since_24h)
        funnel = self.repo.get_opportunity_funnel()
        pnl = self.repo.get_pnl_summary()
        exec_stats_all = self.repo.get_execution_stats()
        chain_stats = self.repo.get_chain_opportunity_stats(since_24h)
        wb = _fetch_wallet_data()

        funnel_lines = "\n".join(
            f"  {s}: {c}" for s, c in funnel.items()
        ) if isinstance(funnel, dict) else f"  {funnel}"

        # ── Plain text ──
        plain = (
            f"Daily Arbitrage Summary — {now.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"{'='*55}\n\n"
            f"WALLET:\n{_wallet_plain(wb)}\n"
            f"LAST 24 HOURS — OPPORTUNITIES:\n"
            f"  Detected:            {total_24h}\n"
            f"  Actionable:          {actionable_24h}{actionable_pct}\n"
            f"    Sim approved:      {sim_approved_24h}\n"
            f"    Approved (live):   {approved_24h}\n"
            f"    Submitted:         {submitted_24h}\n"
            f"    Included on-chain: {included_24h}\n"
            f"    Reverted:          {reverted_24h}\n"
            f"  Rejected:            {rejected_24h}\n"
            f"  Dry-run:             {dry_run_24h}\n\n"
            f"LAST 24 HOURS — EXECUTION:\n{_exec_plain(exec_stats_24h)}\n"
            f"PER CHAIN (24h):\n{_chain_plain(chain_stats)}\n"
            f"ALL TIME — FUNNEL:\n{funnel_lines}\n\n"
            f"ALL TIME — PNL:\n"
            f"  Total profit:       {pnl.get('total_profit', 0)}\n"
            f"  Realized (quote):   {pnl.get('total_realized_profit_quote', 0)}\n"
            f"  Gas cost (base):    {pnl.get('total_gas_cost_base', 0)}\n"
            f"  Successful trades:  {pnl.get('successful', 0)}\n"
            f"  Reverted trades:    {pnl.get('reverted', 0)}\n"
            f"  Not included:       {pnl.get('not_included', 0)}\n\n"
            f"ALL TIME — EXECUTION:\n{_exec_plain(exec_stats_all)}\n"
            f"Dashboard: {self.dashboard_url}\n"
        )

        # ── HTML ──
        title = f"Daily Summary — {now.strftime('%Y-%m-%d %H:%M UTC')}"
        body = _section_header("Wallet")
        body += _wallet_html(wb)

        body += _section_header("Last 24h — Opportunities")
        body += _row("Detected", str(total_24h), _WHITE)
        body += _row("Actionable", f"{actionable_24h}{actionable_pct}", _GREEN if actionable_24h else _GRAY)
        body += _row("Sim approved", str(sim_approved_24h), _GREEN if sim_approved_24h else _GRAY, indent=1)
        body += _row("Approved (live)", str(approved_24h), _GREEN if approved_24h else _GRAY, indent=1)
        body += _row("Submitted", str(submitted_24h), _YELLOW if submitted_24h else _GRAY, indent=1)
        body += _row("Included", str(included_24h), _GREEN if included_24h else _GRAY, indent=1)
        body += _row("Reverted", str(reverted_24h), _RED if reverted_24h else _GRAY, indent=1)
        body += _row("Rejected", str(rejected_24h), _RED if rejected_24h else _GRAY)
        body += _row("Dry-run", str(dry_run_24h), _YELLOW if dry_run_24h else _GRAY)

        body += _section_header("Last 24h — Execution")
        body += _exec_html(exec_stats_24h)

        body += _section_header("Last 24h — Per Chain")
        body += _chain_html(chain_stats)

        body += _section_header("All Time — Funnel")
        if isinstance(funnel, dict):
            for status, count in funnel.items():
                color = _GREEN if status in ("included", "approved", "simulation_approved") else (
                    _RED if status in ("rejected", "reverted") else _YELLOW
                )
                body += _row(status, str(count), color if count else _GRAY)
        else:
            body += _row("Funnel", str(funnel), _GRAY)

        total_profit = pnl.get("total_profit", 0) or 0
        gas_cost_base = pnl.get("total_gas_cost_base", 0) or 0
        body += _section_header("All Time — PnL")
        body += _row("Total profit", f"{total_profit:.6f}", _clr(total_profit))
        body += _row("Realized (quote)", str(pnl.get("total_realized_profit_quote", 0)), _WHITE)
        body += _row("Gas cost (base)", f"{gas_cost_base:.6f}", _RED if gas_cost_base > 0 else _GRAY)
        body += _row("Successful", str(pnl.get("successful", 0)), _GREEN if pnl.get("successful") else _GRAY)
        body += _row("Reverted", str(pnl.get("reverted", 0)), _RED if pnl.get("reverted") else _GRAY)
        body += _row("Not included", str(pnl.get("not_included", 0)), _YELLOW if pnl.get("not_included") else _GRAY)

        body += _section_header("All Time — Execution")
        body += _exec_html(exec_stats_all)

        html = _html_wrapper(title, body, self.dashboard_url)

        details = {
            "period": "24h",
            "detected_24h": total_24h,
            "actionable_24h": actionable_24h,
            "rejected_24h": rejected_24h,
            "included_24h": included_24h,
            "all_time_profit": str(pnl.get("total_profit", 0)),
            "all_time_trades": str(pnl.get("total_trades", 0)),
            "dashboard": self.dashboard_url,
        }

        if self.gmail.configured:
            ok = self.gmail.send("daily_summary", plain, details, html_body=html)
            if ok:
                logger.info("Daily email report sent")
            else:
                logger.error("Daily email report FAILED to send")
        else:
            logger.warning("Daily report skipped — Gmail not configured")

    def maybe_send_daily(self) -> None:
        """Send the daily report at 9:00 AM EST, covering the previous 24 hours.

        Checks if:
          1. Current time in EST is past the target hour (9 AM)
          2. We haven't already sent today's report
        This way the report fires once per day at the right time, regardless
        of restarts or timezone differences.
        """
        from datetime import datetime, timezone, timedelta

        # EST is UTC-5.  During EDT (Mar-Nov) it's UTC-4, but we use a fixed
        # -5 offset for consistency — the report arrives at 9 AM EST / 10 AM EDT.
        EST = timezone(timedelta(hours=-5))
        now_est = datetime.now(EST)
        today_str = now_est.strftime("%Y-%m-%d")

        # Only send if it's past the target hour and we haven't sent today.
        if now_est.hour >= self._daily_hour_est and today_str != self._last_daily_date:
            self._last_daily_date = today_str
            self.send_daily_report()

    # ------------------------------------------------------------------
    # Background threads
    # ------------------------------------------------------------------

    def start_background_hourly(self) -> None:
        """Start a background thread that sends hourly + daily reports."""
        if self._running:
            return
        self._running = True

        def _loop():
            while self._running:
                time.sleep(60)  # check every minute
                self.maybe_send_hourly()
                self.maybe_send_daily()

        self._hourly_thread = Thread(target=_loop, daemon=True)
        self._hourly_thread.start()
        logger.info("Hourly + daily report thread started")

    def stop(self) -> None:
        self._running = False
