"""Centralized logging for the arbitrage bot.

Writes to both console and a timestamped log file in the ``logs/`` folder.
Each scan/decision is logged as structured JSON so it can be parsed later
for analysis or ML training.

Log files are named by run start time: ``logs/bot_2026-04-12_21-30-00.log``

Usage in any module::

    from arbitrage_bot.log import get_logger, log_scan, log_execution

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
from pathlib import Path

from arbitrage_bot.models import ExecutionResult, MarketQuote, Opportunity

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
    json_logger = logging.getLogger("arbitrage_bot.data")
    json_logger.addHandler(json_h)
    json_logger.propagate = False  # don't echo raw JSON to console


def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given module name."""
    return logging.getLogger(name)


def _data_logger() -> logging.Logger:
    return logging.getLogger("arbitrage_bot.data")


# ---------------------------------------------------------------------------
# Structured log helpers — write JSON lines to the data log
# ---------------------------------------------------------------------------

def _quote_to_dict(q: MarketQuote) -> dict:
    return {
        "dex": q.dex,
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
    _data_logger().info(json.dumps(record))


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
    _data_logger().info(json.dumps(record))


def log_swap_event(
    logger: logging.Logger,
    chain: str,
    block_number: int,
    swap_count: int,
) -> None:
    """Log detected swap events."""
    record = {
        "event": "swap_detected",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "chain": chain,
        "block_number": block_number,
        "swap_count": swap_count,
    }
    _data_logger().info(json.dumps(record))


def log_discovery(
    logger: logging.Logger,
    discovered_pairs: list,
) -> None:
    """Log the pair discovery results from DexScreener."""
    pairs_data = []
    for p in discovered_pairs:
        pairs_data.append({
            "pair": p.pair,
            "base_asset": p.base_asset,
            "quote_asset": p.quote_asset,
            "trade_size": p.trade_size,
        })
    record = {
        "event": "discovery",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pair_count": len(discovered_pairs),
        "pairs": pairs_data,
    }
    _data_logger().info(json.dumps(record))


def log_discovery_detail(
    logger: logging.Logger,
    pair_name: str,
    dex_count: int,
    total_volume_usd: float,
    dex_names: list[str],
    chains: list[str],
) -> None:
    """Log detailed info for a single discovered pair."""
    record = {
        "event": "discovery_detail",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pair": pair_name,
        "dex_count": dex_count,
        "total_volume_usd": total_volume_usd,
        "dex_names": dex_names,
        "chains": chains,
    }
    _data_logger().info(json.dumps(record))


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
    _data_logger().info(json.dumps(record))
