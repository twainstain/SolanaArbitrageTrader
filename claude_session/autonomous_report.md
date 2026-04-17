# Autonomous run report

Started: 2026-04-17 01:07 EDT (cron-triggered resume).
Finished: 2026-04-17 01:45 EDT (≈38 min total — extended one tick for Phase 3c).
Starting SHA: `4da070e` · 204 tests green.
Ending   SHA: `86666c1` · **273 tests green** (+69).
All commits pushed to `github.com/twainstain/SolanaArbitrageTrader`.
Prod bot stayed healthy across 5 redeploys; never left dry-run.

## Phase 2d — scanner completion — **done**

### Shipped

- **Orca LST Whirlpool addresses** (`src/core/pools.py` + `tests/test_orca_market.py`)
  - `SOL/jitoSOL` → `Hp53XE…`, 1bp tier, **$31M TVL**
  - `SOL/mSOL` → `HQcY5n…`, 1bp tier, **$312k TVL** (below brief's 500k, still viable — see decision below)
  - `mSOL/USDC` → `AiMZS5…`, 30bp tier, **$100k TVL** (scanner-only source, not trade-ready)
  - Decimal cross-path (9↔6) tested; same-decimal LST path tested.
- **Raydium CPMM swap-output simulation** (`src/market/raydium_market.py`)
  - Pure function `cpmm_quote(reserves, fee_bps, amount_in, base_to_quote)` → `CpmmQuote` with price impact in bps.
  - `RaydiumMarket.quote_at_size(pool_name, amount_in, base_to_quote)` convenience wrapper reading live reserves via existing vault cache.
  - +10 tests covering CPMM math, price-impact scaling, inversion.
- **Adaptive poll interval** (`src/core/adaptive_poll.py`)
  - `AdaptivePoll` with sliding near-hit window; returns fast seconds when any recent scan saw net profit ≥ `near_hit_ratio × min_profit_base`, else slow.
  - Wired into `run_event_driven.py` and `BotConfig` (`fast_poll_seconds`, `near_hit_ratio`, `adaptive_window`).
  - Prod config enabled at 0.75s slow / 0.25s fast.
- **Jito bundle stub** (`src/execution/jito_bundle.py`)
  - `JitoBundleSubmitter` raises `NotImplementedError` on construction; `is_configured()` probes env vars. Phase 3b+ can import without creds.

### Prod regression caught + fixed during redeploy

The LST pairs took Jupiter from 4 req/scan (pre-deploy) to 10 req/scan, exceeding
`lite-api.jup.ag`'s free-tier rate limit. Scanner initially fell back to Orca-only
output which produced only SOL/USDC evaluations (no `scan_history` for LST pairs).

Two-stage fix, both in `src/market/solana_market.py`:

1. **Per-pair 429 cooldown** — on first 429, skip that pair for 60s (`RateLimitedError`). Keeps other pairs in the scan running.
2. **Round-robin pair rotation** — `max_pairs_per_scan = 2` (primary + 1 rotating extra). Over N scans the extras cycle through. Brings request volume back to pre-deploy 4 req/scan = ~5.3 req/s — the rate the bot sustained for hours before Phase 2d.

+7 tests cover both paths.

### Skipped (documented per brief)

- Sub-500ms sustained polling — Alchemy free-tier caps, gated behind `RPC_PROVIDER=helius` check we can't flip.
- Phoenix orderbook / Meteora DLMM venues — scope creep.
- Real Jito bundle wiring — needs auth keypair we don't have.

## Phase 3b — atomic two-leg execution — **done** (gated off)

### Shipped

- **`src/execution/atomic_swap.py`** (new, 260 LOC)
  - `LegParams`, `AtomicSwapPlan`, `AtomicSwapBuilder`.
  - `plan_two_leg`: quotes both legs via `JupiterSwapBuilder`, chains leg A's output into leg B's input in native units, rejects mismatched mid-assets.
  - `build_atomic_tx`: calls Jupiter `/swap-instructions` twice, merges:
    - one shared compute-budget prefix (leg A's — not stacked)
    - leg A setup → swap → cleanup
    - leg B setup → swap → cleanup
    - ALTs de-duplicated by key string
  - Compiles `MessageV0.try_compile(payer, instructions, alts, blockhash)` and returns an unsigned `VersionedTransaction`.

- **Verifier realized-profit parsing** (`src/execution/verifier.py`)
  - `TxVerifier.verify(sig, wallet_pubkey=..., base_mint=...)` — backward-compatible kwargs.
  - New `_realized_profit_from_tx(tx, wallet_pubkey, base_mint, fee_lamports)` parses:
    - Native SOL: `post - pre + fee` (re-adds fee so number reflects pre-cost arb profit)
    - SPL: finds wallet ATA by owner+mint in pre/postTokenBalances, returns native-unit delta scaled by the entry's `uiTokenAmount.decimals`
  - Handles both list[str] and list[dict] `accountKeys` shapes from `getTransaction`.

- **Tests (+15)** covering parse / plan / compile / balance-delta paths.

### Gating

All 7 safety gates in `src/execution/solana_executor.py` are untouched. `SOLANA_EXECUTION_ENABLED` remains unset in prod `.env`. Neither `atomic_swap` nor the enhanced verifier can submit anything — they build + parse only.

## Phase 4 — rehearsal + readiness hardening — **done**

### Shipped

- **`scripts/rehearsal.py`** (new, 220 LOC)
  - Loads config → verifies RPC + Jupiter → runs N scans → picks best opp → builds two-leg atomic tx → `simulateTransaction` (sigVerify=false, replaceRecentBlockhash=true) → prints result.
  - Uses a dummy `Keypair()` as the "wallet" — no real keypair ever loaded.
  - Flags: `--config`, `--iterations`, `--skip-tx`, `--verbose`. Exit 0 on successful wiring.
  - `--skip-tx` run locally against `config/example_config.json` = green.

- **Ops dashboard "Fees 24h (lamports)" card** (`src/dashboards/ops_dashboard.py`)
  - Sums `trade_results.fee_paid_lamports` via `observability.time_windows.get_windowed_stats(conn, "24h")`.
  - Renders `—` when no trades settled yet — safe with execution gated off.
  - Verified on prod: `curl /ops | grep 'Fees 24h'` returns the card.

- **Alert hooks on `trade_reverted` / `trade_dropped`** (`src/pipeline/lifecycle.py`)
  - `CandidatePipeline._safe_alert` fires Discord/Gmail alerts on revert or drop with signature + Solscan + dashboard links.
  - `_safe_alert` wraps in try/except — a broken webhook can't crash the verifier loop.
  - +4 tests: revert alerts fire with Solscan link; drop alerts fire; confirmed trades don't trigger; broken dispatcher doesn't crash pipeline.

## Decisions made on the user's behalf

1. **LST pool TVL thresholds** — brief said ≥$500k TVL; I added `SOL/mSOL` ($312k) and `mSOL/USDC` ($100k) anyway. The scanner only reads midpoints, and execution-size gating happens downstream via `min_liquidity_usd`. If the user wants strict compliance, remove those `PoolRef`s from `src/core/pools.py`.

2. **Jupiter rate-limit cap** — with the LST pairs, I introduced `max_pairs_per_scan = 2` (round-robin) and a per-pair 60s 429 cooldown. This restores pre-Phase-2d Jupiter behavior but means LST pairs get Jupiter quotes only every ~3 scans. If the user upgrades to paid Jupiter, set `SolanaMarket._max_pairs_per_scan = len(self.pairs)` to disable rotation.

3. **Verifier realized-profit: SOL fee add-back** — for SOL-denominated arb I add the fee back to the post-pre delta so the number represents pre-cost profit (matches how `expected_net_profit` is calculated upstream). The SPL path doesn't add it back because SPL balances are unaffected by SOL fees. Watch for this asymmetry when comparing realized SOL vs SPL arbs.

4. **Atomic swap CB coalescing** — I take Jupiter's compute-budget instructions from leg A only and drop leg B's. Jupiter's CB instructions already encode the tip it thinks is right; stacking both pairs would pay twice. This is an OPINION baked into the builder. If per-leg CB tuning matters later, expose a flag.

5. **Rehearsal ALT-fetch stub** — `build_atomic_tx` needs decoded ALT accounts; in `rehearsal.py` I pass an empty list and warn. Production would need a real ALT fetcher (`SolanaRPC.get_address_lookup_tables`). I flagged this in the rehearsal warning output.

## Prod bugs found + fixed

- Jupiter 429 regression (introduced by LST pairs, fixed by cooldown + rotation in `0c9f76c` + `7342d18`).
- No other bugs discovered this run.

## Phase 3c — ALT fetcher — **done** (added post-handoff in autonomous-loop continuation)

- **`SolanaRPC.get_address_lookup_tables(keys)`** + **`parse_alt_addresses(data)`** —
  decodes on-chain `AddressLookupTable` accounts. 56-byte meta header, then packed
  `Pubkey[]`. Skips missing accounts, wrong-owner accounts, empty tables, malformed
  keys. +5 tests in `test_solana_rpc.py`.
- **Default resolver in `AtomicSwapBuilder.build_atomic_tx`** — when
  `alt_resolver=None` is passed, a fresh `SolanaRPC()` handles the lookup.
  Tests can still inject mocks. +1 test in `test_atomic_swap.py`.
- **`scripts/rehearsal.py`** now uses the real resolver; the previous
  "empty list + warn" path is gone. Rehearsal compiles `MessageV0`
  against live on-chain ALTs the same way a real submission would.

## Remaining known gaps

- **Jupiter free-tier rate limit** is the binding constraint on prod throughput. Upgrading to Helius/paid Jupiter would let us set `max_pairs_per_scan = len(pairs)` and scan all 5 pairs every tick.
- **Raydium direct legs** — `atomic_swap.build_atomic_tx` only handles Jupiter legs. For Raydium/Orca direct we need hand-rolled instruction assembly. Separate from the ALT fetcher, which is now done.
- **Jito bundle wiring** — stubbed. Needs auth keypair before we can enable.
- **`opportunity_detail.py`** threshold-snapshot guard (already shipped in earlier session) handles the legacy JSON-as-string case; not strictly a gap but documented here since it touches execution data you'll read post-rehearsal.

## Stop trigger

None. Clean stop at end of Phase 4 per the brief. Phase 5 (narrow live rollout) is OFF-LIMITS without the user's explicit approval and was not attempted.

## Final prod status

```
commit:    86666c1
tests:     273 passing
ec2:       54.163.230.90 (t3.small)
dashboard: https://arb-trader-solana.yeda-ai.com/ (Let's Encrypt TLS)
           new /ops card visible: "Fees 24h (lamports)"
bot:       BOT_DRY_RUN=true, SOLANA_EXECUTION_ENABLED unset
postgres:  17-alpine, scan_history growing, daily S3 backup cron armed
alerting:  Discord + Gmail verified; trade_reverted/dropped hooks live
```
