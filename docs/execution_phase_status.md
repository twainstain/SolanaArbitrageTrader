# Execution Phase Status

Last updated: 2026-04-15

This document captures the current execution/go-live status so work can resume quickly later without reconstructing context from chat history.

## Current Phase

Post-review hardening complete for the highest-priority issues. The execution stack, dashboards, and per-config readiness checks are in place for **Arbitrum** and **Optimism**, and the multichain persistence / execution-metadata issues found in review have been fixed.

Current focus:
- do a narrow Arbitrum-first rollout and validate first real fills
- monitor realized PnL and revert behavior from the first production trades
- treat Optimism as second-wave rollout after reviewing Arbitrum live data

Not in scope right now:
- Base live execution (wallet underfunded, only 0.000005 ETH)
- Bridge-based cross-chain execution
- Curve execution

## Deployed Contracts

| Chain | Address | Aave Pool | Status |
|-------|---------|-----------|--------|
| Arbitrum | `0x95AFF47C4E58F4e4d2A0586bbBEDdbd926198115` | `0x794a61358D6845594F94dc1DB02A252b5b4814aD` | LIVE READY |
| Optimism | `0x95AFF47C4E58F4e4d2A0586bbBEDdbd926198115` | `0x794a61358D6845594F94dc1DB02A252b5b4814aD` | LIVE READY |

Owner: `0xcfF46971b1BA42d74C4c51ec850c7F33f903EAeB`

Contract version: FlashArbExecutor v2 — supports both V3 routers (exactInputSingle) and Solidly-fork routers (Velodrome/Aerodrome swapExactTokensForTokens).

## Wallet Balances (as of 2026-04-15)

| Chain | Balance | Enough for Live? |
|-------|---------|-----------------|
| Arbitrum | ~0.010 ETH | Yes |
| Optimism | ~0.005 ETH | Yes |
| Ethereum | ~0.011 ETH | Yes (but gas is expensive) |
| Base | ~0.000005 ETH | No (needs funding) |

## Live Configs

### Arbitrum (`config/arbitrum_live_execution_config.json`)
- Pairs: WETH/USDC, WETH/USDT
- DEXes: Uniswap V3, Sushi V3
- Trade size: 1.0 ETH
- Min profit: 0.005 ETH (~$11.50)
- Observed spreads: 12-13 bps (tight, few executions expected)
- Readiness: GREEN

### Optimism (`config/optimism_live_execution_config.json`)
- Pairs: WETH/USDC
- DEXes: Uniswap V3, Velodrome V2
- Trade size: 0.1 ETH
- Min profit: 0.001 ETH (~$2.30)
- Observed spreads: 11% between Uniswap ($2337) and Velodrome ($2073)
- Note: Velodrome quotes are from thin USDC.e pools; outlier filter removes them at >5% deviation from median
- Readiness: GREEN

## What Is Done

### 1. Same-chain multi-pair detection path

- pair-aware `OnChainMarket` and `ArbitrageStrategy`
- discovered pair metadata preserved through on-chain scanning
- event-driven flow is pair-aware structurally

### 2. Metadata and ops persistence

- persisted discovered pairs, DB-backed monitored pool bootstrap
- ops dashboard + `/operations` + startup checkpoints

### 3. Live execution stack (V3 + Velodrome)

- `run_event_driven` wires: simulator, submitter, verifier
- **Supported live DEX types**:
  - `uniswap_v3`, `sushi_v3`, `pancakeswap_v3` (V3 routers)
  - `velodrome_v2`, `aerodrome` (Solidly-fork routers) — **NEW**
- Unsupported (detection only): `curve`, `traderjoe_lb`
- Contract `FlashArbExecutor.sol` has dual swap paths: `_swapV3()` and `_swapVelo()`
- `ChainExecutor` routes swap types via `swapTypeA/B` flags and `factoryA/B` addresses
- Velodrome/Aerodrome router and factory addresses in `SWAP_ROUTERS` and `VELO_FACTORIES`

### 4. Launch readiness infrastructure

- `scripts/check_readiness.py` — CLI readiness for any config
- `GET /launch-readiness` — API readiness for running instances
- `POST /execution` refuses live enablement unless readiness is green
- Runner forces simulation mode if launch is not ready

### 5. Fork execution rehearsal

- `scripts/fork_rehearsal.py --auto-anvil`
- Forks Arbitrum via anvil, builds tx, simulates, signs, submits, verifies
- All 7 checks passed (revert expected — no real arb at forked block)

### 6. Execution PnL persistence

