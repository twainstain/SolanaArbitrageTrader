"""Solana keypair loader with strict safety checks.

Loads a keypair from ``SOLANA_WALLET_KEYPAIR_PATH`` — the standard JSON
array of 64 ``u8`` bytes that ``solana-keygen new -o wallet.json`` writes.
Refuses to load unless:

- The file exists
- File mode bits are restrictive (not readable by group/other on POSIX)

Never logs, prints, or serializes the secret bytes.  The private half is
only held inside the ``solders.Keypair`` Rust object; the Python side
only handles the pubkey and a sign callback.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from solders.keypair import Keypair

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Wallet:
    """Thin safe-wrapper around ``solders.Keypair``.

    Instances expose only:

    - ``pubkey`` — base58 string
    - ``sign_message(bytes) -> bytes`` — 64-byte ed25519 signature

    The underlying secret bytes never leave the Rust ``Keypair`` object.
    """

    pubkey: str
    _keypair: Keypair

    def sign_message(self, message: bytes) -> bytes:
        return bytes(self._keypair.sign_message(message))

    @property
    def solders_keypair(self) -> Keypair:
        """For adapters that must pass the native object to solders APIs."""
        return self._keypair

    # ------------------------------------------------------------------
    # Safe constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_path(cls, path: str | Path, enforce_permissions: bool = True) -> "Wallet":
        p = Path(path).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"Wallet keypair file not found: {p}")

        if enforce_permissions:
            mode = p.stat().st_mode
            # Refuse if the file is readable by group or other (any of 0o077 bits set).
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise PermissionError(
                    f"Wallet file {p} is readable by group/other (mode {oct(mode & 0o777)}). "
                    f"Run: chmod 600 {p}"
                )

        try:
            data = json.loads(p.read_text())
        except Exception as exc:
            raise ValueError(f"Wallet file {p} is not valid JSON: {exc}") from exc

        if not isinstance(data, list) or len(data) != 64:
            raise ValueError(
                f"Wallet file {p} must be a JSON array of 64 bytes (solana-keygen format)"
            )

        try:
            kp = Keypair.from_bytes(bytes(data))
        except Exception as exc:
            raise ValueError(f"Wallet file {p} is not a valid ed25519 keypair") from exc

        pubkey = str(kp.pubkey())
        logger.info("[wallet] loaded pubkey=%s", pubkey)
        return cls(pubkey=pubkey, _keypair=kp)

    @classmethod
    def from_env(cls) -> "Wallet":
        """Load from ``SOLANA_WALLET_KEYPAIR_PATH``.  Raises if unset."""
        path = os.environ.get("SOLANA_WALLET_KEYPAIR_PATH", "")
        if not path:
            raise RuntimeError(
                "SOLANA_WALLET_KEYPAIR_PATH is not set.  "
                "Live execution requires a wallet keypair; scanner-only mode does not."
            )
        return cls.from_path(path)
