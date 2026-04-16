# Arbitrage Trader Architecture

## Purpose

Build a production-grade **DEX-only arbitrage trading system** that can:

- detect real arbitrage opportunities
- simulate trades before execution
- estimate realistic net profitability
- protect against MEV and execution failure
- submit transactions privately
- track results, PnL, and system health
- recover safely after restart

This system is for **real trading**, not just spread detection.

---

# Core Principle

Arbitrage is **not**:

spread -> execute

Arbitrage is:

detect -> normalize -> filter -> simulate -> estimate gas -> estimate slippage -> assess risk -> submit privately -> verify -> reconcile

The real edge is **execution quality**, not simply finding spreads.

---

# Scope

## In Scope

- DEX-only arbitrage
- same-chain cross-DEX arbitrage
- optional same-chain triangular arbitrage later
- private execution
- gas-aware profitability
- latency-aware architecture
- deterministic logic
- restart-safe persistence
- observability and replayability

## Out of Scope (for initial versions)

- CEX/DEX arbitrage
- cross-chain arbitrage
- bridge-based arbitrage
- LLMs in live execution path
- public mempool execution
- non-atomic manual execution

---

# Strategy Focus

## Primary Strategy: Same-Chain Cross-DEX Arbitrage

Example:

- Buy WETH on DEX A
- Sell WETH on DEX B
- Both trades happen atomically in one transaction or flash-loan-based execution path

Why this is the primary strategy:

- simplest DEX-only arbitrage model
- no bridge risk
- no custody on centralized venues
- can be executed atomically
- easier to simulate and reason about

## Secondary Strategy: Triangular Arbitrage

Example:

- WETH -> USDC -> DAI -> WETH

Use only after the primary strategy is stable.

Why secondary:

- more route complexity
- smaller margins
- more sensitive to gas/slippage

## Advanced Strategy: Backrun Large Swaps

Example:

- detect large pending swap
- predict post-trade price distortion
- submit arbitrage right after the target transaction using a private bundle

Use only after core same-chain arbitrage and private submission are working.

---

# Product Model

Build the system in **two layers**:

## Layer 1: Scanner Product

Purpose:

- discover and rank opportunities

Responsibilities:

- ingest market data
- normalize pair pricing across DEXs
- detect spreads
- filter low-quality candidates
- rank by likelihood and opportunity quality
- output candidates for execution evaluation

The scanner is useful even before live trading is enabled.

## Layer 2: Execution Product

Purpose:

- decide if a candidate is truly tradable
- simulate, validate, and execute safely

Responsibilities:

- calculate realistic net profitability
- estimate gas and slippage
- reject unsafe opportunities
- build transactions
- submit privately
- verify results
- reconcile actual vs expected PnL

The execution layer is the actual trading bot.

---

# Architecture Decision

## Recommended Approach

Use:

- deterministic custom code
- modular architecture
- one repo
- one deployable application at first
- then split into multiple services only where operationally useful

## Do Not Start With

- LLM autonomous agents
- agent frameworks in the live trading path
- many microservices too early
- public mempool arbitrage execution

## Why

Trading decisions must be:

- deterministic
- auditable
- replayable
- fast
- numerically precise

---

# Architecture Overview

## High-Level Components

1. Market Data Layer
2. Pair Registry
3. Scanner Layer
4. Pricing Engine
5. Risk Engine
6. Simulation Engine
7. Execution Engine
8. Private Submission Layer
9. Persistence Layer
10. Observability Layer
11. Control/API Layer
12. Session Persistence Layer

---

# Service Model

## Recommended MVP

Start with one codebase, modularized internally.

Suggested modules:

```text
src/
  common/
  data/
  registry/
  scanner/
  pricing/
  risk/
  simulation/
  execution/
  persistence/
  observability/
  api/
```

## Recommended Evolution

After the MVP is stable, split into:

- scanner service
- execution service
- api/state service
- optional mempool service

---

