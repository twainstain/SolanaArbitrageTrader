"""BotConfig / VenueConfig / PairConfig tests."""

from decimal import Decimal
from pathlib import Path

import pytest

from core.config import BotConfig, PairConfig, VenueConfig

D = Decimal


def _write(tmp_path: Path, data: dict) -> Path:
    import json
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(data))
    return path


def _good() -> dict:
    return {
        "pair": "SOL/USDC",
        "base_asset": "SOL",
        "quote_asset": "USDC",
        "trade_size": 1.0,
        "min_profit_base": 0.002,
        "priority_fee_lamports": 10000,
        "slippage_bps": 20,
        "poll_interval_seconds": 0.5,
        "venues": [
            {"name": "Jupiter-Best", "fee_bps": 0, "min_liquidity_usd": 100000},
            {"name": "Jupiter-Direct", "fee_bps": 0, "min_liquidity_usd": 100000},
        ],
    }


def test_load_and_validate(tmp_path):
    cfg = BotConfig.from_file(_write(tmp_path, _good()))
    assert cfg.pair == "SOL/USDC"
    assert cfg.trade_size == D("1.0")
    assert cfg.min_profit_base == D("0.002")
    assert cfg.priority_fee_lamports == 10000
    assert len(cfg.venues) == 2


def test_decimal_coercion(tmp_path):
    cfg = BotConfig.from_file(_write(tmp_path, _good()))
    assert isinstance(cfg.slippage_bps, Decimal)
    assert isinstance(cfg.venues[0].fee_bps, Decimal)


def test_priority_fee_sol_helper(tmp_path):
    cfg = BotConfig.from_file(_write(tmp_path, _good()))
    assert cfg.priority_fee_sol() == D("0.00001")


def test_extra_pairs(tmp_path):
    data = _good()
    data["extra_pairs"] = [
        {"pair": "USDC/USDT", "base_asset": "USDC", "quote_asset": "USDT",
         "trade_size": 100.0, "max_exposure": 1000.0},
    ]
    cfg = BotConfig.from_file(_write(tmp_path, data))
    assert len(cfg.extra_pairs) == 1
    assert cfg.extra_pairs[0].max_exposure == D("1000.0")


def test_rejects_too_few_venues(tmp_path):
    data = _good()
    data["venues"] = data["venues"][:1]
    with pytest.raises(ValueError):
        BotConfig.from_file(_write(tmp_path, data))


def test_rejects_negative_trade_size(tmp_path):
    data = _good()
    data["trade_size"] = 0
    with pytest.raises(ValueError):
        BotConfig.from_file(_write(tmp_path, data))


def test_rejects_negative_priority_fee(tmp_path):
    data = _good()
    data["priority_fee_lamports"] = -1
    with pytest.raises(ValueError):
        BotConfig.from_file(_write(tmp_path, data))


def test_pair_config_decimal_coercion():
    pc = PairConfig(pair="SOL/USDC", base_asset="SOL", quote_asset="USDC",
                    trade_size=1.5, max_exposure=50)
    assert isinstance(pc.trade_size, Decimal)
    assert pc.max_exposure == D("50")


def test_venue_config_decimal_coercion():
    vc = VenueConfig(name="Jupiter", fee_bps=10)
    assert isinstance(vc.fee_bps, Decimal)
