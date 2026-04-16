# Execution Phase Status

Last updated: 2026-04-16

This document captures the current execution/go-live status so work can resume quickly later without reconstructing context from chat history.

## Current Phase

**Three chains LIVE in production.** Arbitrum, Base, and Optimism are executing real trades. Ethereum is simulated (gas too expensive). Full PnL analytics, scan history, and per-chain execution controls deployed.

## Deployed Contracts

| Chain | Address | Aave Pool | Status |
|-------|---------|-----------|--------|
| Arbitrum | `0x95AFF47C4E58F4e4d2A0586bbBEDdbd926198115` | `0x794a61358D6845594F94dc1DB02A252b5b4814aD` | **LIVE** |
| Optimism | `0x95AFF47C4E58F4e4d2A0586bbBEDdbd926198115` | `0x794a61358D6845594F94dc1DB02A252b5b4814aD` | **LIVE** |
| Base | `0x3ff116dd77CCf80f5908928b217C89d1a182a8B8` | `0xA238Dd80C259a72e81d7e4664a9801593F98d1c5` | **LIVE** |

Owner: `0xcfF46971b1BA42d74C4c51ec850c7F33f903EAeB`

Contract version: FlashArbExecutor v2 — supports V3 routers (exactInputSingle) and Solidly-fork routers (Velodrome/Aerodrome swapExactTokensForTokens).

Per-chain contract env vars:
- `EXECUTOR_CONTRACT=0x95AFF47C4E58F4e4d2A0586bbBEDdbd926198115` (Arbitrum/Optimism)
- `EXECUTOR_CONTRACT_BASE=0x3ff116dd77CCf80f5908928b217C89d1a182a8B8`

## Wallet Balances (as of 2026-04-16)

| Chain | Balance | Status |
|-------|---------|--------|
| Arbitrum | ~0.006 ETH | LIVE |
| Optimism | ~0.005 ETH | LIVE |
| Base | ~0.010 ETH | LIVE |
| Ethereum | ~0.011 ETH | Simulated (gas too expensive) |

## Chain Execution Modes

Defined in `config/multichain_onchain_config.json` → `chain_execution_mode`:

```json
{
  "arbitrum": "live",
  "optimism": "live",
  "base": "live",
  "ethereum": "simulated"
}
```

Persists across container restarts. Can be toggled at runtime via dashboard buttons or API:
```bash
curl -u admin:$PASS -X POST http://localhost:8000/execution \
  -H 'Content-Type: application/json' -d '{"chain":"arbitrum","mode":"simulated"}'
```

## Per-Chain Gas Costs

Defined in `config/multichain_onchain_config.json` → `chain_gas_cost`:

| Chain | Gas Estimate | Why |
|-------|-------------|-----|
| Ethereum | 0.005 ETH (~$11.50) | Too expensive — eats all profit on 30-50 bps spreads |
| Arbitrum | 0.0002 ETH (~$0.46) | Cheap, viable |
| Optimism | 0.0001 ETH (~$0.23) | Very cheap |
| Base | 0.0001 ETH (~$0.23) | Very cheap |

## Trade Sizes (Flash Loan Amounts)

| Pair | Trade Size | Flash Loan (~USD) |
|------|-----------|-------------------|
| WETH/USDC | 1.0 WETH | ~$2,340 |
| WETH/USDT | 1.0 WETH | ~$2,340 |
| OP/USDC | 20,000 OP | ~$2,400 |

## Production Observations

### Spreads Seen (2026-04-15 to 2026-04-16)

| Chain | Pair | Spread | Route | Notes |
|-------|------|--------|-------|-------|
| Arbitrum | WETH/USDC | 11-15 bps | Sushi→Uniswap | Tight, rarely profitable after costs |
| Base | WETH/USDT | 58-68 bps | Aerodrome→Uniswap | Best spread, viable |
| Base | WETH/USDC | 30-42 bps | Aerodrome/Sushi→Uniswap | Moderate |
| Ethereum | WETH/USDC | 31-55 bps | Sushi→Uniswap | Good spread but gas kills profit |
| Optimism | Velodrome quotes | 11% deviation | Filtered as outliers | Thin USDC.e pools |

### Pipeline Results

Most opportunities are filtered at scanner level (`unprofitable` after per-chain gas). Zero trades have been executed yet — spreads don't clear the min_profit threshold after costs in calm markets. Real executions expected during volatility spikes.

## What Is Done

