# Phase 3 — Solana Execution Backend

Last updated: 2026-04-16

## Goal

Build the full execution stack (transaction build → preflight simulate →
submit → verify) so the scanner *could* execute an approved opportunity.
**Not** enable live trading.  Every path to a real submission is gated by
multiple explicit opt-ins per `CLAUDE.md`:

> Default to simulation.
> NEVER: enable live trading without approval, send transactions without confirmation.

## Non-goals for Phase 3

- Going live — that is Phase 5.
- Jito bundles — start with plain RPC `sendTransaction`; Jito is Phase 3b.
- Complex MEV protection (private relays, tip auction tuning) — Phase 4+.
- Flash-loan-equivalent on Solana (Kamino/Solend flash). Not needed for
  pure two-leg AMM arb that fits in a single transaction.

## Components

### 1. Wallet (`src/execution/wallet.py`)

- Loads keypair from `SOLANA_WALLET_KEYPAIR_PATH` (JSON array of 64 u8
  bytes — the standard `solana-keygen` format).
- Refuses to load if the file is world-readable (`os.stat` check).
- Exposes `pubkey()` and `sign(message_bytes) -> bytes`.
- Uses `solders` library for ed25519 signing — no hand-rolled crypto.
- Never serializes or logs the secret bytes.

### 2. Jupiter swap tx builder (`src/execution/jupiter_swap.py`)

- For a given `(input_mint, output_mint, amount, user_pubkey)` calls
  Jupiter `/swap` which returns a base64 `VersionedTransaction`.
- Includes a `computeUnitPriceMicroLamports` field set from
  `config.priority_fee_lamports` (converted to micro-lamports per CU).
- Returns unsigned VersionedTransaction bytes — the wallet signs later.
- For a two-leg arb: builds *two* Jupiter swaps (leg A: quote→base at
  venue A, leg B: base→quote at venue B) and combines them into a single
  atomic transaction.  If combining exceeds Solana's 1232-byte tx limit,
  falls back to two sequential submissions with explicit failure
  handling.

### 3. Preflight simulator (`src/execution/simulator.py`)

- Takes a signed tx, calls `simulateTransaction` on the Alchemy RPC.
- Bails if the returned `err` is non-null, if logs contain known custom
  program errors (InsufficientFunds, SlippageExceeded, etc.), or if the
  balance delta doesn't match the opportunity's predicted output.
- Returns `(ok: bool, reason: str)` matching the `Simulator` protocol
  already wired into `CandidatePipeline`.

### 4. Submitter (`src/execution/submitter.py`)

- Sends a signed tx via `sendTransaction` with `skipPreflight=true`
  (we already ran preflight ourselves).
- Returns `SubmissionRef(signature=sig, kind="rpc")`.
- Jito bundle submitter is a future sibling class — same protocol.

### 5. Verifier (`src/execution/verifier.py`)

- Polls `getSignatureStatuses` with a 30s timeout.
- Returns `VerificationResult` with:
  - `included` — landed in a block
  - `reverted` — landed but errored (`err != null`)
  - `dropped` — blockhash expired before inclusion
  - `confirmation_slot`, `fee_paid_lamports`, `realized_profit_quote`
- Parses post-balance delta via `getTransaction` to compute realized
  profit in quote units.

### 6. SolanaExecutor (`src/execution/solana_executor.py`)

Composes all of the above.  **Mandatory safety gates** (each checked in
`__init__`):

1. `SOLANA_EXECUTION_ENABLED=true` env var — missing → raise.
2. `SOLANA_WALLET_KEYPAIR_PATH` env var — missing → raise.
3. Wallet file permissions 0600 — otherwise refuse.
4. Kill-switch: if file `data/.execution_kill_switch` exists, refuse.
5. Per-call: opportunity's `trade_size` must not exceed
   `config.max_exposure_per_pair`.
6. Per-call: wallet SOL balance ≥ `trade_size + priority_fee`.

## Pipeline wiring

`CandidatePipeline` already has `simulator` / `submitter` / `verifier`
slots.  Phase 3 just passes real instances into them when all safety
gates clear:

```python
from execution.wallet import Wallet
from execution.solana_executor import SolanaExecutor

wallet = Wallet.from_env()             # raises on any misconfig
executor = SolanaExecutor(
    config=config, wallet=wallet, rpc=SolanaRPC(),
)
# SolanaExecutor exposes .simulator, .submitter, .verifier
pipeline = CandidatePipeline(
    repo=repo, risk_policy=policy,
    simulator=executor.simulator,
    submitter=executor.submitter,
    verifier=executor.verifier,
)
```

When any gate fails, `SolanaExecutor.__init__` raises and the bot falls
back to the existing "no submitter → dry_run" pipeline path — no change
of behaviour for Phase 1/2 users.

## CLI

New flag `main.py --execute-live` prints a banner, reads
`SOLANA_EXECUTION_ENABLED` from env, prompts for a y/N confirmation, and
only then instantiates `SolanaExecutor`.  Without the flag, paper mode
unchanged.

Environment summary (`.env`):

```bash
# ALL of these must be set BEFORE live execution is possible:
SOLANA_EXECUTION_ENABLED=true
SOLANA_WALLET_KEYPAIR_PATH=/secure/path/to/keypair.json
```

## Tests

- `tests/test_wallet.py` — refuses loose file perms, loads good file, signs deterministically.
- `tests/test_jupiter_swap.py` — mocks Jupiter `/swap` response, verifies CU-price instruction.
- `tests/test_simulator.py` — mocks RPC `simulateTransaction`, parses err + logs.
- `tests/test_submitter.py` — mocks `sendTransaction`, returns correct `SubmissionRef`.
- `tests/test_verifier.py` — mocks `getSignatureStatuses` + `getTransaction` across all end-states.
- `tests/test_solana_executor.py` — verifies every safety gate rejects correctly; happy path only runs with a dummy `execution_enabled=true` env fixture.

## Exit criteria

- `pytest tests/ -q` stays green.
- `SolanaExecutor` refuses to instantiate without full env setup.
- Full compose-and-simulate path runs against real Alchemy RPC in dry
  mode (simulate-only, no submit).
- **No new tests hit real `sendTransaction`.**
- Doc updated with "live-enable checklist" for Phase 5.

## What requires user approval before we cross the line

Phase 3 lands the code.  Before any real submission:

1. User confirms wallet pubkey is correct
2. User funds the wallet with a small SOL balance (e.g. 0.1 SOL)
3. User sets `SOLANA_EXECUTION_ENABLED=true` themselves
4. User picks one pair and one venue pair to enable
5. User runs `main.py --execute-live` and types `yes` at the prompt
6. User monitors the first 10 trades manually and compares expected vs
   realized PnL

That sequence happens in Phase 5, not Phase 3.
