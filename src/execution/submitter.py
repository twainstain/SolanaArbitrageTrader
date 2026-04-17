"""RPC submitter — sends a signed transaction to the Solana cluster.

Matches the ``Submitter`` protocol used by ``CandidatePipeline``.  Returns
a ``SubmissionRef`` so downstream code (repo, verifier) can track the tx.

Jito bundle submission is a sibling class in Phase 3b — same protocol,
different HTTP endpoint.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Callable

from market.solana_rpc import SolanaRPC
from pipeline.lifecycle import SubmissionRef

logger = logging.getLogger(__name__)


@dataclass
class RpcSubmitter:
    """Send via ``sendTransaction`` on a standard Solana RPC.

    ``signed_tx_provider(opportunity) -> bytes`` is the caller's pipeline
    for building + signing a tx — typically supplied by SolanaExecutor.
    """

    rpc: SolanaRPC
    signed_tx_provider: Callable
    skip_preflight: bool = True    # we already preflighted locally
    max_retries: int = 0           # RPC-side retries; local retry is the pipeline's job

    def submit(self, opportunity) -> SubmissionRef:
        signed = self.signed_tx_provider(opportunity)
        return self.submit_raw(signed, opportunity=opportunity)

    # ------------------------------------------------------------------
    # Raw tx submission (test-friendly entry point)
    # ------------------------------------------------------------------

    def submit_raw(self, signed_tx: bytes, opportunity=None) -> SubmissionRef:
        tx_b64 = base64.b64encode(signed_tx).decode()
        params = [tx_b64, {
            "skipPreflight": self.skip_preflight,
            "preflightCommitment": "processed",
            "maxRetries": self.max_retries,
            "encoding": "base64",
        }]
        try:
            sig = self.rpc._call("sendTransaction", params)
        except Exception as exc:
            logger.warning("[submitter] sendTransaction failed: %s", exc)
            raise

        meta = {"preflight_skipped": self.skip_preflight}
        if opportunity is not None:
            meta["pair"] = getattr(opportunity, "pair", "")
            meta["buy_venue"] = getattr(opportunity, "buy_venue", "")
            meta["sell_venue"] = getattr(opportunity, "sell_venue", "")
        logger.info("[submitter] submitted signature=%s", sig)
        return SubmissionRef(signature=str(sig), kind="rpc", metadata=meta)