### Infrastructure
- Contracts deployed on 3 chains (Arbitrum, Optimism, Base)
- Per-chain execution mode persists in config (survives restarts)
- Per-chain gas cost estimates (Ethereum 0.005, L2s 0.0001-0.0002)
- Per-chain contract addresses (`EXECUTOR_CONTRACT_BASE`)
- Deployment script: `./scripts/deploy_prod.sh`

### Execution Stack
- FlashArbExecutor v2: V3 + Solidly-fork (Velodrome/Aerodrome) swap paths
- `ChainExecutor` routes via `swapTypeA/B` flags
- `run_event_driven.py` wires: simulator, submitter, verifier
- Supported: `uniswap_v3`, `sushi_v3`, `pancakeswap_v3`, `velodrome_v2`, `aerodrome`
- Unsupported: `curve`, `traderjoe_lb`

### Dashboard (3 pages)

**Main Dashboard (`/dashboard`)**
- Per-chain execution status table with Go Live / Pause / Disable buttons
- Wallet balances (Arbitrum, Ethereum, Base, Optimism)
- Status/pair/chain filters on opportunities table
- Sortable columns, expandable execution detail rows
- Scanner controls, time windows, hourly chart

**Operations (`/ops`)**
- RPC health, DEX health, scan metrics, risk policy

**PnL Analytics (`/analytics`)**
- Filters: chain, time window, date range
- Summary cards, hourly PnL, profit by pair, profit by venue route
- Expected vs realized, gas efficiency, rejection reasons
- **Scan history**: filter breakdown, spread distribution, near-miss analysis

### Data Persistence
- All pipeline stages persist to Postgres (Neon): opportunities, pricing, risk, simulation, execution, trade results
- `scan_history` table: every evaluated pair per scan cycle (async flush, zero pipeline latency)
- `/scan-history` and `/scan-history/summary` API endpoints

### Fixes Applied
- Velodrome V2 factory address corrected (`0xF10460...`)
- Velodrome USDC.e fallback picks deeper pool
- Optimism RPC: Infura (429'd) → `1rpc.io/op` → Llamanodes
- Per-chain gas prevents false Ethereum approvals
- `event_listener.py` removed (superseded by `run_event_driven.py`)

## What Is Still Open

### 1. No executed trades yet
Spreads don't clear min_profit after costs in calm markets. Waiting for volatility. The full submit→verify→PnL pipeline is wired and tested but not yet proven with real capital.

### 2. Velodrome/Optimism outlier filtering
Velodrome quotes deviate >5% from median and get filtered. The spread is real but the pools are thin. Options: lower outlier threshold, or accept thin pools aren't reliably arbitrageable.

### 3. Ethereum stays simulated
Gas (~$11.50) exceeds typical profit (~$5-7). Only viable during major volatility events with >100 bps spreads.

## Most Relevant Files

Core live path:
- `src/run_event_driven.py` — production scanner + pipeline consumer
- `src/execution/chain_executor.py` — tx building, signing, execution-side addresses
- `src/pipeline/lifecycle.py` — detect → price → risk → simulate → submit → verify
- `src/pipeline/verifier.py` — on-chain PnL extraction

Contract addresses (two files, must stay in sync):
- `src/core/contracts.py` — READ-ONLY quoter addresses (price fetching)
- `src/execution/chain_executor.py` — EXECUTION swap router addresses (trading)
- `src/core/tokens.py` — ERC-20 token addresses per chain

Persistence / API / dashboard:
- `src/persistence/db.py` — schema including `scan_history`
- `src/persistence/repository.py` — queries including `get_pnl_analytics()`, `get_scan_summary()`
- `src/api/app.py` — all API endpoints
- `src/api/dashboard.py` — 3 dashboard pages (main, ops, analytics)

Configs:
- `config/multichain_onchain_config.json` — production (all chains, execution modes, gas costs)
- `config/arbitrum_live_execution_config.json` — Arbitrum-only
- `config/optimism_live_execution_config.json` — Optimism-only

Scripts:
- `scripts/deploy_prod.sh` — one-command production deploy
- `scripts/check_readiness.py` — launch readiness CLI
- `scripts/fork_rehearsal.py` — fork-style dry execution rehearsal

Docs:
- `docs/go_live_checklist.md` — step-by-step go-live guide
- `docs/rpc_providers.md` — RPC provider reference

## Test Coverage

```
855 passed, 8 skipped
```

## Resume Prompt

> Bot is live on Arbitrum + Base + Optimism. Ethereum simulated. 3 contracts deployed. Per-chain gas, execution modes, scan history all in production. 855 tests. No trades executed yet (spreads too thin in calm markets). Check `docs/execution_phase_status.md` and `docs/go_live_checklist.md`.