# Module Responsibilities

## 1. Market Data Layer

Purpose:

- fetch real-time pool and market state

Inputs:

- RPC endpoints
- WebSocket subscriptions
- subgraph APIs
- local caches

Responsibilities:

- read reserves, pool state, quotes, ticks, liquidity, fees
- maintain fresh local state
- normalize data formats
- detect stale/incomplete data
- support multiple DEX types

Outputs:

- normalized pool state
- normalized price data
- updated liquidity snapshots

### Notes

- prefer RPC/WebSocket for freshness
- use subgraphs as support, not sole source of truth for execution
- avoid repeated expensive calls in the hot path
- cache aggressively but safely

---

## 2. Pair Registry

Purpose:

- define what the system is allowed to trade

Responsibilities:

- maintain supported tokens
- maintain supported DEX pools
- map canonical pairs to pool addresses
- store pair metadata:
  - decimals
  - fee tiers
  - liquidity class
  - token risk category
  - route compatibility

Outputs:

- list of tradable pairs
- liquidity-ranked pair universe
- allowed route graph

### Rules

- start with high-liquidity pairs only
- avoid thin and exotic pairs in early versions
- whitelist rather than discover everything dynamically

---

## 3. Scanner Layer

Purpose:

- detect candidate arbitrage opportunities

Responsibilities:

- compare same-pair prices across DEXs
- compute raw spread
- identify route candidates
- score opportunity quality
- drop obviously bad candidates quickly

Outputs:

- structured candidate opportunities

Example candidate:

```json
{
  "opportunity_id": "opp_123",
  "pair": "WETH/USDC",
  "chain": "ethereum",
  "buy_dex": "uniswap_v3",
  "sell_dex": "sushiswap",
  "spread_bps": 42,
  "timestamp_ms": 1710000000000,
  "estimated_depth_usd": 250000,
  "candidate_status": "detected"
}
```

### Scanner Rules

- scanner does not approve trades
- scanner only produces candidates
- scanner must be fast and conservative
- scanner should prefer false negatives over noisy false positives in production

---

## 4. Pricing Engine

Purpose:

- estimate realistic trade economics

Responsibilities:

- estimate route outputs
- estimate pool fees
- model slippage
- calculate expected gross profit
- calculate expected net profit before execution

Core formula:

```text
net_profit = output_value - input_value - swap_fees - gas_cost - slippage_cost - execution_buffer
```

### Pricing Requirements

- never use float for balances or PnL
- use precise integer math or Decimal
- handle token decimals explicitly
- include fee tiers per pool
- include configurable buffers

Outputs:

- estimated input/output
- estimated slippage
- gross and net PnL
- minimum viable size suggestion

---

## 5. Risk Engine

Purpose:

- reject unsafe or low-quality trades

Responsibilities:

- enforce trade thresholds
- reject opportunities below minimum expected edge
- reject stale quotes
- reject low-liquidity routes
- reject trades with excessive price impact
- reject routes too sensitive to gas spikes
- reject trades with poor execution confidence

Example policies:

- minimum net profit threshold
- maximum slippage threshold
- maximum trade size by pair
- minimum pool liquidity threshold
- maximum quote age
- maximum gas cost as % of expected profit

Outputs:

- approved / rejected decision
- explicit rejection reason

### Principle

No trade is better than a bad trade.

---

## 6. Simulation Engine

Purpose:

- validate a trade path before submission

Responsibilities:

- simulate route execution
- simulate atomic path if flash loan is used
- detect reverts
- validate expected outputs
- verify trade still clears risk thresholds under simulation

### Requirements

- deterministic
- close to real execution conditions
- supports candidate replay for debugging
- fast enough for hot-path use

Outputs:

- simulation result
- expected token deltas
- revert/no-revert
- confidence score

### Rule

Never execute without simulation.

---

## 7. Execution Engine

Purpose:

- construct executable trades

Responsibilities:

