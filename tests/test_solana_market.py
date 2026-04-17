"""SolanaMarket (Jupiter adapter) tests — fully mocked, no network."""

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from core.config import BotConfig
from core.models import MarketQuote
from market.solana_market import SolanaMarket


def _cfg(tmp_path: Path) -> BotConfig:
    data = {
        "pair": "SOL/USDC",
        "base_asset": "SOL",
        "quote_asset": "USDC",
        "trade_size": 1.0,
        "min_profit_base": 0.0,
        "priority_fee_lamports": 10000,
        "slippage_bps": 20,
        "poll_interval_seconds": 0.1,
        "venues": [
            {"name": "Jupiter-Best", "fee_bps": 0},
            {"name": "Jupiter-Direct", "fee_bps": 0},
        ],
    }
    p = tmp_path / "c.json"
    p.write_text(json.dumps(data))
    return BotConfig.from_file(p)


def _mock_session(best_out: int, direct_out: int) -> MagicMock:
    """Build a mock requests.Session that returns two different Jupiter quotes."""
    call_count = {"n": 0}
    session = MagicMock()

    def _get(url, params=None, timeout=None):
        call_count["n"] += 1
        # First call is multi-hop "best", second is direct (onlyDirectRoutes=true).
        is_direct = (params or {}).get("onlyDirectRoutes") == "true"
        out_amount = direct_out if is_direct else best_out
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={
            "inAmount": str(1_000_000_000),   # 1 SOL
            "outAmount": str(out_amount),     # USDC (6 decimals)
            "priceImpactPct": "0.001",
        })
        return resp

    session.get = MagicMock(side_effect=_get)
    session.headers = {}
    return session


def test_two_quotes_per_pair_from_jupiter(tmp_path):
    cfg = _cfg(tmp_path)
    market = SolanaMarket(cfg)
    # Inject mock session before calling get_quotes.
    market._session = _mock_session(best_out=166_500_000, direct_out=165_800_000)

    quotes = market.get_quotes()
    assert len(quotes) == 2
    venues = {q.venue for q in quotes}
    assert venues == {"Jupiter-Best", "Jupiter-Direct"}

    # 1 SOL → 166.5 USDC and 165.8 USDC respectively.
    best = next(q for q in quotes if q.venue == "Jupiter-Best")
    direct = next(q for q in quotes if q.venue == "Jupiter-Direct")
    assert best.buy_price == Decimal("166.5")
    assert direct.buy_price == Decimal("165.8")
    # Jupiter output already nets fees.
    assert best.fee_included is True
    assert best.fee_bps == Decimal("0")
    # Venue type for aggregator quotes.
    assert best.venue_type == "aggregator"


def test_per_pair_failure_is_tolerated(tmp_path):
    cfg = _cfg(tmp_path)
    market = SolanaMarket(cfg)
    # Session that always raises — both requests fail.
    session = MagicMock()
    session.headers = {}
    session.get = MagicMock(side_effect=RuntimeError("boom"))
    market._session = session

    # Should not raise; returns empty list instead of crashing the loop.
    assert market.get_quotes() == []


def test_zero_output_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    market = SolanaMarket(cfg)
    session = MagicMock()
    session.headers = {}
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"inAmount": "1000", "outAmount": "0"})
    session.get = MagicMock(return_value=resp)
    market._session = session

    # Both responses are zero-output; the adapter logs the error and returns [].
    assert market.get_quotes() == []


# ---------------------------------------------------------------------------
# Rate-limit cooldown (Phase 2d).
# ---------------------------------------------------------------------------


def test_429_puts_pair_in_cooldown(tmp_path):
    from unittest.mock import patch
    from market.solana_market import RateLimitedError

    cfg = _cfg(tmp_path)
    # Two pairs — the 429 on the first should NOT stop the second from running.
    from core.config import PairConfig
    cfg2 = _cfg(tmp_path)
    # Monkey-patch two pairs onto the market.
    market = SolanaMarket(cfg)
    market.pairs = [
        PairConfig("SOL/USDC", "SOL", "USDC", Decimal("1")),
        PairConfig("USDC/USDT", "USDC", "USDT", Decimal("100")),
    ]

    call_log: list[tuple] = []

    def fake_get(url, params=None, timeout=None):
        in_mint = params["inputMint"]
        call_log.append(in_mint[:6])
        r = MagicMock()
        # 429 only when the input mint is SOL's.
        r.status_code = 429 if in_mint.startswith("So1111") else 200
        r.raise_for_status = MagicMock()
        r.json = MagicMock(return_value={
            "outAmount": "99000000",           # 99 USDT back for 100 USDC
            "inAmount": params["amount"],
            "priceImpactPct": "0.0",
            "slippageBps": 0,
            "platformFee": None,
            "routePlan": [{"swapInfo": {"label": "Orca", "feeAmount": "0", "feeBps": "1"}}],
        })
        return r

    with patch.object(market._session, "get", side_effect=fake_get):
        quotes = market.get_quotes()

    # SOL/USDC 429'd; USDC/USDT succeeded. Both routes were attempted for
    # each pair; the first 429 short-circuits SOL/USDC's second call (cooldown).
    assert market._pair_cooldown_until.get("SOL/USDC", 0) > 0
    # USDC/USDT isn't in cooldown.
    assert "USDC/USDT" not in market._pair_cooldown_until
    # USDC/USDT emitted 2 quotes (Best + Direct).
    usdc_usdt_quotes = [q for q in quotes if q.pair == "USDC/USDT"]
    assert len(usdc_usdt_quotes) == 2


def test_pair_in_cooldown_is_skipped(tmp_path):
    from unittest.mock import patch
    import time as _time

    cfg = _cfg(tmp_path)
    market = SolanaMarket(cfg)
    # Put SOL/USDC in cooldown until the heat death of the test.
    market._pair_cooldown_until["SOL/USDC"] = _time.time() + 10_000

    call_log: list = []
    def fake_get(url, params=None, timeout=None):
        call_log.append(params["inputMint"])
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status = MagicMock()
        r.json = MagicMock(return_value={
            "outAmount": "90000000", "inAmount": "1000000000",
            "priceImpactPct": "0.0", "slippageBps": 0, "platformFee": None,
            "routePlan": [{"swapInfo": {"label": "Orca", "feeAmount": "0", "feeBps": "1"}}],
        })
        return r

    with patch.object(market._session, "get", side_effect=fake_get):
        quotes = market.get_quotes()

    # No HTTP calls were made — the cooled-down pair was skipped.
    assert call_log == []
    assert quotes == []


def test_cooldown_expires_naturally(tmp_path):
    from unittest.mock import patch
    import time as _time

    cfg = _cfg(tmp_path)
    market = SolanaMarket(cfg)
    # Set cooldown in the past → should be ignored.
    market._pair_cooldown_until["SOL/USDC"] = _time.time() - 1

    def fake_get(url, params=None, timeout=None):
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status = MagicMock()
        r.json = MagicMock(return_value={
            "outAmount": "90000000", "inAmount": "1000000000",
            "priceImpactPct": "0.0", "slippageBps": 0, "platformFee": None,
            "routePlan": [{"swapInfo": {"label": "Orca", "feeAmount": "0", "feeBps": "1"}}],
        })
        return r

    with patch.object(market._session, "get", side_effect=fake_get):
        quotes = market.get_quotes()

    # Cooldown had expired → 2 quotes (Best + Direct) produced.
    assert len(quotes) == 2
