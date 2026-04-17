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