- build transaction calldata
- build atomic multi-leg trade
- optionally build flash-loan route
- enforce all-or-nothing trade conditions
- prepare execution payload for relay/builder submission

### Execution Requirements

- execution must revert if profitability guardrails fail on-chain
- execution must include slippage protection
- execution must be atomic
- execution logic must be simple, deterministic, and auditable

Outputs:

- signed transaction or bundle payload
- execution metadata
- idempotency key / trade key

---

## 8. Private Submission Layer

Purpose:

- protect trades from public mempool competition

Responsibilities:

- submit transactions privately
- submit bundles to private relays/builders
- support retries with bounded policy
- track inclusion / non-inclusion
- optionally submit bundle variants

### Rules

- never send arbitrage trades to public mempool by default
- private submission is required for real execution
- if private submission fails, do not blindly retry without re-evaluation

Outputs:

- submitted bundle records
- inclusion result
- relay/builder status
- failure reasons

---

## 9. Persistence Layer

Purpose:

- make the system restart-safe and auditable

Use:

- PostgreSQL as durable state store

Persist:

- opportunities
- simulations
- risk decisions
- execution attempts
- tx hashes
- block numbers
- PnL results
- bot config version
- nonce / execution coordination state
- last processed event/block
- reconciliation records

### Principles

- database = current system state
- append-only execution log = history
- blockchain = external source of truth
- memory = cache only

---

## 10. Observability Layer

Purpose:

- make every decision traceable

Responsibilities:

- structured logs
- metrics
- traces
- alerts
- dashboards

Log at minimum:

- candidate detected
- candidate rejected
- rejection reason
- simulation started/completed
- execution submitted
- inclusion result
- actual gas used
- expected vs actual PnL
- reconciliation result

Metrics to track:

- opportunities detected per minute
- candidates rejected per reason
- simulation success rate
- execution inclusion rate
- revert rate
- average expected PnL
- average actual PnL
- gas cost distribution
- latency from detect -> submit
- data freshness lag

### Rule

Every trade decision must be explainable after the fact.

---

## 11. Control / API Layer

Purpose:

- operate and inspect the system safely

Recommended tech:

- FastAPI

Responsibilities:

- system health endpoints
- current config inspection
- enable/disable execution
- simulation-only toggle
- current opportunities
- recent trades
- recent failures
- replay endpoint for a candidate
- manual pause/kill switch

### Control Rules

- live execution must be explicitly enabled
- there must be a global kill switch
- all config changes must be logged

---

## 12. Session Persistence Layer

Purpose:

- help Claude Code and operators survive restarts cleanly

Use:

```text
claude_session/
  current.md
  decisions.md
  tasks.md
  scratch.md
```

Rules:

- `current.md` = current state of work
- `decisions.md` = durable architectural or operational decisions
- `tasks.md` = current open tasks
- `scratch.md` = temporary notes only

Claude should read these on restart.

---

# Runtime Flow

## End-to-End Flow

1. data layer updates pool state
2. scanner finds raw opportunity
3. scanner outputs candidate
4. pricing engine estimates economics
5. risk engine approves or rejects
6. simulation engine validates trade path
7. execution engine builds tx/bundle
8. private submission layer sends trade privately
9. observability layer records all events
10. persistence layer stores results
11. reconciliation verifies on-chain outcome

---

# Detailed Candidate Lifecycle

## Stage 1: Detection

Status:

- detected

Fields:

- pair
- chain
- buy venue
- sell venue
- raw spread
- timestamp
- liquidity estimate

## Stage 2: Pricing

Status:

- priced

Fields:

- estimated input/output
- fees
- slippage estimate
- gas estimate
- expected net profit

## Stage 3: Risk

Status:

- approved
- rejected

Fields:

- decision
- reason code
- threshold values
- confidence

## Stage 4: Simulation

Status:

- simulated_ok
- simulated_fail

Fields:

- expected deltas
- revert reason if any
- post-simulation net estimate

