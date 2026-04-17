# Solana Migration Status

Last updated: 2026-04-16

## Context

This repo was copied from `ArbitrageTrader` (the live EVM/flash-loan product)
and is being converted in-place into `SolanaTrader`. We are **not** creating a
separate repo (diverges from `docs/solana_trader_full_plan.md` Section 1, but
confirmed with the user).

## Scope — this session

User-confirmed decisions:

| Decision | Choice |
|---|---|
| 1. First phase | **Scanner-only** (Phase 1 per plan). No live execution. |
| 2. Venues | **Simple** — Jupiter aggregator quotes only (v1). |
| 3. Initial pairs | `SOL/USDC` and `USDC/USDT`. |
| 4. EVM code | Move to `_evm_legacy/` for reference, not deleted. |
| 5. Database | **New Solana-native schema** (submission_ref, confirmation_slot, fee_paid_lamports). New reporting. |
| 6. Logging | Keep `observability/latency_tracker.py` — user explicitly asked to preserve per-stage latency tracking. |

## Phase map (from `solana_trader_full_plan.md`)

- [x] **Phase 0** — repo skeleton, plan, EVM quarantine (done)
- [x] **Phase 1** — scanner-only Solana ingestion (done)
- [x] **Phase 2a** — Jupiter baseline run (done — exposed rate-limit + convergence issues)
- [x] **Phase 2b** — direct-pool adapters (Raydium + Orca) (done — real venue diversity)
- [x] **Phase 2c** — threshold tuning + LST pairs added (done — exposed structural fee-bound spread)
- [x] **Phase 3** — Solana execution backend (done — all components built, gated off by default)
- [x] **Phase 3.5** — Dashboards + HTTP API rebuilt for Solana (done)
- [ ] Phase 4 — rehearsal + readiness hardening
- [ ] Phase 5 — narrow live rollout (1 pair, 1 venue, capped size)
- [ ] Phase 6 — scale and optimization

## Phase 2b Findings (2026-04-16)

### Venue stack

Three genuinely independent spot-price sources now feed the scanner:

1. **Jupiter lite-api v1** — aggregator (2 routes: Best + Direct)
2. **Raydium AMM V4** — direct pool reserves via `getMultipleAccounts` on vault SPL accounts
3. **Orca Whirlpool** — `sqrt_price` Q64.64 decoded from the pool account

### Measured spreads (15 scans, SOL/USDC + USDC/USDT)

| Venue pair | avg bps | max bps |
|---|---|---|
| Jupiter → Orca (SOL/USDC) | 4.0 | 6.6 |
| Jupiter → Raydium (SOL/USDC) | 3.9 | 6.2 |
| Orca → Raydium (SOL/USDC) | 0.1 | 1.3 |
| Jupiter-Best ↔ Jupiter-Direct | ~0 | ~0 |

### What this means

- **Scanner works end-to-end** — all 3 venues respond, quotes round-trip through pipeline, latency tracked.
- **Real spreads exist**: Jupiter systematically quotes ~4 bps better than direct Raydium/Orca pools. Expected — Jupiter's routing finds the deepest liquidity path.
- **Not yet profitable on SOL/USDC**: 4 bps avg is below our round-trip cost (25 bps Raydium fee + 5 bps Orca + 20 bps slippage = 50 bps). The pair is too efficient.
- **Per-scan RPC latency**: p50=250ms, p95=401ms — acceptable, dominated by Jupiter's HTTP round-trip (Alchemy `getMultipleAccounts` is sub-100ms).

## Phase 2c Findings (2026-04-16)

### Changes

- `slippage_bps` default 20 → 3 (accurate for 1 SOL on $7M+ pools)
- `min_profit_base` default 0.002 → 0.0005 SOL
- Added `SOL/mSOL`, `SOL/jitoSOL`, `mSOL/USDC` to `config/example_config.json`

### Key measurement

Across 15 scans × 4 pairs, **Jupiter→Raydium SOL/USDC price ratio median = 1.00248** (24.8 bps markup), min 22.7 bps, max 25.5 bps.

The Raydium pool fee is exactly **25 bps**.

### Interpretation

The scanner is working correctly.  What it's detecting is **not real arbitrage** — it's the structural difference between:

- Raydium adapter price = `quote_reserve / base_reserve` (raw midpoint, fee NOT deducted)
- Jupiter adapter price = actual `outAmount / inAmount` (fee already deducted, since Jupiter routes through Raydium and nets the fee)

When the strategy applies Raydium's 25 bps fee on the sell leg, the apparent spread disappears.  **Gross spread ≤ fee ⇒ net profit ≤ 0, every time.**

