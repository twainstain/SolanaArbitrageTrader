# Solana Support Plan

Last updated: 2026-04-16

## Recommendation

Do **not** replace Ethereum/EVM logic in-place.

Instead:

1. Keep the shared operational pieces in the future `trading_platform` library.
2. Add a **Solana-specific product adapter** beside the current EVM arbitrage code.
3. Reuse the existing pipeline, risk, alerting, persistence, API, dashboards, and observability patterns wherever possible.

Short answer on "do we need a new agent?":

- We do **not** need a brand-new generic agent/runtime.
- We **do** need a new **Solana execution + market adapter layer** because the current implementation is deeply EVM-specific.
- The `trading_platform` work is the right place to centralize shared infrastructure so Solana can plug in cleanly.

## Current Architecture Reality

The current live system is EVM-first:

- `src/execution/chain_executor.py` assumes `web3.py`, EVM RPC, contract addresses, router ABIs, `eth_call`, signing, and FlashArbExecutor.
- `contracts/FlashArbExecutor.sol` is Solidity + Aave-flash-loan based.
- `src/market/onchain_market.py` assumes EVM DEX quoters and token addresses.
- `src/core/tokens.py` and `src/core/contracts.py` are ERC-20 / router / quoter registries.
- `docs/execution_phase_status.md` shows live rollout on Arbitrum, Base, and Optimism as of 2026-04-16.

What is reusable:

- `src/pipeline/lifecycle.py`
- `src/pipeline/queue.py`
- `src/risk/*`
- `src/alerting/*`
- `src/observability/*`
- `src/persistence/*`
- `src/api/*`

This matches the direction already documented in `docs/trading_platform_plan.md`.

## What Changes for Solana

Solana is not a drop-in chain addition.

Main differences:

- No EVM ABI / `web3.py` execution path
- No Solidity executor contract reuse
- No Aave-style flash-loan path as currently modeled
- Different account model, transaction building, simulation, and fee model
- Different DEX integrations and quote sources
- Different token metadata and address formats

Because of that, the right unit of change is not "add Solana to `SUPPORTED_CHAINS`".
The right unit is "introduce a Solana implementation for market data, execution, verification, and chain-specific config".

## Target Design

### Shared platform

Move or preserve as shared:

- candidate pipeline
- risk engine framework
- circuit breaker
- persistence base patterns
- metrics / latency / logs
- alerting
- API shell and dashboard shell

### EVM product adapter

Keep current logic under an EVM-specific slice:

- `market/onchain_market.py`
- `execution/chain_executor.py`
- `core/contracts.py`
- `core/tokens.py`

### Solana product adapter

Add Solana-specific modules:

- `src/solana/market/solana_market.py`
- `src/solana/execution/solana_executor.py`
- `src/solana/execution/solana_verifier.py`
- `src/solana/core/tokens.py`
- `src/solana/core/pools.py`
- `src/solana/core/models.py` if needed for chain-specific fields

## Phased Plan

### Phase 0: Architecture extraction

Goal: create the seam before chain expansion.

Work:

- Continue the `trading_platform` extraction from `docs/trading_platform_plan.md`
- Define interfaces for:
  - quote provider
  - simulator
  - submitter
  - verifier
  - chain config provider
- Remove EVM assumptions from shared pipeline types where practical

Exit criteria:

- Pipeline and risk layers run without importing EVM-only modules
- ArbitrageTrader still works unchanged on existing EVM chains

### Phase 1: Solana read-only market data

Goal: detect opportunities safely before any execution work.

Work:

- Implement Solana token registry and pair config
- Implement Solana DEX quote adapters
- Normalize quotes into the existing `MarketQuote` / opportunity flow
- Add Solana-specific config file(s)
- Add tests for quote normalization, decimals, and pair identity

Important product decision:

- Start with **scanner-only / paper mode**
- No live execution in this phase

Exit criteria:

- Solana opportunities appear in dashboard/API
- Persistence, metrics, and alerting work for Solana scans
- No execution code required yet

### Phase 2: Solana strategy and risk calibration

Goal: make detected opportunities meaningful.

Work:

- Calibrate Solana fee model
- Add chain-specific slippage assumptions
- Add Solana min-liquidity thresholds
- Add Solana gas/fee-equivalent accounting
- Tune outlier filtering for Solana pool behavior

Exit criteria:

- Near-miss and rejection analytics look realistic
- False positives are low enough for rehearsal

### Phase 3: Solana execution adapter

Goal: execute on Solana without contaminating the EVM path.

Work:

- Build `solana_executor.py` with:
  - transaction builder
  - preflight simulation
  - signer integration
  - send / confirm flow
- Build `solana_verifier.py`
- Extend pipeline wiring to dispatch by execution backend, not just EVM chain name

Important note:

- This should be treated as a different execution backend, not a small patch to `ChainExecutor`

Exit criteria:

- End-to-end local dry-run / testnet or replay rehearsal passes
- Verification records expected vs realized PnL

### Phase 4: Solana go-live rehearsal

Goal: prove operational readiness before capital.

Work:

- Add readiness checks equivalent to `scripts/check_readiness.py`
- Add fork/rehearsal equivalent where possible for Solana
- Run simulation-only in production-like conditions
- Review alerting, balances, RPC stability, and route quality

Exit criteria:

- At least one clean rehearsal path through detect -> risk -> simulate -> submit -> verify in non-production capital conditions

### Phase 5: Narrow Solana rollout

Goal: go live carefully.

Work:

- Enable one pair
- Enable one or two Solana venues only
- Cap trade size aggressively
- Keep instant rollback / disable controls in dashboard/API

Exit criteria:

- First successful real trade verified
- Spread capture and failure rate are acceptable

### Phase 6: Scale-out

Goal: broaden coverage only after proof.

Work:

- Add more pairs
- Add more venues
- Tune thresholds from observed data
- Unify dashboards so EVM and Solana analytics can be compared cleanly

## Recommended Build Order

1. Finish shared `trading_platform` seams first
2. Add Solana scanner-only support
3. Validate data quality and risk thresholds
4. Build Solana execution backend
5. Rehearse
6. Narrow go-live

## Answer to "Do We Need a New Agent?"

Recommended answer: **No new generic agent. Yes new backend adapter.**

Use:

- existing shared platform effort for common infrastructure
- existing pipeline/risk/dashboard/persistence patterns
- a new Solana-specific market + execution backend

Avoid:

- forcing Solana into `ChainExecutor`
- mixing ERC-20 registries and Solana token/account metadata
- treating Solana as just another value in the current EVM chain enum

## Suggested Milestone Breakdown

### Milestone A

Platform seam complete.

### Milestone B

Solana scanner visible in dashboard, simulation only.

### Milestone C

Solana execution rehearsal complete.

### Milestone D

First narrow live rollout.

## Immediate Next Steps

1. Extract or formalize the backend interfaces from the current EVM path.
2. Decide the first Solana venues and first 1-2 pairs.
3. Implement scanner-only Solana ingestion before execution.
4. Keep EVM live stack untouched during early Solana work.
