"""Wallet loader tests — safety checks and happy path."""

from __future__ import annotations

import json
import os
import stat

import pytest

from execution.wallet import Wallet


def _write_keypair(tmp_path, name="kp.json"):
    """Write a fresh valid keypair JSON with restrictive perms."""
    from solders.keypair import Keypair
    kp = Keypair()
    path = tmp_path / name
    path.write_text(json.dumps(list(bytes(kp))))
    os.chmod(path, 0o600)
    return path, kp


def test_loads_valid_keypair(tmp_path):
    path, expected = _write_keypair(tmp_path)
    w = Wallet.from_path(path)
    assert w.pubkey == str(expected.pubkey())
    # Signing actually works
    sig = w.sign_message(b"hello")
    assert len(sig) == 64


def test_refuses_loose_permissions(tmp_path):
    path, _ = _write_keypair(tmp_path)
    os.chmod(path, 0o644)   # world-readable
    with pytest.raises(PermissionError, match="readable by group"):
        Wallet.from_path(path)


def test_refuses_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        Wallet.from_path(tmp_path / "nonexistent.json")


def test_refuses_non_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json")
    os.chmod(path, 0o600)
    with pytest.raises(ValueError, match="valid JSON"):
        Wallet.from_path(path)


def test_refuses_wrong_shape(tmp_path):
    path = tmp_path / "wrong.json"
    path.write_text(json.dumps([1, 2, 3]))
    os.chmod(path, 0o600)
    with pytest.raises(ValueError, match="64 bytes"):
        Wallet.from_path(path)


def test_from_env_requires_env_var(monkeypatch):
    monkeypatch.delenv("SOLANA_WALLET_KEYPAIR_PATH", raising=False)
    with pytest.raises(RuntimeError, match="SOLANA_WALLET_KEYPAIR_PATH"):
        Wallet.from_env()


def test_from_env_happy_path(tmp_path, monkeypatch):
    path, kp = _write_keypair(tmp_path)
    monkeypatch.setenv("SOLANA_WALLET_KEYPAIR_PATH", str(path))
    w = Wallet.from_env()
    assert w.pubkey == str(kp.pubkey())
