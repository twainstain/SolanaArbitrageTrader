"""Preflight transaction simulator.

Wraps Solana's ``simulateTransaction`` RPC and turns its wire-level
response into a ``(ok, reason)`` tuple matching the ``Simulator`` protocol
``CandidatePipeline`` expects.

Philosophy
----------

- A simulation that returns ``err != null`` → hard reject.
- Non-null err but no logs → still reject (unknown failure).
- Known soft failures we reject explicitly: InsufficientFundsForRent,
  SlippageExceeded, etc.  Anything else → reject with the raw err string
  so the operator can investigate.

We **never** submit the tx to the chain from this module.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from market.solana_rpc import SolanaRPC

logger = logging.getLogger(__name__)

# Lowercase substrings found in Solana program logs that should abort.
_FATAL_LOG_PATTERNS = (
    "insufficient funds",
    "insufficient lamports",
    "slippage tolerance exceeded",
    "slippage exceeded",
    "max slippage",
    "expected more output",
    "stake account not delegated",
    "account not initialized",
)


class PreflightSimulator:
    """Solana preflight simulator used by CandidatePipeline."""

    def __init__(self, rpc: SolanaRPC | None = None, signed_tx_provider=None) -> None:
        """Build a simulator.

        ``signed_tx_provider`` is a callable ``(opportunity) -> bytes`` that
        returns the signed transaction wire bytes for the given opportunity.
        ``SolanaExecutor`` injects this; direct users of the simulator pass
        their own signer pipeline.
        """
        self.rpc = rpc or SolanaRPC()
        self._signed_tx_provider = signed_tx_provider

    # Matches the Simulator protocol: simulate(opp) -> (ok, reason)
    def simulate(self, opportunity) -> tuple[bool, str]:
        if self._signed_tx_provider is None:
            return False, "no signed_tx_provider configured"
        try:
            signed = self._signed_tx_provider(opportunity)
        except Exception as exc:
            logger.warning("[simulator] tx build failed: %s", exc)
            return False, f"tx_build_failed:{exc.__class__.__name__}"
        return self.simulate_raw(signed)

    # ------------------------------------------------------------------
    # Raw tx → simulate (separate entry point for tests)
    # ------------------------------------------------------------------

    def simulate_raw(self, signed_tx: bytes) -> tuple[bool, str]:
        tx_b64 = base64.b64encode(signed_tx).decode()
        params = [tx_b64, {
            "sigVerify": False,     # we know we signed correctly
            "commitment": "processed",
            "encoding": "base64",
            "replaceRecentBlockhash": True,   # avoid stale-blockhash false failures
        }]
        try:
            result = self.rpc._call("simulateTransaction", params)
        except Exception as exc:
            logger.warning("[simulator] RPC call failed: %s", exc)
            return False, f"rpc_error:{exc.__class__.__name__}"
        return self._interpret(result)

    @staticmethod
    def _interpret(result: dict[str, Any] | None) -> tuple[bool, str]:
        if not result:
            return False, "empty_simulation_result"
        value = result.get("value") or {}
        err = value.get("err")
        logs = value.get("logs") or []

        if err is not None:
            # Scan logs for a known fatal pattern — gives a more specific reason.
            joined = "\n".join(logs).lower()
            for pattern in _FATAL_LOG_PATTERNS:
                if pattern in joined:
                    return False, f"simulation_failed:{pattern.replace(' ', '_')}"
            return False, f"simulation_failed:{_err_str(err)}"

        # err is null → tx would succeed.  Bubble up a terse OK so the
        # pipeline logs something useful even on success.
        return True, "ok"


def _err_str(err: Any) -> str:
    """Best-effort stringification of Solana's variant err field."""
    if isinstance(err, str):
        return err
    if isinstance(err, dict):
        keys = list(err.keys())
        return keys[0] if keys else "unknown"
    return str(err)
