"""SimulatedMarket (Solana pairs) tests."""

import json
from decimal import Decimal
from pathlib import Path

from core.config import BotConfig
from market.sim_market import SimulatedMarket


def _cfg(tmp_path: Path) -> BotConfig:
    data = {
        "pair": "SOL/USDC",
        "base_asset": "SOL",
        "quote_asset": "USDC",
        "trade_size": 1.0,
        "min_profit_base": 0.0,
        "priority_fee_lamports": 10000,
        "slippage_bps": 10,
        "poll_interval_seconds": 0.1,
        "extra_pairs": [
            {"pair": "USDC/USDT", "base_asset": "USDC", "quote_asset": "USDT",
             "trade_size": 100.0},
        ],
        "venues": [
            {"name": "Jupiter-Best", "fee_bps": 0},
            {"name": "Jupiter-Direct", "fee_bps": 0},
        ],
    }
    p = tmp_path / "c.json"
    p.write_text(json.dumps(data))
    return BotConfig.from_file(p)


def test_produces_quotes_for_all_pairs_and_venues(tmp_path):
    cfg = _cfg(tmp_path)
    market = SimulatedMarket(cfg, seed=1)
    quotes = market.get_quotes()
    # 2 pairs × 2 venues = 4 quotes per cycle
    assert len(quotes) == 4
    pairs = {q.pair for q in quotes}
    assert pairs == {"SOL/USDC", "USDC/USDT"}
    venues = {q.venue for q in quotes}
    assert venues == {"Jupiter-Best", "Jupiter-Direct"}


def test_prices_are_in_reasonable_range(tmp_path):
    cfg = _cfg(tmp_path)
    market = SimulatedMarket(cfg, seed=1)
    quotes = market.get_quotes()
    by_pair: dict[str, list] = {}
    for q in quotes:
        by_pair.setdefault(q.pair, []).append(q)
    # SOL/USDC base ~165
    sol_usdc = by_pair["SOL/USDC"]
    for q in sol_usdc:
        assert Decimal("100") < q.buy_price < Decimal("250")
    # USDC/USDT base ~1
    usdc_usdt = by_pair["USDC/USDT"]
    for q in usdc_usdt:
        assert Decimal("0.9") < q.buy_price < Decimal("1.1")


def test_walk_changes_price(tmp_path):
    cfg = _cfg(tmp_path)
    market = SimulatedMarket(cfg, seed=1)
    first = {q.venue + ":" + q.pair: q.buy_price for q in market.get_quotes()}
    second = {q.venue + ":" + q.pair: q.buy_price for q in market.get_quotes()}
    # At least one price should have moved after one tick.
    assert any(first[k] != second[k] for k in first)
