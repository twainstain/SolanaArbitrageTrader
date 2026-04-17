"""Live Solana executor — composes the Phase 3 execution stack.

Per ``CLAUDE.md``: "Default to simulation.  NEVER enable live trading
without approval.  NEVER send transactions without confirmation."

This class enforces that rule by refusing to instantiate unless
**every** safety gate passes:

1. ``SOLANA_EXECUTION_ENABLED=true`` env var — missing → refuse
2. ``SOLANA_WALLET_KEYPAIR_PATH`` env var points at a readable keypair
   with ``0600`` perms — otherwise refuse
3. Kill-switch file ``data/.execution_kill_switch`` does not exist —
   otherwise refuse
4. Per-opportunity ``trade_size <= config.trade_size × 2`` — guards
   against config drift that would oversize trades
5. Wallet balance read at construction time to confirm there's at least
   ``0.005 SOL`` available to cover fees — otherwise refuse

If **any** gate fails, ``__init__`` raises and the calling code should
fall back to ``PaperExecutor`` or the scanner-only pipeline.

Even after construction, ``execute()`` re-checks the kill-switch before
every attempt — operators can disable execution mid-run by touching
``data/.execution_kill_switch``.
"""

from __future__ import annotations

import logging
import os
import time
from decimal import Decimal
from pathlib import Path

from core.config import BotConfig
from core.models import ExecutionResult, Opportunity, ZERO
from core.tokens import get_token
from execution.jupiter_swap import JupiterSwapBuilder, sign_swap_tx
from execution.simulator import PreflightSimulator
from execution.submitter import RpcSubmitter
from execution.verifier import TxVerifier
from execution.wallet import Wallet
from market.solana_rpc import SolanaRPC

logger = logging.getLogger(__name__)

D = Decimal
_MIN_WALLET_SOL = D("0.005")   # floor for fees / rent


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_KILL_SWITCH_PATH = _PROJECT_ROOT / "data" / ".execution_kill_switch"