All executed trades persist to DB:
- `execution_attempts`: tx_hash, bundle_id, target_block, submission_type
- `trade_results`: included, reverted, gas_used, realized_profit_quote, gas_cost_base, actual_net_profit, block_number
- `pricing_results`: expected_net_profit, fee_cost, slippage_cost, gas_estimate

### 7. Dashboard (3 pages)

#### Main Dashboard (`/dashboard`)
- Scanner controls (start/stop/execution toggle)
- System status cards (execution, paused, detected, included, PnL, latency)
- **Wallet balance cards** — live on-chain balances for Arbitrum, Ethereum, Base, Optimism
- Chain filter + time window tabs (5m to 1m)
- Hourly win/loss bar chart
- Per-chain breakdown table (sortable)
- **Opportunities table** (all columns sortable, click-to-sort with arrows):
  - Default sort: executed first, then by profit desc
  - Expected Profit only shown for approved statuses
  - Realized PnL column for executed trades
  - **Expandable execution detail rows** — click to see: tx hash (links to block explorer), inclusion, gas used, gas cost, realized profit, net PnL

#### Operations Dashboard (`/ops`)
- Infrastructure status (live stack, launch readiness, DB, discovered pairs)
- RPC health per chain (success rate, latency)
- DEX health table (per pair, success/failure, latency, last error)
- Scan metrics (uptime, opportunities/min, rejections, latency P95)
- Risk policy display

#### PnL Analytics Dashboard (`/analytics`) — **NEW**
- **Filters**: chain dropdown, time window (1h-1m), date range (from/to), Apply button
- **Summary cards**: total trades, win rate, net profit, gas cost, spread capture %, profit/trade
- **Hourly PnL bar chart**: green/red bars by hour
- **Profit by Pair table**: trades, wins, reverts, net profit, gas, avg profit
- **Profit by Venue Route table**: buy_dex→sell_dex win rate, identifies best venue pairs
- **Expected vs Realized table**: every included trade with capture %, tx hash links
- **Gas Efficiency table**: per-chain avg gas used vs estimated, avg gas cost
- **Rejection Reasons table**: why opportunities were NOT traded, avg expected profit

### 8. API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/pnl/analytics` | Comprehensive PnL analytics with chain/window/date filters |
| `/wallet/balance` | Live on-chain wallet balances (Arbitrum, Ethereum, Base, Optimism) |
| `/opportunities` | Joined query includes execution + trade_result data |
| `/opportunities/{id}/full` | Full lifecycle: pricing, risk, simulation, execution, trade result |

### 9. Optimism / Velodrome fixes

- Velodrome V2 factory address fixed: `0xF1046053aa5682b4F9a81b5481394DA16BE5FF5a` (was wrong)
- Velodrome USDC.e fallback: tries both native USDC and bridged USDC.e, picks the deeper pool
- Infura Optimism RPC replaced with `1rpc.io/op` (Infura was 429 rate-limited)
- All 3 Optimism DEXes return quotes: Uniswap ($2337), Velodrome ($2073), Sushi ($2016)

## What Is Still Blocked / Not Done

### 1. Pair identity fix completed

The persistence model now keys `pairs` by `(pair, chain)` and includes an in-place migration path for both SQLite and PostgreSQL/Neon. Chain-specific pool bootstrap and factory discovery now fetch pair rows by pair+chain before attaching pools.

Regression coverage added for:
- same pair name on multiple chains
- monitored-pool bootstrap creating distinct pair rows per chain
- migration from legacy SQLite schema to pair+chain uniqueness

### 2. Optimism Velodrome spreads filtered by outlier detector

The outlier filter removes Velodrome quotes because they deviate >5% from median. This means Velodrome arb opportunities on Optimism are detected but filtered before reaching the pipeline. Options:
- Lower outlier threshold for known thin pools
- Cross-validate against same-DEX other pairs
- Accept that Velodrome's thin USDC.e pools are not reliably arbitrageable at 0.1 ETH size

### 3. Submission-type persistence fix completed

`CandidatePipeline` now persists the actual submission path coming back from the submitter, so Arbitrum/Optimism public submissions are no longer mislabeled as `flashbots`.

### 4. Readiness / verification tooling needed cleanup

Two smaller tooling issues were found during review:
- `scripts/test_rpc_endpoints.py` exposed a helper named `test_endpoint()`, which caused `pytest -q` to fail from accidental collection outside `tests/`
- `scripts/check_readiness.py --api` used a default password of `test`, while the API default was `adminTest`

Both were fixed during this review, and full `pytest -q` is now clean.

### 5. Base underfunded

