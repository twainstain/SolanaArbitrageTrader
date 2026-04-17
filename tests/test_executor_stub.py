"""SolanaExecutor safety gates — must refuse unless env is explicitly opted-in."""

from __future__ import annotations

import json
import os

import pytest

from core.config import BotConfig
from execution.solana_executor import SolanaExecutor


def _cfg(tmp_path):
    data = {
        "pair": "SOL/USDC", "base_asset": "SOL", "quote_asset": "USDC",
        "trade_size": 1.0, "min_profit_base": 0.001,
        "priority_fee_lamports": 10000, "slippage_bps": 20,
        "poll_interval_seconds": 0.1,
        "venues": [
            {"name": "Jupiter-Best", "fee_bps": 0},
            {"name": "Jupiter-Direct", "fee_bps": 0},
        ],
    }
    p = tmp_path / "c.json"
    p.write_text(json.dumps(data))
    return BotConfig.from_file(p)


def test_refuses_without_env_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("SOLANA_EXECUTION_ENABLED", raising=False)
    monkeypatch.delenv("SOLANA_WALLET_KEYPAIR_PATH", raising=False)
    with pytest.raises(RuntimeError, match="SOLANA_EXECUTION_ENABLED"):
        SolanaExecutor(_cfg(tmp_path))


def test_refuses_without_wallet_path(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLANA_EXECUTION_ENABLED", "true")
    monkeypatch.delenv("SOLANA_WALLET_KEYPAIR_PATH", raising=False)
    with pytest.raises(RuntimeError, match="SOLANA_WALLET_KEYPAIR_PATH"):
        SolanaExecutor(_cfg(tmp_path))


def test_refuses_with_kill_switch(tmp_path, monkeypatch):
    # Arrange: env gates pass + fake wallet path
    from solders.keypair import Keypair
    kp_path = tmp_path / "kp.json"
    kp_path.write_text(json.dumps(list(bytes(Keypair()))))
    os.chmod(kp_path, 0o600)

    monkeypatch.setenv("SOLANA_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("SOLANA_WALLET_KEYPAIR_PATH", str(kp_path))

    # Create the kill switch
    from execution import solana_executor as se
    kill = tmp_path / ".kill"
    kill.touch()
    monkeypatch.setattr(se, "_KILL_SWITCH_PATH", kill)

    with pytest.raises(RuntimeError, match="kill switch"):
        SolanaExecutor(_cfg(tmp_path))