class SolanaExecutor:
    """Live Solana executor.  See module docstring for safety contract.

    Exposes the three pipeline-facing objects as attributes so the caller
    can wire them into ``CandidatePipeline``:

        exe = SolanaExecutor(config)
        pipeline = CandidatePipeline(
            ...,
            simulator=exe.simulator,
            submitter=exe.submitter,
            verifier=exe.verifier,
        )
    """

    def __init__(
        self,
        config: BotConfig,
        wallet: Wallet | None = None,
        rpc: SolanaRPC | None = None,
        swap_builder: JupiterSwapBuilder | None = None,
    ) -> None:
        self._check_env_gate()
        self._check_kill_switch()

        self.config = config
        self.rpc = rpc or SolanaRPC()
        self.wallet = wallet or Wallet.from_env()
        self.swap_builder = swap_builder or JupiterSwapBuilder()

        # Wallet balance sanity check — refuse to start if we can't even
        # cover the priority fee.
        self._check_wallet_balance()

        # Wire the three pipeline stages.
        self.simulator = PreflightSimulator(
            rpc=self.rpc,
            signed_tx_provider=self._build_and_sign,
        )
        self.submitter = RpcSubmitter(
            rpc=self.rpc,
            signed_tx_provider=self._build_and_sign,
        )
        self.verifier = TxVerifier(rpc=self.rpc)

        logger.warning(
            "[EXECUTOR] LIVE SolanaExecutor initialised — wallet=%s.  "
            "Kill switch: touch %s",
            self.wallet.pubkey, _KILL_SWITCH_PATH,
        )

    # ------------------------------------------------------------------
    # Safety gates
    # ------------------------------------------------------------------

    @staticmethod
    def _check_env_gate() -> None:
        enabled = os.environ.get("SOLANA_EXECUTION_ENABLED", "").lower()
        if enabled not in ("true", "1", "yes"):
            raise RuntimeError(
                "SOLANA_EXECUTION_ENABLED must be set to 'true' to construct "
                "SolanaExecutor.  Scanner-only mode does not require this."
            )
        # Wallet env var must also be set — Wallet.from_env() will re-check
        # but we fail earlier with a better message here.
        if not os.environ.get("SOLANA_WALLET_KEYPAIR_PATH"):
            raise RuntimeError(
                "SOLANA_WALLET_KEYPAIR_PATH must point at a keypair file "
                "(mode 0600) to construct SolanaExecutor."
            )

    @staticmethod
    def _check_kill_switch() -> None:
        if _KILL_SWITCH_PATH.exists():
            raise RuntimeError(
                f"Execution kill switch is active ({_KILL_SWITCH_PATH}). "
                f"Remove that file to re-enable live execution."
            )

    def _check_wallet_balance(self) -> None:
        try:
            result = self.rpc._call("getBalance", [self.wallet.pubkey])
        except Exception as exc:
            raise RuntimeError(f"Could not read wallet balance: {exc}") from exc
        lamports = (result or {}).get("value", 0) if isinstance(result, dict) else result
        sol = D(lamports) / D(10**9)
        if sol < _MIN_WALLET_SOL:
            raise RuntimeError(
                f"Wallet balance too low: {sol} SOL < minimum {_MIN_WALLET_SOL} SOL. "
                f"Fund wallet {self.wallet.pubkey} before enabling execution."
            )
        logger.info("[executor] wallet balance OK: %s SOL", sol)

    def _reject_oversize(self, opp: Opportunity) -> None:
        cap = self.config.trade_size * D("2")
        if opp.trade_size > cap:
            raise RuntimeError(
                f"Opportunity trade_size {opp.trade_size} exceeds 2× config cap {cap}"
            )

    # ------------------------------------------------------------------
    # The signed-tx pipeline callback (shared by simulator + submitter)
    # ------------------------------------------------------------------

    def _build_and_sign(self, opp: Opportunity) -> bytes:
        """Build and sign a Jupiter swap transaction for ``opp``.

        Phase 3 v1 executes just the *buy* leg via Jupiter — actual
        two-leg atomic arb is Phase 3b once we confirm single-leg
        execution works and is safe.  The verifier measures realized PnL
        against expected, and the risk policy will reject trades whose
        single-leg execution cost doesn't clear the target spread.
        """
        self._check_kill_switch()   # re-check before every tx build
        self._reject_oversize(opp)

        base_sym = opp.pair.split("/")[0]
        quote_sym = opp.pair.split("/")[1] if "/" in opp.pair else self.config.quote_asset

        # Phase 3 v1 demo direction: buy base with quote on the cheaper venue.
        # Real two-leg arb needs both directions — Phase 3b.
        quote = self.swap_builder.quote(
            input_symbol=quote_sym,
            output_symbol=base_sym,
            input_amount_human=opp.cost_to_buy_quote,
            slippage_bps=int(self.config.slippage_bps),
        )
        unsigned = self.swap_builder.build_swap_tx(
            quote=quote,
            user_pubkey=self.wallet.pubkey,
            priority_fee_lamports=self.config.priority_fee_lamports,
        )
        return sign_swap_tx(unsigned, self.wallet)

    # ------------------------------------------------------------------
    # Convenience execute() for code that uses the older protocol
    # ------------------------------------------------------------------

    def execute(self, opp: Opportunity) -> ExecutionResult:
        """Synchronous execute — rarely used; pipeline integration preferred."""
        self._check_kill_switch()
        try:
            signed = self._build_and_sign(opp)
        except Exception as exc:
            return ExecutionResult(
                success=False, reason=f"build_failed:{exc}",
                realized_profit_base=ZERO, opportunity=opp,
            )

        ok, reason = self.simulator.simulate_raw(signed)
        if not ok:
            return ExecutionResult(
                success=False, reason=f"simulation:{reason}",
                realized_profit_base=ZERO, opportunity=opp,
            )

        ref = self.submitter.submit_raw(signed, opportunity=opp)
        verification = self.verifier.verify(ref.signature)
        if verification.included and not verification.reverted:
            return ExecutionResult(
                success=True, reason="confirmed",
                realized_profit_base=verification.actual_profit_base or opp.net_profit_base,
                opportunity=opp,
                signature=ref.signature,
                confirmation_slot=verification.confirmation_slot,
            )
        reason = (
            "dropped" if verification.dropped
            else "reverted" if verification.reverted
            else "unknown"
        )
        return ExecutionResult(
            success=False, reason=reason,
            realized_profit_base=ZERO, opportunity=opp,
            signature=ref.signature,
            confirmation_slot=verification.confirmation_slot,
        )
