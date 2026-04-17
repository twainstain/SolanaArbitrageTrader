"""Persistence smoke tests — Solana-native schema."""

from decimal import Decimal

from persistence.db import init_db
from persistence.repository import Repository

D = Decimal


def test_create_and_update_opportunity(tmp_path):
    db = init_db(str(tmp_path / "t.db"))
    repo = Repository(db)
    opp_id = repo.create_opportunity(
        pair="SOL/USDC", buy_venue="Jupiter-Direct", sell_venue="Jupiter-Best",
        spread_bps=D("0.45"),
    )
    assert opp_id.startswith("opp_")
    opp = repo.get_opportunity(opp_id)
    assert opp is not None
    assert opp["status"] == "detected"
    assert opp["buy_venue"] == "Jupiter-Direct"
    repo.update_opportunity_status(opp_id, "approved")
    assert repo.get_opportunity(opp_id)["status"] == "approved"


def test_full_lifecycle_persists_solana_fields(tmp_path):
    db = init_db(str(tmp_path / "t.db"))
    repo = Repository(db)
    opp_id = repo.create_opportunity(
        pair="SOL/USDC", buy_venue="V1", sell_venue="V2", spread_bps=D("0.5"),
    )
    repo.save_pricing(
        opp_id=opp_id,
        input_amount=D("165"),
        estimated_output=D("166.5"),
        fee_cost=D("0"),
        slippage_cost=D("0.1"),
        fee_estimate_base=D("0.00001"),
        expected_net_profit=D("0.009"),
    )
    repo.save_risk_decision(opp_id=opp_id, approved=True, reason_code="approved",
                            threshold_snapshot={"min_net_profit": "0.005"})
    exec_id = repo.save_execution_attempt(
        opp_id=opp_id, submission_kind="rpc", signature="5SigBase58",
        metadata={"priority_fee_lamports": 10000},
    )
    repo.save_trade_result(
        execution_id=exec_id,
        included=True,
        reverted=False,
        dropped=False,
        fee_paid_lamports=12345,
        realized_profit_quote=D("1.48"),
        fee_paid_base=D("0.00001234"),
        actual_net_profit=D("0.0089"),
        confirmation_slot=987654321,
        profit_currency="USDC",
    )

    summary = repo.get_pnl_summary()
    assert summary["total_trades"] == 1
    assert summary["successful"] == 1
    assert summary["total_fee_paid_lamports"] == 12345


def test_scan_history_persists_solana_columns(tmp_path):
    db = init_db(str(tmp_path / "t.db"))
    repo = Repository(db)
    repo.save_scan_history([
        {"pair": "SOL/USDC", "buy_venue": "V1", "sell_venue": "V2",
         "buy_price": "165", "sell_price": "166", "spread_bps": "0.6",
         "gross_profit": "1", "net_profit": "0.5",
         "fee_cost": "0.00001", "venue_fee_cost": "0", "slippage_cost": "0.1",
         "filter_reason": "passed", "passed": True},
    ])
    rows = repo.get_scan_history(limit=10)
    assert len(rows) == 1
    assert rows[0]["buy_venue"] == "V1"
    assert rows[0]["filter_reason"] == "passed"


def test_venue_and_pair_registries(tmp_path):
    db = init_db(str(tmp_path / "t.db"))
    repo = Repository(db)
    repo.save_venue("Jupiter-Best", kind="aggregator")
    repo.save_venue("Jupiter-Direct", kind="aggregator")
    assert len(repo.get_enabled_venues()) == 2

    repo.save_pair(
        pair="SOL/USDC", base_symbol="SOL", quote_symbol="USDC",
        base_mint="So111...", quote_mint="EPj...",
    )
    assert len(repo.get_enabled_pairs()) == 1
