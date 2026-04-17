"""OpportunityScanner (Solana) tests."""

import json
from decimal import Decimal
from pathlib import Path

from core.config import BotConfig
from core.models import MarketQuote
from strategy.scanner import OpportunityScanner

D = Decimal


def _cfg(tmp_path: Path) -> BotConfig:
    data = {
        "pair": "SOL/USDC",
        "base_asset": "SOL",
        "quote_asset": "USDC",
        "trade_size": 1.0,
        "min_profit_base": 0.0,
        "priority_fee_lamports": 10000,
        "slippage_bps": 0,
        "poll_interval_seconds": 0.1,
        "venues": [
            {"name": "Jupiter-Best", "fee_bps": 0},
            {"name": "Jupiter-Direct", "fee_bps": 0},
        ],
    }
    p = tmp_path / "c.json"
    p.write_text(json.dumps(data))
    return BotConfig.from_file(p)


def _q(venue: str, price: str, liq: str = "1000000") -> MarketQuote:
    return MarketQuote(
        venue=venue, pair="SOL/USDC",
        buy_price=D(price), sell_price=D(price),
        fee_bps=D("0"), fee_included=True,
        liquidity_usd=D(liq),
        venue_type="aggregator",
    )


def test_scanner_ranks_opportunities(tmp_path):
    cfg = _cfg(tmp_path)
    scanner = OpportunityScanner(cfg, min_liquidity_usd=D("0"))
    quotes = [_q("Jupiter-Direct", "165.00"), _q("Jupiter-Best", "166.50")]
    result = scanner.scan_and_rank(quotes)
    assert result.total_quotes == 2
    assert result.best is not None
    assert result.best.buy_venue == "Jupiter-Direct"
    assert result.best.sell_venue == "Jupiter-Best"


def test_scanner_skips_same_venue(tmp_path):
    cfg = _cfg(tmp_path)
    scanner = OpportunityScanner(cfg, min_liquidity_usd=D("0"))
    quotes = [
        _q("Jupiter-Direct", "165.00"),
        _q("Jupiter-Direct", "165.50"),   # same venue duplicate
    ]
    result = scanner.scan_and_rank(quotes)
    assert result.best is None


def test_scanner_skips_low_liquidity(tmp_path):
    cfg = _cfg(tmp_path)
    scanner = OpportunityScanner(cfg, min_liquidity_usd=D("500000"))
    quotes = [
        _q("Jupiter-Direct", "165.00", liq="100000"),
        _q("Jupiter-Best", "166.50", liq="100000"),
    ]
    result = scanner.scan_and_rank(quotes)
    # Liquidity is below the threshold — scanner should reject.
    assert result.best is None
    assert result.rejected_count == 0  # no opportunities scored


def test_scanner_skips_price_deviation(tmp_path):
    cfg = _cfg(tmp_path)
    scanner = OpportunityScanner(cfg, min_liquidity_usd=D("0"),
                                 max_price_deviation=D("0.01"))
    # 160 vs 167 — the 160 quote deviates >5% from the median.
    quotes = [_q("Jupiter-Direct", "160.00"), _q("Jupiter-Best", "167.00")]
    result = scanner.scan_and_rank(quotes)
    assert result.best is None


def test_scan_records_drained(tmp_path):
    cfg = _cfg(tmp_path)
    scanner = OpportunityScanner(cfg, min_liquidity_usd=D("0"))
    quotes = [_q("Jupiter-Direct", "165.00"), _q("Jupiter-Best", "166.50")]
    scanner.scan_and_rank(quotes)
    records = scanner.drain_scan_records()
    assert records
    assert all("pair" in r for r in records)
    # Second drain is empty.
    assert scanner.drain_scan_records() == []