### SOL/mSOL + SOL/jitoSOL

Jupiter's two routes (Best vs Direct) return identical prices on LST pairs — no venue diversity visible via Jupiter alone.  Would require finding direct Raydium/Orca LST pool addresses for this angle to yield signal.  Not blocked here — open ticket in Phase 2d.

### Real implications

**Profitable cross-venue arb on mainline SOL pairs requires one of:**

1. **Genuine mispricing spikes** — brief periods (~seconds) during high volatility where one pool's reserves lag the others.  Needs sub-500ms polling and sub-second execution latency, probably via Jito bundle submission to land before competitors.
2. **Lower-fee venues** — Orca (5 bps) + low-fee Whirlpools + Phoenix orderbook.  Cheaper round-trip.
3. **Longer-tail tokens** — pairs where bots aren't actively squeezing spreads.  Much bigger edge, much less liquidity.

### Phase 2c fix — half-fee adjustment (applied)

To make Raydium/Orca adapters return prices **directly comparable with
Jupiter** (which already nets fees), both direct-pool adapters now apply
half of the pool fee into their returned price and set ``fee_included=True``:

```python
effective_price = raw_midpoint × (1 - fee_bps / 2 / 10000)
```

Half-fee (not full-fee) is the right "spot" midpoint because a buyer pays
raw × (1+half) and a seller receives raw × (1-half) — the midpoint splits
the fee symmetrically.  With this change the strategy no longer
double-counts fees.

### Live detection, post-fix (10 scans)

| Pair | Venue pair | Hits | Avg spread | Avg net profit |
|---|---|---|---|---|
| mSOL/USDC | Jupiter-Direct → Jupiter-Best | 10 | 12.6 bps | 0.00131 SOL |
| SOL/USDC | Orca → Jupiter-Best | 2 | 9.0 bps | 0.00059 SOL |
| SOL/USDC | Orca → Jupiter-Direct | 2 | 8.9 bps | 0.00058 SOL |

### Qualitative read

- **Orca → Jupiter SOL/USDC** is the cleanest real arb: buy on Orca pool (cheaper effective price), sell via Jupiter's aggregator.  ~0.6 mSOL per hit, ~10 bps spread.  This IS executable via Phase 3 stack — Orca swap + Jupiter swap in sequence (Phase 3b makes it atomic in one tx).
- **mSOL/USDC Jupiter-Direct→Best** is Jupiter's own two-route price difference.  Executable only by doing two separate Jupiter swaps with different `onlyDirectRoutes` settings — less clean than venue-to-venue arb but still real.
- **SOL/mSOL and SOL/jitoSOL** remain essentially flat at Jupiter's two routes — LST pairs need direct pool reads to surface signal (Phase 2d).

### Recommended Phase 2d scope (if pursued)

- Sub-500ms poll interval with Jito bundle path (Phase 3b dependency) — capture the brief spread spikes before competitors
- Replace Raydium's half-fee shortcut with full CPMM **swap-output simulation** at the opportunity's actual trade size (models price impact accurately)
- Discover + add direct Orca Whirlpool pool addresses for SOL/mSOL, SOL/jitoSOL, mSOL/USDC
- Add Meteora DLMM and Phoenix orderbook venues

## Phase 3 — Solana Execution Stack (2026-04-16)

### What's built

| Module | Purpose |
|---|---|
| `src/execution/wallet.py` | Safe keypair loader — refuses loose file perms |
| `src/execution/jupiter_swap.py` | `/quote` + `/swap` → unsigned VersionedTransaction |
| `src/execution/simulator.py` | `simulateTransaction` preflight, log pattern matching |
| `src/execution/submitter.py` | `sendTransaction` → `SubmissionRef(signature)` |
| `src/execution/verifier.py` | Polls `getSignatureStatuses` + `getTransaction` for final PnL |
| `src/execution/solana_executor.py` | Composes the above; enforces ALL safety gates |

### Safety gates (every one must pass)

1. `SOLANA_EXECUTION_ENABLED=true` env var
2. `SOLANA_WALLET_KEYPAIR_PATH` env var
3. Wallet file perms ≤ 0600
4. Kill switch `data/.execution_kill_switch` does not exist (re-checked before every tx build)
5. Wallet SOL balance ≥ 0.005
6. Per-opportunity `trade_size ≤ 2 × config.trade_size`
7. CLI `--execute-live` flag + operator types `yes` at the prompt

Without all seven, the bot falls back to `PaperExecutor` — same behaviour as Phase 1/2.

### Tests