## Stage 5: Submission

Status:

- submitted
- not_submitted

Fields:

- relay/builder target
- bundle ID
- tx hash
- target block
- submission latency

## Stage 6: Outcome

Status:

- included
- not_included
- reverted
- expired

Fields:

- actual gas used
- actual output
- actual net profit
- block included
- failure category

---

# Latency Requirements

## Critical Path

Hot path:

detect -> price -> risk -> simulate -> build -> submit

This must be highly optimized.

## Optimization Goals

- minimal RPC calls in the hot path
- aggressive caching for pool state
- precomputed route metadata
- parallel quote and validation work where safe
- prebuilt execution templates
- short-lived in-memory candidate queue

## Latency Risks

- slow quote fetching
- excessive synchronous RPC calls
- building tx calldata from scratch every time
- unnecessary DB writes in the hot path
- using subgraphs as live execution source

---

# Mempool / Backrun Extension

This is advanced and should come after private same-chain arbitrage is working.

## Additional Components

- mempool listener
- tx decoder
- pending swap classifier
- impact predictor
- bundle composer

## Flow

1. watch pending swaps
2. decode large or relevant trades
3. estimate post-trade pool distortion
4. generate arbitrage candidate
5. simulate backrun
6. submit private bundle

## Risk

- high competition
- high operational complexity
- requires excellent latency and simulation

---

# Data Model

## Suggested Core Tables

### pairs
- pair_id
- chain
- base_token
- quote_token
- enabled
- risk_class

### pools
- pool_id
- chain
- dex
- address
- pair_id
- fee_tier
- enabled
- liquidity_class

### opportunities
- opportunity_id
- pair_id
- chain
- buy_pool_id
- sell_pool_id
- raw_spread_bps
- detected_at
- status

### pricing_results
- pricing_id
- opportunity_id
- input_amount
- estimated_output
- fee_cost
- slippage_cost
- gas_estimate
- expected_net_profit
- created_at

### risk_decisions
- decision_id
- opportunity_id
- approved
- reason_code
- threshold_snapshot
- created_at

### simulations
- simulation_id
- opportunity_id
- success
- revert_reason
- expected_output
- expected_net_profit
- created_at

### execution_attempts
- execution_id
- opportunity_id
- submission_type
- relay_target
- tx_hash
- bundle_id
- target_block
- status
- submitted_at

### trade_results
- result_id
- execution_id
- included
- reverted
- gas_used
- actual_output
- actual_net_profit
- block_number
- finalized_at

### system_checkpoints
- checkpoint_id
- checkpoint_type
- value
- updated_at

---

# Queueing Model

## MVP

Use one of:

- PostgreSQL polling queue
- Redis queue / Redis streams

Recommended early choice:

- PostgreSQL if you want simplicity and durability
- Redis if you want faster event passing

## Event Flow

```text
scanner -> candidate queue -> execution pipeline -> results store
```

---

# Safety Controls

## Global Controls

- simulation-only mode default
- live execution disabled by default
- kill switch
- max trades per interval
- max exposure per pair
- max exposure per block window
- pause on repeated reverts
- pause on stale data
- pause on RPC degradation

## Trade-Level Controls

- minimum expected net profit
- maximum allowed slippage
- minimum liquidity
- maximum quote age
- gas-profit ratio ceiling
- on-chain profitability assertion if supported

---

# Numeric Correctness

## Rules

- never use float for money, token amounts, or PnL
- use Decimal or integer-safe token math
- handle token decimals explicitly
- preserve precision across all route calculations
- normalize units before comparisons

---

# Execution Safety Rules

- never execute without simulation
- never trust raw spread alone
- never enable live trading without explicit operator approval
- never modify wallet or private key handling casually
- never submit arbitrage to public mempool by default
- never assume a detected spread survives execution
- always include gas and slippage in net profitability
- always treat MEV competition as real

---

# Git and Operational Discipline

## Version Control Rules

