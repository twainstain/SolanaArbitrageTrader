# Autonomous brief — Phase 2d → Phase 4

**Written**: 2026-04-17 00:07 EDT by the prior Claude session.
**Target run**: ~01:07 EDT (scheduled via CronCreate).
**User**: not available to answer questions. Make the best assumption,
document it here, keep going.

Read this file **first** when you wake up. Then `claude_session/current.md`
and `docs/solana_migration_status.md`.

---

## Current prod state (what's running right now)

- Scanner: live on `ec2 54.163.230.90` (t3.small), HTTPS at
  `https://arb-trader-solana.yeda-ai.com`.
- Dashboard user / pass: `admin` / value in `.env` `DASHBOARD_PASS=`
  (local laptop .env has the same value).
- Postgres 17 container, ~4,000+ scans accumulated, 0 opportunities
  (all rejected as "unprofitable" — fee-adjusted spread under threshold).
- Alerting: Discord + Gmail configured and verified end-to-end.
- Test suite: **204 passing**.
- Git: `github.com/twainstain/SolanaArbitrageTrader`, master is
  origin-tracked. Push freely; never force-push.

## Non-negotiable safety rules

1. **Stay in paper / dry-run**. Do NOT set `SOLANA_EXECUTION_ENABLED=true`
   in any `.env`, do NOT remove the kill switch, do NOT run with
   `--execute-live`. Scanner runs untouched in prod.
2. **No destructive git**. Commits only. `git push origin master` only.
   No `--force`, no `--force-with-lease`, no branch deletion, no
   `git reset --hard` against anything on origin. If a push is rejected,
   stop and document.
3. **No destructive AWS**. No `terminate-instances`, no SG edits, no IAM
   edits beyond what's already here. The EC2, EIP, IAM role, S3 bucket,
   keypair are all fine. If you need new AWS state, write the command
   into this brief for the user to run on return.
4. **Do not break prod**. Every code change must pass `pytest tests/ -q`
   locally before `./scripts/deploy_prod.sh`. After redeploy, probe
   `/health` via HTTPS and confirm the bot container is healthy. If it
   crash-loops, roll back by reverting the commit and redeploying.
5. **No third-party uploads of private keys / `.env` content**. Redact
   secrets in anything you write.
6. **Phase 5 (narrow live rollout) is OFF-LIMITS**. Stop at the end of
   Phase 4. Do not enable live trading even if everything looks ready.

## Baseline assumptions (make the same call the user would)

- **Scope priority**: scanner-side first (Phase 2d), then execution
  plumbing (Phase 3b), then rehearsal (Phase 4). Skip Phase 2d items
  that can't be done without live Jito infra or extra RPC budget.
- **Commit cadence**: small, logical commits with clear messages. Push
  after each. Redeploy once per phase (not after every commit) to keep
  prod bot stable.
- **Test coverage**: every new public function needs a unit test.
  Existing modules without tests are fair game to leave alone unless
  you touch them.
- **Dependencies**: if a phase needs a package that isn't in
  `pyproject.toml` / `Dockerfile`, add it in a separate dependency
  commit BEFORE the code that needs it.
- **External services**: assume Alchemy (primary RPC), Jupiter
  (`lite-api.jup.ag/swap/v1`), Helius (not configured yet), Jito
  (not configured yet). You can add Helius/Jito URL env vars but leave
  them optional — code must work without them.
- **DB migrations**: if you add a column, update `_TABLES_SQLITE` AND
  `_TABLES_POSTGRES` (via the `.replace(...)` trick in `src/persistence/db.py`),
  then run `migrate_db.py` on prod. `CREATE TABLE IF NOT EXISTS` means
  you'll need an explicit `ALTER TABLE` for existing DBs.

## Phase 2d — scanner completion

Source of truth: `docs/solana_migration_status.md` line ~128.

### Do these

1. **Direct Orca Whirlpool pool addresses for LST pairs**.
   - Add pool addresses for `SOL/mSOL`, `SOL/jitoSOL`, `mSOL/USDC` in
     `src/core/pools.py` under `ORCA_WHIRLPOOLS`.
   - Source: `https://api.mainnet.orca.so/v1/whirlpool/list` → find by
     mint pair. If the pool for a pair doesn't exist with meaningful
     liquidity (>$500k TVL), skip that pair and document why below.
   - Tests: extend `tests/test_orca_market.py` to cover the new pools
     (mock the RPC response shape).

2. **Raydium full-output swap simulation** (replace the half-fee shortcut).
   - `src/market/raydium_market.py` — add a `quote_at_size(amount_in)`
     method that uses CPMM `(x * y = k)` with the actual trade size so
     price impact is accurate.
   - Keep the existing half-fee midpoint helper as `_fast_midpoint` for
     the cheap scan-time quote; use `quote_at_size` only when strategy
     is about to act on the opp (Pricing Agent stage).
   - Tests: unit test the CPMM math with known reserves + amount_in.

3. **Poll interval tuning**.
   - `config/prod_scan.json` currently `poll_interval_seconds: 0.75`.
   - Add a `fast_poll_seconds: 0.25` field (optional, defaults to
     current interval) and make the scanner downshift to it when any
     recent scan showed spread within 50% of `min_profit_base`.
   - Keep it modest — Alchemy free tier will 429 around 100 req/s; the
     code must fall back cleanly if the primary RPC errors.
   - Test: unit test the downshift trigger.

