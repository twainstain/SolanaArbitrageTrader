"""Jito bundle submitter — stub for Phase 3b.

Bundling two-leg swaps through Jito's block engine is the obvious path
to latency-competitive arbitrage on mainline SOL pairs: bundled txs land
atomically and pay tips to block leaders who include them, front-running
ordinary RPC submissions.

This module is a stub so Phase 3b can import a consistent interface
without us shipping an authenticated client that requires a Jito auth
keypair we don't yet have.  When the auth is in place, the real
implementation fills in ``submit`` by POSTing a bundle to
``/api/v1/bundles`` on the configured block engine URL.

To wire this up for real:

1. Obtain a Jito auth keypair (opt in via the Jito searcher platform).
2. Set ``JITO_AUTH_KEYPAIR_PATH`` + ``JITO_BLOCK_ENGINE_URL`` in ``.env``.
3. Replace this class body with the real client — preserve the
   ``submit(versioned_tx)`` signature so callers don't change.

Until then, instantiating this class raises ``NotImplementedError``, so
a caller that imports ``JitoBundleSubmitter`` will see a clear failure
at construction time rather than silently dropping bundles.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class JitoBundleResult:
    """Placeholder result shape — mirrors SubmissionRef."""
    bundle_id: str
    accepted: bool
    message: str = ""


class JitoBundleSubmitter:
    """Unwired stub. See module docstring."""

    def __init__(
        self,
        block_engine_url: str | None = None,
        auth_keypair_path: str | None = None,
    ) -> None:
        self.block_engine_url = block_engine_url or os.environ.get(
            "JITO_BLOCK_ENGINE_URL", ""
        )
        self.auth_keypair_path = auth_keypair_path or os.environ.get(
            "JITO_AUTH_KEYPAIR_PATH", ""
        )
        raise NotImplementedError(
            "JitoBundleSubmitter is a Phase 3b+ stub; see src/execution/jito_bundle.py. "
            "Provide JITO_AUTH_KEYPAIR_PATH + JITO_BLOCK_ENGINE_URL and wire the real "
            "client before calling this class."
        )

    def submit(self, versioned_tx_b64: str, tip_lamports: int = 0) -> JitoBundleResult:
        raise NotImplementedError

    @staticmethod
    def is_configured() -> bool:
        """True iff both JITO_BLOCK_ENGINE_URL and JITO_AUTH_KEYPAIR_PATH env vars are set."""
        return bool(
            os.environ.get("JITO_BLOCK_ENGINE_URL")
            and os.environ.get("JITO_AUTH_KEYPAIR_PATH")
        )
