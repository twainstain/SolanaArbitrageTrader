"""Core model tests — MarketQuote, Opportunity, ExecutionResult."""

from decimal import Decimal

from core.models import (
    ZERO,
    ExecutionResult,
    MarketQuote,
    Opportunity,
    OpportunityStatus,
)

D = Decimal


def test_market_quote_coerces_decimal():
    q = MarketQuote(venue="Jupiter-Best", pair="SOL/USDC",
                    buy_price=165.5, sell_price=165.4, fee_bps=0)
    assert isinstance(q.buy_price, Decimal)
    assert q.venue == "Jupiter-Best"
    # dex alias still works for legacy call sites
    assert q.dex == q.venue


def test_opportunity_defaults():
    opp = Opportunity(
        pair="SOL/USDC",
        buy_venue="Jupiter-Direct",
        sell_venue="Jupiter-Best",
        trade_size=1,
        cost_to_buy_quote=165,
        proceeds_from_sell_quote=166,
        gross_profit_quote=1,
        net_profit_quote=0.9,
        net_profit_base=0.005,
    )
    assert opp.pair == "SOL/USDC"
    assert opp.is_cross_chain is False   # Solana is single-chain
    assert opp.fee_cost_base == ZERO
    assert opp.warning_flags == ()


def test_execution_result_signature_field():
    opp = Opportunity(
        pair="SOL/USDC", buy_venue="A", sell_venue="B",
        trade_size=1, cost_to_buy_quote=1, proceeds_from_sell_quote=1,
        gross_profit_quote=0, net_profit_quote=0, net_profit_base=0,
    )
    r = ExecutionResult(
        success=True, reason="paper",
        realized_profit_base=D("0"), opportunity=opp,
        signature="5Yn…", confirmation_slot=12345,
    )
    assert r.signature == "5Yn…"
    assert r.confirmation_slot == 12345


def test_opportunity_status_solana_values():
    # Phase 3+ statuses are Solana-native (confirmed/dropped).
    assert OpportunityStatus.CONFIRMED == "confirmed"
    assert OpportunityStatus.DROPPED == "dropped"
    # Dry-run is still the terminal state in Phase 1 scanner-only.
    assert OpportunityStatus.DRY_RUN == "dry_run"
