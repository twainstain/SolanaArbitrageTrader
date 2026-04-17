"""Shared HTML scaffolding for SolanaTrader dashboards.

CSS style mirrors the production ArbitrageTrader dashboard
(https://arb-trader.yeda-ai.com) — Inter font, 20px-radius cards,
pill-shaped tags, hoverable columns.  The user explicitly asked to keep
the 3 separate URLs (/dashboard, /ops, /analytics) with this table design.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

SOLSCAN = "https://solscan.io"

# Window presets exposed in the filter dropdown.
WINDOW_PRESETS = [
    ("5m",  timedelta(minutes=5)),
    ("15m", timedelta(minutes=15)),
    ("1h",  timedelta(hours=1)),
    ("4h",  timedelta(hours=4)),
    ("8h",  timedelta(hours=8)),
    ("24h", timedelta(hours=24)),
    ("3d",  timedelta(days=3)),
    ("1w",  timedelta(weeks=1)),
    ("1m",  timedelta(days=30)),
]

# Opportunity-status values surfaced in the status filter.
STATUS_OPTIONS = [
    "detected", "priced", "approved", "rejected",
    "simulation_approved", "simulated", "simulation_failed",
    "submitted", "confirmed", "reverted", "dropped", "dry_run",
]

_SHARED_CSS = """
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
           background: #191c1f; color: #f4f4f4; padding: 24px; letter-spacing: 0.16px; }
    a { color: #9cdcfe; text-decoration: none; }
    a:hover { text-decoration: underline; }
    h1 { color: #ffffff; margin-bottom: 24px; font-weight: 600; font-size: 28px; letter-spacing: -0.4px; }
    h2 { color: #8d969e; margin: 24px 0 12px; font-size: 13px; text-transform: uppercase;
         font-weight: 600; letter-spacing: 0.24px; }
    h3 { color: #8d969e; margin: 14px 0 8px; font-size: 12px; text-transform: uppercase;
         letter-spacing: 0.2em; font-weight: 600; }

    .nav { display: flex; gap: 8px; margin-bottom: 20px; flex-wrap: wrap; }
    .nav a { padding: 8px 20px; border-radius: 9999px; background: #242729; color: #8d969e;
             font-size: 13px; font-weight: 500; text-decoration: none; }
    .nav a:hover { background: #2e3236; color: #f4f4f4; }
    .nav a.active { background: #494fdf; color: #fff; }

    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; }
    .grid.tight { grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); }
    .card { background: #242729; border-radius: 20px; padding: 20px; }
    .card-title { font-size: 11px; color: #8d969e; text-transform: uppercase; margin-bottom: 8px;
                  font-weight: 600; letter-spacing: 0.24px; }
    .card-value { font-size: 26px; font-weight: 600; color: #ffffff; letter-spacing: -0.32px; }
    .card-value.small { font-size: 16px; }
    .card-sub { font-size: 11px; color: #8d969e; margin-top: 6px; }

    .status-ok   { color: #00a87e; }
    .status-warn { color: #ec7e00; }
    .status-bad  { color: #e23b4a; }

    table { width: 100%; border-collapse: collapse; margin-top: 10px;
            background: #242729; border-radius: 14px; overflow: hidden; }
    th { text-align: left; padding: 10px 12px; border-bottom: 2px solid #2e3236;
         color: #8d969e; font-size: 11px; text-transform: uppercase; font-weight: 600;
         letter-spacing: 0.22px; }
    td { padding: 10px 12px; border-bottom: 1px solid #2a2d31; font-size: 13px; }
    tr:hover td { background: #2a2d31; }
    tr:last-child td { border-bottom: none; }
    .num { text-align: right; font-variant-numeric: tabular-nums; }
    .positive { color: #00a87e; }
    .negative { color: #e23b4a; }
    .muted { color: #8d969e; }

    .tag { display: inline-block; padding: 3px 10px; border-radius: 9999px;
           font-size: 11px; font-weight: 600; letter-spacing: 0.16px; }
    .tag-ok        { background: rgba(0,168,126,0.15);  color: #00a87e; }
    .tag-warn      { background: rgba(236,126,0,0.15);  color: #ec7e00; }
    .tag-err       { background: rgba(226,59,74,0.15);  color: #e23b4a; }
    .tag-muted     { background: rgba(141,150,158,0.15); color: #8d969e; }
    .tag-accent    { background: rgba(73,79,223,0.15);  color: #494fdf; }

    .btn { padding: 9px 20px; border-radius: 9999px; font-size: 13px; cursor: pointer;
           border: none; font-weight: 600; letter-spacing: 0.16px; color: #fff; }
    .btn-green { background: #00a87e; }
    .btn-red   { background: #e23b4a; }
    .btn-gray  { background: #2e3236; color: #8d969e; }
    .btn:hover { opacity: 0.85; }

    form.inline { display: inline; }

    .filter-bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
                  background: #242729; border-radius: 20px; padding: 14px 18px;
                  margin-bottom: 18px; }
    .filter-bar label { color: #8d969e; font-size: 12px; font-weight: 600; }
    .filter-bar select, .filter-bar input[type="date"] {
        background: #191c1f; color: #f4f4f4; border: 1px solid #2e3236;
        border-radius: 9999px; padding: 6px 14px; font-size: 13px;
        font-family: inherit;
    }
    .filter-bar select:hover, .filter-bar input:hover { border-color: #494fdf; }
    .footer { color: #8d969e; font-size: 11px; margin-top: 30px; padding-top: 8px;
              border-top: 1px solid #2a2d31; }
    code { background: #2a2d31; padding: 2px 6px; border-radius: 4px;
           font-family: ui-monospace, Menlo, monospace; font-size: 12px; color: #d0d7de; }
"""


def page(title: str, body_html: str, active: str = "", refresh_seconds: int = 15) -> str:
    """Full page shell matching the production dashboard style.

    ``active`` is one of "dashboard" | "ops" | "analytics" to highlight
    the current nav tab.
    """
    refresh = f'<meta http-equiv="refresh" content="{refresh_seconds}">' if refresh_seconds else ""
    def _a(href: str, label: str) -> str:
        cls = "active" if href.strip("/") == active else ""
        return f'<a class="{cls}" href="/{href}">{label}</a>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title} — SolanaTrader</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
{refresh}
<style>{_SHARED_CSS}</style>
</head>
<body>
  <nav class="nav">
    {_a("dashboard", "Dashboard")}
    {_a("ops", "Ops")}
    {_a("analytics", "Analytics")}
  </nav>
  <h1>{title}</h1>
  {body_html}
  <div class="footer">Auto-refresh every {refresh_seconds}s · <a href="/health">/health</a> · <a href="/metrics">/metrics</a></div>
</body>
</html>"""


def tag(label: str, kind: str = "muted") -> str:
    """kind: ok | warn | err | muted | accent."""
    return f'<span class="tag tag-{kind}">{label}</span>'


def card(label: str, value: str, sub: str = "", value_class: str = "") -> str:
    """Render one stat card.  ``value_class`` is appended to card-value (e.g. status-ok)."""
    vc = f"card-value {value_class}".strip()
    sub_html = f'<div class="card-sub">{sub}</div>' if sub else ""
    return f'<div class="card"><div class="card-title">{label}</div><div class="{vc}">{value}</div>{sub_html}</div>'


def tx_link(signature: str | None) -> str:
    if not signature:
        return '<span class="muted">—</span>'
    return f'<a href="{SOLSCAN}/tx/{signature}" target="_blank">{signature[:12]}…</a>'


def wallet_link(pubkey: str | None) -> str:
    if not pubkey:
        return '<span class="muted">—</span>'
    return f'<a href="{SOLSCAN}/account/{pubkey}" target="_blank">{pubkey[:8]}…{pubkey[-6:]}</a>'


# ---------------------------------------------------------------------------
# Small formatters
# ---------------------------------------------------------------------------

def fmt_sol(v, decimals: int = 6) -> str:
    if v is None or v == "":
        return '<span class="muted">—</span>'
    try:
        return f"{float(v):.{decimals}f} SOL"
    except (ValueError, TypeError):
        return str(v)


def fmt_num(v, decimals: int = 0) -> str:
    if v is None or v == "":
        return '<span class="muted">—</span>'
    try:
        return f"{float(v):,.{decimals}f}"
    except (ValueError, TypeError):
        return str(v)


def fmt_pct(v, decimals: int = 4) -> str:
    if v is None or v == "":
        return '<span class="muted">—</span>'
    try:
        return f"{float(v):.{decimals}f}%"
    except (ValueError, TypeError):
        return str(v)


def fmt_signed_sol(v, decimals: int = 6) -> str:
    try:
        f = float(v)
    except (ValueError, TypeError):
        return str(v) if v else '<span class="muted">—</span>'
    cls = "positive" if f >= 0 else "negative"
    sign = "+" if f > 0 else ""
    return f'<span class="{cls}">{sign}{f:.{decimals}f} SOL</span>'


# ---------------------------------------------------------------------------
# Filter parsing + rendering
# ---------------------------------------------------------------------------

def resolve_filters(filters: dict | None) -> dict:
    """Normalise a raw query-param dict into a usable filter dict.

    Output keys:
      - window:   one of the WINDOW_PRESETS keys, or "" for "all time"
      - since:    ISO datetime string (resolved from window if set, else from ``since`` param)
      - until:    ISO datetime string or empty
      - pair:     pair string or empty
      - status:   opportunity status or empty

    Priority: ``since``/``until`` query params override the window preset
    when both are supplied.
    """
    f = filters or {}
    window = (f.get("window") or "").strip()
    raw_since = (f.get("since") or "").strip()
    raw_until = (f.get("until") or "").strip()
    pair = (f.get("pair") or "").strip()
    status = (f.get("status") or "").strip()

    since = _coerce_to_iso(raw_since)
    until = _coerce_to_iso(raw_until)

    if window:
        td = dict(WINDOW_PRESETS).get(window)
        if td is not None:
            computed = (datetime.now(timezone.utc) - td).isoformat()
            # Explicit since/until win; window only fills in when caller didn't
            # pass one.
            if not since:
                since = computed

    return {
        "window": window,
        "since": since,
        "until": until,
        "pair": pair,
        "status": status,
    }


def _coerce_to_iso(raw: str) -> str:
    """Accept 'YYYY-MM-DD' or full ISO; return full ISO in UTC, or '' on failure."""
    if not raw:
        return ""
    try:
        # datetime.fromisoformat handles both "YYYY-MM-DD" and "YYYY-MM-DDTHH:MM:SS"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return ""


def filter_bar(
    filters: dict,
    active_page: str,
    pair_options: list[str] | None = None,
    show_status: bool = True,
) -> str:
    """Render the prod-style filter row.  Posts GET to the current page so
    selections round-trip via query params and can be bookmarked.
    """
    window = filters.get("window", "")
    pair = filters.get("pair", "")
    status = filters.get("status", "")
    since_input = (filters.get("since") or "")[:10]
    until_input = (filters.get("until") or "")[:10]

    window_opts = "".join(
        f'<option value="{k}"{" selected" if k == window else ""}>{k}</option>'
        for k, _ in WINDOW_PRESETS
    )
    pair_opts = "".join(
        f'<option value="{p}"{" selected" if p == pair else ""}>{p}</option>'
        for p in (pair_options or [])
    )
    status_opts = "".join(
        f'<option value="{s}"{" selected" if s == status else ""}>{s}</option>'
        for s in STATUS_OPTIONS
    ) if show_status else ""

    status_block = f"""
        <label>Status:</label>
        <select name="status">
          <option value=""{"" if status else " selected"}>All</option>
          {status_opts}
        </select>
    """ if show_status else ""

    return f"""
    <form class="filter-bar" method="get" action="/{active_page}">
      <label>Window:</label>
      <select name="window">
        <option value=""{"" if window else " selected"}>All time</option>
        {window_opts}
      </select>

      <label>From:</label>
      <input type="date" name="since" value="{since_input}">
      <label>To:</label>
      <input type="date" name="until" value="{until_input}">

      <label>Pair:</label>
      <select name="pair">
        <option value=""{"" if pair else " selected"}>All</option>
        {pair_opts}
      </select>

      {status_block}

      <button class="btn btn-green" type="submit">Apply</button>
      <a class="btn btn-gray" href="/{active_page}">Clear</a>
    </form>
    """