### Skip these (document why)

- **Jito bundle submission** — requires a Jito auth keypair we don't
  have. Stub the module (`src/execution/jito_bundle.py`) as
  `class JitoBundleSubmitter(NotImplementedError)` so Phase 3b can
  import it without a real Jito creds.
- **Sub-500ms sustained polling** — Alchemy free tier caps will hit
  first. Gate behind `RPC_PROVIDER=helius` check.
- **Meteora DLMM / Phoenix orderbook venues** — scope creep; skip
  without asking.

### Phase 2d acceptance

- `pytest tests/ -q` green, ≥210 tests.
- Redeploy to prod, confirm scanner still scanning, no regressions in
  logs.
- `claude_session/current.md` updated with new LST pool addresses +
  per-venue `max_spread_bps` observations (sample ~10 min of prod logs).

## Phase 3b — atomic two-leg execution (gated off)

### Do these

1. **Two-swap VersionedTransaction builder**.
   - `src/execution/atomic_swap.py` — builds one
     VersionedTransaction containing both legs (buy on venue A, sell
     on venue B) with a shared compute-budget.
   - Initially only Jupiter→Jupiter (different route params) since
     Jupiter's `/swap` gives us ready-made instruction sets. Orca +
     Raydium direct legs are Phase 3c.
   - Must respect the existing safety gates — do not bypass.

2. **Verifier reads realized profit from balance deltas**.
   - `src/execution/verifier.py` currently leaves `actual_profit_base = 0`.
   - Parse `meta.postBalances - meta.preBalances` for the wallet's
     base-token SPL account to compute realized profit.

3. **Tests**.
   - Mock `sendTransaction`, `getTransaction`, `simulateTransaction`.
   - Assert both legs present in the versioned tx, priority fee set,
     compute unit limit reasonable (200k × 2 = 400k upper bound).
   - Assert verifier computes realized profit correctly from mocked
     balance deltas.

### Gating stays

The 7 safety checks in `src/execution/solana_executor.py` stay. Your
tests must NOT bypass them.

### Phase 3b acceptance

- ≥225 tests passing.
- Atomic swap path importable, unit-tested, but `SOLANA_EXECUTION_ENABLED`
  stays unset in prod .env.
- `src/execution/verifier.py` now parses realized profit correctly in
  the mocked test.

## Phase 4 — rehearsal + readiness hardening

### Do these

1. **`scripts/rehearsal.py`** — offline sanity script that:
   - Loads config, verifies wallet (mocked) + RPC + Jupiter.
   - Runs 10 scans, picks the best candidate, builds an atomic tx,
     runs `simulateTransaction` against it, prints the result.
   - Exits 0 if everything is wired; 1 otherwise.
   - Must NOT submit a real tx.

2. **Ops dashboard cards**.
   - `/ops` — add `Wallet SOL balance`, `Fee spent (lamports, 24h)`,
     `Kill switch state` cards.
   - These should render even when execution is gated off (show "—"
     for wallet/fees).

3. **Alert hooks**.
   - `src/pipeline/lifecycle.py` — on `trade_reverted` / `trade_dropped`
     statuses, call `AlertDispatcher.alert(event_type, ...)` with tx
     signature + dashboard link.
   - Tests: extend `tests/test_pipeline.py` to assert alert fires.

4. **Missing dashboard tests** — write smoke tests for any remaining
   dashboard modules with zero coverage.

### Phase 4 acceptance

- ≥240 tests passing.
- `python scripts/rehearsal.py` runs green locally against a mocked
  RPC + Jupiter.
- `/ops` on prod shows the new cards.
- One bogus `trade_reverted` event in a test confirms an alert fires.

## Final steps before stopping (hand-off)

1. **Push all commits**.
2. **Update `claude_session/current.md`** with: test count, last
   deployed commit SHA, any open blockers, any documented assumptions
   made.
3. **Write a hand-off summary** to `claude_session/autonomous_report.md`:
   - Per-phase what was done / what was skipped / why.
   - Any prod bugs found + fixes pushed.
   - Any decisions made that the user should sanity-check.
   - Remaining known gaps.
4. **Stop**. Do not start Phase 5. Do not set `SOLANA_EXECUTION_ENABLED`.
   Do not run `--execute-live`.

## Stopping conditions (STOP IMMEDIATELY)

- Any test regression you can't fix in ≤15 min.
- Any push rejection (diverged remote).
- Any prod bot crash-loop after a redeploy. Revert + redeploy prior
  commit, then stop.
- Any AWS API error you don't understand.
- Any request for credentials that aren't already in the environment.

Document the trigger in `autonomous_report.md` and stop.

## Assumption log (append as you make more)

- **2026-04-17 00:07** (initial): proceeding in dry-run only. No live
  trading under any circumstances in this autonomous run, even if
  Phase 4 rehearsal passes.
- **2026-04-17 00:07**: Jito bundle code will be stubbed, not wired.
  Helius / paid RPC is not configured; Phase 2d sub-500ms polling is
  gated behind a `RPC_PROVIDER` env check we won't flip.
- **2026-04-17 00:07**: `Phoenix` + `Meteora DLMM` venues are out of
  scope to avoid scope creep.
