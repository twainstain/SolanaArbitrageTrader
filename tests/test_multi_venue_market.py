"""MultiVenueMarket fan-out tests."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from core.models import MarketQuote
from market.multi_venue_market import MultiVenueMarket

D = Decimal


def _stub(quotes: list[MarketQuote]):
    m = MagicMock()
    m.get_quotes = MagicMock(return_value=quotes)
    return m


def _q(venue: str, price: str) -> MarketQuote:
    return MarketQuote(
        venue=venue, pair="SOL/USDC",
        buy_price=D(price), sell_price=D(price),
        fee_bps=D("0"), fee_included=True,
        venue_type="aggregator",
    )


def test_combines_all_backends():
    mv = MultiVenueMarket([
        ("A", _stub([_q("A", "100")])),
        ("B", _stub([_q("B", "101")])),
        ("C", _stub([_q("C", "102")])),
    ])
    quotes = mv.get_quotes()
    assert len(quotes) == 3
    venues = sorted(q.venue for q in quotes)
    assert venues == ["A", "B", "C"]
    mv.close()


def test_one_backend_failure_does_not_break_the_rest():
    bad = MagicMock()
    bad.get_quotes = MagicMock(side_effect=RuntimeError("boom"))
    mv = MultiVenueMarket([
        ("Good", _stub([_q("G", "100")])),
        ("Bad",  bad),
    ])
    quotes = mv.get_quotes()
    assert len(quotes) == 1
    assert quotes[0].venue == "G"
    mv.close()


def test_no_backends_returns_empty():
    mv = MultiVenueMarket([])
    assert mv.get_quotes() == []
    mv.close()


def test_each_backend_called_once_per_scan():
    a = _stub([])
    b = _stub([])
    mv = MultiVenueMarket([("A", a), ("B", b)])
    mv.get_quotes()
    mv.get_quotes()
    assert a.get_quotes.call_count == 2
    assert b.get_quotes.call_count == 2
    mv.close()


# ---------------------------------------------------------------------------
# Per-venue latency timings (perf instrumentation).
# ---------------------------------------------------------------------------


def test_last_venue_timings_populated_on_success():
    from market.multi_venue_market import MultiVenueMarket

    class _FakeSource:
        def __init__(self, quotes):
            self._quotes = quotes
        def get_quotes(self):
            return self._quotes

    m = MultiVenueMarket([
        ("Jupiter", _FakeSource(["q1"])),
        ("Orca", _FakeSource(["q2", "q3"])),
    ])
    quotes = m.get_quotes()
    assert sorted(quotes) == ["q1", "q2", "q3"]
    assert set(m.last_venue_timings_ms) == {"Jupiter", "Orca"}
    # Times are non-negative floats (just-ran workers usually clock under 1ms
    # but can be slightly higher on CI).
    for name, ms in m.last_venue_timings_ms.items():
        assert ms >= 0
        assert ms < 1000


def test_last_venue_timings_still_recorded_on_error():
    from market.multi_venue_market import MultiVenueMarket

    class _BoomSource:
        def get_quotes(self):
            raise RuntimeError("venue down")

    class _OkSource:
        def get_quotes(self):
            return ["ok"]

    m = MultiVenueMarket([
        ("Raydium", _BoomSource()),
        ("Orca", _OkSource()),
    ])
    quotes = m.get_quotes()
    assert quotes == ["ok"]
    # Both venues logged — caller can see the failed one's latency too.
    assert set(m.last_venue_timings_ms) == {"Raydium", "Orca"}


def test_last_venue_timings_reset_per_scan():
    from market.multi_venue_market import MultiVenueMarket

    class _Src:
        def get_quotes(self):
            return []

    m = MultiVenueMarket([("Jupiter", _Src())])
    m.get_quotes()
    assert "Jupiter" in m.last_venue_timings_ms
    # Second call starts fresh — even if the venue list changed.
    m.backends = []
    m.get_quotes()
    assert m.last_venue_timings_ms == {}