- commit frequently for meaningful progress
- prefer small, reversible commits
- use clear commit messages
- never commit:
  - `.env`
  - secrets
  - `data/`
  - `logs/`

## Deployment Rules

- do not deploy contract changes casually
- do not enable live mode as part of development testing
- require explicit config separation for sim vs live

---

# LLM / Claude Usage Policy

## Allowed Uses

- code generation assistance
- architecture iteration
- refactoring help
- documentation
- test scaffolding
- log analysis
- incident summarization
- strategy research

## Not Allowed In Live Critical Path

- approving live trades
- deciding slippage or gas dynamically without deterministic checks
- final profitability decision for live execution
- constructing live transaction intent without deterministic validation

### Principle

Use Claude as an engineering copilot, not as the live trading decision engine.

---

# Build Plan

## Phase 1: Scanner MVP

Build:

- pair registry
- market data ingestion
- pool normalization
- spread detector
- candidate ranking
- structured candidate output
- logs and metrics

Do not build live execution yet.

Success criteria:

- scanner produces high-quality candidates
- scanner avoids obvious low-liquidity/noisy opportunities
- candidate pipeline is replayable and observable

## Phase 2: Pricing + Risk + Simulation

Build:

- net profitability engine
- gas estimation
- slippage modeling
- risk policy engine
- trade simulation engine

Success criteria:

- candidates can be classified as tradable or not tradable
- false positives are reduced sharply
- simulation results are deterministic and logged

## Phase 3: Private Execution

Build:

- transaction builder
- atomic route executor
- private submission relay integration
- execution tracker
- outcome reconciliation

Success criteria:

- live path is technically complete
- private submission works
- end-to-end execution is observable and restart-safe

## Phase 4: Production Hardening

Build:

- retries with bounded rules
- failure categorization
- pause/kill switch logic
- improved dashboards
- latency optimization
- RPC failover
- config versioning

Success criteria:

- system degrades safely
- repeated failures pause execution automatically
- operational control is strong

## Phase 5: Advanced Extensions

Build:

- triangular arbitrage
- mempool watcher
- backrun strategy
- multi-builder bundle strategy
- route optimization improvements

Only after earlier phases are stable.

---

# File / Repo Recommendations

## Suggested Repo Structure

```text
src/
  common/
    models/
    schemas/
    config/
    utils/
  data/
    rpc_client.py
    ws_client.py
    subgraph_client.py
    cache.py
  registry/
    pairs.py
    pools.py
  scanner/
    detector.py
    ranker.py
    filters.py
  pricing/
    quote_engine.py
    gas_model.py
    pnl.py
  risk/
    policy.py
    rules.py
  simulation/
    simulator.py
  execution/
    tx_builder.py
    flash_loan_builder.py
    relay_submitter.py
    reconciler.py
  persistence/
    db.py
    repositories.py
    queue.py
  observability/
    logging.py
    metrics.py
    tracing.py
  api/
    app.py
tests/
config/
docs/
claude_session/
```

---

# Session / Restart Protocol

On each new Claude Code session:

1. read `CLAUDE.md`
2. read `claude_session/current.md`
3. read `claude_session/decisions.md`
4. review recent open tasks
5. summarize current state before changing code

After meaningful progress:

- update `claude_session/current.md`
- record durable decisions in `claude_session/decisions.md`

---

# Definition of Done

A production-ready DEX arbitrage trader must:

- detect real same-chain opportunities
- reject bad opportunities reliably
- simulate before execution
- estimate realistic net profitability
- submit privately
- track expected vs actual PnL
- survive restart safely
- provide full traceability for every trade attempt
- keep live execution deterministic and controlled

---

# Final Principles

- scanner is not the bot
- detection is easy; execution is hard
- speed matters, but safe execution matters more
- no trade is better than a bad trade
- private submission is mandatory for serious DEX arbitrage
- every decision must be auditable
- deterministic code wins in live trading