Base wallet has only 0.000005 ETH. Needs ~0.005 ETH to deploy and trade.

### 6. No real executed trades yet

All trades are `simulation_approved` (execution disabled). Need to enable live execution and monitor first real trades to validate PnL persistence and analytics.

## Flash Loan Behavior

The flash loan is part of execution itself.

Current behavior:
- Python calls `executeArbitrage(params)` — params include `swapTypeA/B` (V3 or Velo)
- Contract requests flash loan from Aave V3
- Aave calls back `executeOperation()`
- Contract dispatches swaps via `_swap()` → `_swapV3()` or `_swapVelo()`
- Repays principal + 9 bps fee, transfers profit to owner
- If profit < `minProfit`, entire tx reverts (no loss, only gas)

## Most Relevant Files

Core live path:
- `src/run_event_driven.py` — wires simulator + submitter + verifier
- `src/chain_executor.py` — builds/signs/sends tx, resolves routers + factories
- `src/pipeline/lifecycle.py` — detect → price → risk → simulate → submit → verify
- `src/pipeline/verifier.py` — extracts realized PnL from on-chain receipts

Persistence / API / dashboard:
- `src/persistence/db.py` — schema (opportunities, pricing, execution_attempts, trade_results)
- `src/persistence/repository.py` — queries including `get_pnl_analytics()`
- `src/api/app.py` — API endpoints including `/pnl/analytics`, `/wallet/balance`
- `src/api/dashboard.py` — dashboard HTML, ops HTML, analytics HTML

Configs:
- `config/arbitrum_live_execution_config.json`
- `config/optimism_live_execution_config.json`
- `config/multichain_onchain_config.json` (detection only, not live-ready)

Contract:
- `contracts/FlashArbExecutor.sol` — V3 + Velodrome dual swap paths
- `contracts/script/Deploy.s.sol` — forge script deployment (forge create --broadcast broken in v1.5.1)

Scripts:
- `scripts/check_readiness.py` — launch readiness CLI
- `scripts/fork_rehearsal.py` — fork-style dry execution rehearsal
- `scripts/run_local.sh` — local dev runner

Docs:
- `docs/rpc_providers.md` — RPC provider reference for adding/updating chains

## Test Coverage

```
840 passed, 8 skipped in full `pytest -q`
```

Key test files:
- `tests/test_chain_executor.py` — 43 tests (V3 + Velodrome routing, gas estimation, Flashbots)
- `tests/test_api.py` — 50 tests (endpoints, analytics, wallet, dashboard HTML)
- `tests/test_dashboard.py` — 82 tests (time windows, chains, profit, ops, execution stats)
- `tests/test_pipeline.py` — pipeline lifecycle
- `tests/test_verifier.py` — on-chain PnL extraction
- `tests/test_persistence.py` — DB operations

## Review Verdict

- Functionality: core lifecycle is implemented, the highest-priority correctness issues are fixed, and both Arbitrum and Optimism readiness checks are green in the current environment.
- Reliability: schema/repository migration is now chain-safe for both local SQLite and production PostgreSQL/Neon.
- Quality: test coverage is strong and now runs cleanly end-to-end.
- Performance: the event-driven stack looks healthy in simulation, but Arbitrum spreads remain tight and Optimism still depends on resolving the Velodrome outlier tension.

## Recommended Next Steps

### 1. Enable live execution on Arbitrum

```bash
curl -u admin:$DASHBOARD_PASS -X POST \
  http://localhost:8000/execution \
  -H 'Content-Type: application/json' \
  -d '{"enabled": true}'
```

Monitor first trades on `/analytics`. Expected: most trades will still be `simulation_approved` because spreads are tight; real executions likely require volatility spikes.

### 2. Tune thresholds from analytics data

Use `/analytics` rejection reasons to decide:
- Lower `min_profit_base` if spreads are consistently close but filtered?
- Adjust outlier filter if Velodrome quotes are being removed incorrectly?

### 3. Decide whether Optimism is truly rollout-ready

- if Velodrome opportunities continue to be filtered as outliers, keep Optimism in simulation until route quality is proven
- only widen rollout after reviewing real Arbitrum fills

### 4. Fund Base wallet and deploy there

Bridge 0.005 ETH to Base, deploy contract, create Base live config with Uniswap V3 + Aerodrome.

## Resume Prompt

If resuming later, a good starting prompt is:

> Bot now has chain-safe pair persistence, accurate submission-type persistence, green readiness checks on Arbitrum and Optimism, and 840 passing tests. Next: do a narrow Arbitrum rollout, review the first real fills, and only then expand.