93/93 passing, including:
- 7 wallet tests (perm refusal, bad JSON, bad shape, env var missing, …)
- 6 simulator tests (null err, non-null err, slippage pattern, empty result, RPC error)
- 3 submitter tests (happy path, RPC error, metadata)
- 4 verifier tests (confirmed ok, confirmed with err, timeout → dropped, getTransaction failure)
- 3 executor tests (refuses without env, without wallet, with kill switch)

### Phase 3 known limitations

- Phase 3 v1 executes only the **buy leg** via Jupiter swap.  Two-leg atomic arb (buy on A, sell on B in one tx) is **Phase 3b**.
- Priority-fee conversion uses a fixed 200k CU assumption — fine for single swap, tune for multi-swap txs in Phase 3b.
- No Jito bundle path yet — plain RPC `sendTransaction` only.
- Verifier doesn't yet parse realized profit from `getTransaction` balance deltas; `actual_profit_base` is 0 until Phase 3b.

### What Phase 4/5 will add

Phase 4: readiness/rehearsal script, ops dashboard cards for wallet balance + fee spend, alerting hooks for `trade_reverted`/`trade_dropped`.

Phase 5: first real trade — small size, one pair, one venue pair, manual review loop.

## What's being done this session

### Preserve (shared / platform-neutral)

- `src/pipeline/` (lifecycle, verifier) — adapted to drop EVM-only fields
- `src/risk/` (policy, rules, retry) — platform-neutral, kept
- `src/alerting/` — kept
- `src/observability/` — kept, especially `latency_tracker.py`
- `src/api/` — trimmed of EVM-only endpoints (wallet/router readiness)
- `src/persistence/` base patterns — new schema, same repository/connection shape

### Quarantine → `_evm_legacy/`

Moved verbatim for future reference, not imported by active code:

- `src/execution/chain_executor.py`
- `src/market/{onchain,subgraph,historical,live}_market.py`
- `src/market/subgraphs.py`
- `src/core/contracts.py`
- `contracts/` (Solidity + Foundry)
- `deploy/` (EVM deployment scripts)
- EVM-specific tests and scripts

### Rewrite in-place (Solana-native)

- `src/core/tokens.py` — SPL mint addresses (SOL, USDC, USDT, mSOL, jitoSOL)
- `src/core/venues.py` — Solana DEX venues (Jupiter, Raydium, Orca, Meteora)
- `src/core/config.py` — Solana-shaped `BotConfig` (no flash_loan, no gas_cost_eth)
- `src/core/models.py` — drop `SUPPORTED_CHAINS` EVM list, drop EVM-shaped `is_cross_chain`
- `src/market/solana_market.py` — Jupiter v6 quote API adapter (new)
- `src/market/sim_market.py` — kept, tuned for Solana pairs
- `src/strategy/arb_strategy.py` — SOL/USDC reference conversion (not WETH)
- `src/strategy/scanner.py` — venue-based liquidity gates, not chain-based
- `src/pipeline/lifecycle.py` — no tx_hash/bundle_id; uses submission_ref/confirmation_slot
- `src/pipeline/verifier.py` — Solana-neutral verification result
- `src/execution/executor.py` — `PaperExecutor` kept, Solana model
- `src/execution/solana_executor.py` — stub raising `NotImplementedError("phase 3")`
- `src/persistence/db.py` + `repository.py` — new schema, lamport fees
- `src/main.py` + `src/run_event_driven.py` — Solana-only mode selection
- `config/example_config.json` — SOL/USDC + USDC/USDT, Jupiter venue
- `.env.example` — `SOLANA_RPC_URL`, `HELIUS_API_KEY`, `JUPITER_API_URL`
- `scripts/run_local.sh` + `scripts/check_readiness.py` — Solana
- `README.md` — reflect SolanaTrader scope
- `CLAUDE.md` — already Solana-first; keep

### Delete outright

- EVM configs: `arbitrum_live_execution_config.json`, `optimism_live_execution_config.json`, `multichain_*`, `uniswap_pancake_config.json`, `live_config.json`, `onchain_config.json`, `subgraph_config.json`, `historical_config.json`, `multi_pair_config.json`
- EVM tests: `test_chain_executor.py`, `test_onchain_market.py`, `test_subgraph_market.py`, `test_historical_market.py`, `test_live_market.py`, `test_live_rpc_integration.py`, `test_fork_scanner.py`, `test_cross_chain_filter.py`, `test_multi_pair.py`, `test_pool_discovery.py`, `test_price_downloader.py`, `test_pair_refresher.py`, `test_pair_scanner.py`, `test_new_pairs_and_fixes.py`, `test_retry_and_failover.py`, `test_registry.py`, `test_discovery.py`
- EVM scripts: `validate_chains.py`, `fork_rehearsal.py`, `deploy_prod.sh`, `verify_opportunity.py`

