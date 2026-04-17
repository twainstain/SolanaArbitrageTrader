"""ArbitrageStrategy tests — Solana math."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from core.config import BotConfig
from core.models import MarketQuote
from strategy.arb_strategy import ArbitrageStrategy

D = Decimal


def _cfg(tmp_path: Path, min_profit: float = 0.0) -> BotConfig:
    data = {
        "pair": "SOL/USDC",
        "base_asset": "SOL",
        "quote_asset": "USDC",
        "trade_size": 1.0,
        "min_profit_base": min_profit,
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


def _quote(venue: str, buy: str, sell: str | None = None,
           fee_included: bool = True) -> MarketQuote:
    return MarketQuote(
        venue=venue, pair="SOL/USDC",
        buy_price=D(buy), sell_price=D(sell or buy),
        fee_bps=D("0"), fee_included=fee_included,
        liquidity_usd=D("1000000"),
        venue_type="aggregator",
    )


def test_detects_profitable_opportunity(tmp_path):
    cfg = _cfg(tmp_path)
    strat = ArbitrageStrategy(cfg)
    buy = _quote("Jupiter-Direct", "165.00")
    sell = _quote("Jupiter-Best", "166.50")
    opp = strat.evaluate_pair(buy, sell)
    assert opp is not None
    assert opp.buy_venue == "Jupiter-Direct"
    assert opp.sell_venue == "Jupiter-Best"
    # 1 SOL: 166.50 - 165.00 = 1.50 USDC gross, divided by ~165.75 ≈ 0.00905 SOL
    # minus ~0.00001 priority fee = ~0.00904 SOL net.
    assert opp.net_profit_base > D("0.008")
    assert opp.fee_cost_base == D("0.00001")


def test_rejects_below_min_profit(tmp_path):
    cfg = _cfg(tmp_path, min_profit=0.5)
    strat = ArbitrageStrategy(cfg)
    buy = _quote("Jupiter-Direct", "165.00")
    sell = _quote("Jupiter-Best", "165.10")   # tiny spread
    assert strat.evaluate_pair(buy, sell) is None


def test_find_best_opportunity_from_quotes(tmp_path):
    cfg = _cfg(tmp_path)
    strat = ArbitrageStrategy(cfg)
    quotes = [
        _quote("Jupiter-Direct", "165.00"),
        _quote("Jupiter-Best", "166.50"),
    ]
    best = strat.find_best_opportunity(quotes)
    assert best is not None
    assert best.sell_venue == "Jupiter-Best"


def test_opportunity_carries_warning_flags(tmp_path):
    cfg = _cfg(tmp_path)
    strat = ArbitrageStrategy(cfg)
    # No liquidity → low_liquidity flag should trigger when one side is < $100k.
    buy = MarketQuote(venue="V1", pair="SOL/USDC", buy_price=D("165"),
                      sell_price=D("165"), fee_bps=D("0"), fee_included=True,
                      liquidity_usd=D("50000"), venue_type="aggregator")
    sell = MarketQuote(venue="V2", pair="SOL/USDC", buy_price=D("166.5"),
                       sell_price=D("166.5"), fee_bps=D("0"), fee_included=True,
                       liquidity_usd=D("50000"), venue_type="aggregator")
    opp = strat.evaluate_pair(buy, sell)
    assert opp is not None
    assert "low_liquidity" in opp.warning_flags
