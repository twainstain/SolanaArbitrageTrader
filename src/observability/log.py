"""Centralized logging for the arbitrage bot.

Writes to both console and a timestamped log file in the ``logs/`` folder.
Each scan/decision is logged as structured JSON so it can be parsed later
for analysis or ML training.

Log files are named by run start time: ``logs/bot_2026-04-12_21-30-00.log``

Usage in any module::

    from observability.log import get_logger, log_scan, log_execution

    logger = get_logger(__name__)
    logger.info("Starting bot")
    log_scan(logger, scan_index=1, quotes=quotes, opportunity=opp)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from core.models import ExecutionResult, MarketQuote, Opportunity


class _DecimalEncoder(json.JSONEncoder):
    """JSON encoder that serializes Decimal as string to preserve precision."""

    def default(self, o: object) -> object:
        if isinstance(o, Decimal):
            return str(o)
        return super().default(o)

# ---------------------------------------------------------------------------
# Log directory — resolve relative to project root
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = _PROJECT_ROOT / "logs"

_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger with console + file handlers.

    Call once at startup (main.py / event_listener.py).  Safe to call
    multiple times — subsequent calls are no-ops.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    log_file = LOG_DIR / f"bot_{timestamp}.log"

    fmt = "%(asctime)s  %(name)-25s  %(levelname)-7s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    # Root logger
    root = logging.getLogger()
    root.setLevel(level)

    # Console handler — human-readable
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(console)

    # File handler — same format, persisted
    file_h = logging.FileHandler(str(log_file), encoding="utf-8")
    file_h.setLevel(level)
    file_h.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(file_h)

    # JSON data file — one JSON object per line for structured analysis
    json_file = LOG_DIR / f"bot_{timestamp}.jsonl"
    json_h = logging.FileHandler(str(json_file), encoding="utf-8")
    json_h.setLevel(logging.INFO)
    json_h.setFormatter(logging.Formatter("%(message)s"))
    json_logger = logging.getLogger("bot_data")
    json_logger.addHandler(json_h)
    json_logger.propagate = False  # don't echo raw JSON to console


def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given module name."""
    return logging.getLogger(name)


def _data_logger() -> logging.Logger:
    return logging.getLogger("bot_data")


# ---------------------------------------------------------------------------
# Structured log helpers — write JSON lines to the data log
# ---------------------------------------------------------------------------

def _json_dumps(obj: object) -> str:
    """Serialize to JSON, handling Decimal values."""
    return json.dumps(obj, cls=_DecimalEncoder)


def _quote_to_dict(q: MarketQuote) -> dict:
    return {
        "venue": q.venue,
        "pair": q.pair,
        "buy_price": q.buy_price,
        "sell_price": q.sell_price,
        "fee_bps": q.fee_bps,
        "volume_usd": q.volume_usd,
        "liquidity_usd": q.liquidity_usd,
        "venue_type": q.venue_type,
    }


def _opp_to_dict(opp: Opportunity) -> dict:
    return asdict(opp)


def log_scan(
    logger: logging.Logger,
    scan_index: int,
    quotes: list[MarketQuote],
    opportunity: Opportunity | None,
    decision: str,
) -> None:
    """Log a scan iteration with all quotes and the decision made."""
    record = {
        "event": "scan",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scan_index": scan_index,
        "quotes": [_quote_to_dict(q) for q in quotes],
        "opportunity": _opp_to_dict(opportunity) if opportunity else None,
        "decision": decision,
    }
    _data_logger().info(_json_dumps(record))


def log_execution(
    logger: logging.Logger,
    scan_index: int,
    result: ExecutionResult,
) -> None:
    """Log an execution result."""
    record = {
        "event": "execution",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scan_index": scan_index,
        "success": result.success,
        "reason": result.reason,
        "realized_profit_base": result.realized_profit_base,
        "opportunity": _opp_to_dict(result.opportunity),
    }
    _data_logger().info(_json_dumps(record))


def log_slot_event(
    logger: logging.Logger,
    slot: int,
    venue: str,
    event: str,
) -> None:
    """Log Solana slot/venue-level events (e.g. route health changes)."""
    record = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "slot": slot,
        "venue": venue,
    }
    _data_logger().info(_json_dumps(record))


def log_summary(
    logger: logging.Logger,
    mode: str,
    total_scans: int,
    opportunities_found: int,
    executed_count: int,
    total_realized_profit: float,
    base_asset: str,
) -> None:
    """Log the run summary."""
    record = {
        "event": "summary",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "total_scans": total_scans,
        "opportunities_found": opportunities_found,
        "executed_count": executed_count,
        "total_realized_profit": total_realized_profit,
        "base_asset": base_asset,
    }
    _data_logger().info(_json_dumps(record))