## Exit criteria for this session (Phase 0+1)

- [x] `pytest tests/ -q` passes with the reduced Solana test suite — 54/54 passing
- [x] `PYTHONPATH=src:lib/trading_platform/src python3.11 -m main --config config/example_config.json --iterations 3 --dry-run` runs end-to-end in simulated mode
- [x] `PYTHONPATH=src:lib/trading_platform/src python3.11 -m run_event_driven --config config/example_config.json --iterations 3 --sleep 0.1` runs end-to-end, producer+consumer
- [x] `logs/latency.jsonl` records per-stage timings for each scan AND each opportunity (verified: rpc_fetch, scanner, detect_ms, price_ms, risk_ms, total_ms)
- [x] No `import web3`, `from web3`, `aave`, `flashbots`, `FlashArbExecutor` in active `src/` code (confirmed; all moved to `_evm_legacy/`)
- [x] `SolanaExecutor` stub refuses construction (Phase-1 safety guard)
- [x] README describes Solana scanner-phase product
- [x] New Solana DB schema with `submission_ref`, `signature`, `confirmation_slot`, `fee_paid_lamports`, `fee_paid_base` (no `tx_hash`, `bundle_id`, `gas_used` in active schema)

## Known deferrals (not done this session)

| Item | Phase |
|---|---|
| Jupiter WebSocket streaming quotes | 2 |
| Raydium/Orca/Meteora direct pool quotes | 2 |
| Per-venue liquidity thresholds tuned with real data | 2 |
| Solana fee model (priority fees, Jito tips, compute units) | 2 |
| `solana_executor.py` — transaction builder | 3 |
| Preflight simulation via `simulateTransaction` | 3 |
| Submitter (regular RPC vs Jito bundle) | 3 |
| Verifier (slot confirmation, finality, realised PnL) | 3 |
| Solana dashboard rebuild (Etherscan → Solscan links, lamports not wei) | 3 |
| Readiness/rehearsal scripts for Solana | 4 |

## How to resume

1. Read this file + `docs/solana_trader_full_plan.md`
2. `git log --oneline` to see migration commits
3. `_evm_legacy/` holds the old EVM code if any reference is needed
4. Next phase: tune fee/slippage/liquidity thresholds using real quote data collected in Phase 1

## Current state summary (end of Phase 0+1)

### What works

- `src/` compiles clean and has no EVM imports
- `pytest tests/ -q` → 54 passed
- `main.py` (single-shot scanner) runs with `--simulated` (offline) or `--jupiter` (live)
- `run_event_driven.py` runs a producer/consumer loop with full latency tracking
- `persistence` rewrites everything against a fresh Solana schema in `data/solana_arb.db`
- Per-stage latency lands in `logs/latency.jsonl` for both scan and pipeline records

### What's stubbed / deferred

- `SolanaExecutor` raises `NotImplementedError` — no live submission is possible
- `alerting/smart_alerts.py` is a no-op shell; EVM rich summaries moved to `_evm_legacy/`
- No API / dashboard in active code — moved to `_evm_legacy/src/api/` and `_evm_legacy/src/dashboards/`
- No Raydium / Orca / Meteora direct-pool adapters (venue registry entries are disabled)
- `log_parser.py` still references pre-Solana event shapes; not loaded by any active code

### Immediate next steps (Phase 2)

1. Run `run_event_driven.py --mode jupiter` against real Jupiter for ≥1 hour
2. Analyse `latency.jsonl` — check p95 of `rpc_fetch` vs `scanner` vs `total_ms`
3. Tune `RiskPolicy.min_spread_pct` / `min_net_profit` / `min_liquidity_usd`
   from observed distributions (currently defaults from the EVM codebase)
4. Add direct-pool quote adapters for Raydium/Orca when Jupiter's two-route
   trick stops producing meaningful divergence
5. Replace the legacy `smart_alerts` no-op with a Solana-native hourly/daily
   summary (wallet SOL balance, fee spend in lamports, per-venue success rate)

### Deferred to Phase 3+

- `SolanaExecutor` — Jupiter swap instruction + priority-fee compute-budget + send/poll
- `SolanaVerifier` — `getSignatureStatuses` + slot confirmation + realized-PnL parsing
- Jito bundle submission path
- Dashboard rebuild (Solscan links, lamport formatting, route health cards)
- `scripts/rehearsal.py` equivalent for Solana (testnet or mainnet replay)

