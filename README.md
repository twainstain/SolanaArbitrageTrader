# SolanaTrader

A Python Solana arbitrage scanner built on the `trading_platform` shared
library.  Phase 1 detects cross-venue arbitrage opportunities via the
Jupiter aggregator quote API; live execution (Phase 3) will build and
submit versioned transactions with a priority-fee + optional Jito tip.

This repo was **converted in-place** from the EVM ArbitrageTrader product.
The old EVM code lives under `_evm_legacy/` for reference.  See
`docs/solana_migration_status.md` for the migration plan and phase map.

## How It Works

```
1. FETCH       Solana market source pulls quotes (Jupiter best + direct routes)
2. FILTER      Outlier quotes removed (median-based per-pair deviation check)
3. EVALUATE    Strategy checks every buy-on-A / sell-on-B venue combination
4. RANK        Scanner scores by profit, liquidity, risk flags
5. DECIDE      Net profit > min threshold + no critical risk flags → queue
6. LOG         Every stage writes a latency sample to logs/latency.jsonl
```

Phase 1 is **scanner-only** — the pipeline's submitter slot is left unset,
so approved opportunities land in `dry_run` terminal state.  Attempting to
construct the `SolanaExecutor` raises `NotImplementedError` by design.

## Quick Start

```bash
# 1. install
python3.11 -m pip install -r requirements.txt   # or use pyproject.toml
cp .env.example .env                            # fill in Solana RPC/Jupiter
git submodule update --init                     # trading_platform

# 2. smoke-test (deterministic, offline)
PYTHONPATH=src:lib/trading_platform/src python3.11 -m main \
    --config config/example_config.json --iterations 5 --dry-run

# 3. live Jupiter scan (short run)
PYTHONPATH=src:lib/trading_platform/src python3.11 -m main \
    --config config/example_config.json --jupiter --iterations 10 --dry-run

# 4. production scanner loop (runs until SIGINT)
./scripts/run_local.sh                          # live Jupiter
./scripts/run_local.sh --sim                    # offline simulated

# 5. readiness + tests
PYTHONPATH=src:lib/trading_platform/src python3.11 scripts/check_readiness.py
PYTHONPATH=src:lib/trading_platform/src python3.11 -m pytest tests/ -q
```

## Key Files

| Path | Purpose |
|---|---|
| `src/market/solana_market.py` | Jupiter v6 quote adapter |
| `src/market/sim_market.py` | Deterministic synthetic market (tests + offline) |
| `src/core/tokens.py` | SPL mint registry (SOL, USDC, USDT, mSOL, jitoSOL, bSOL) |
| `src/core/venues.py` | Solana venue registry (Jupiter enabled, AMMs disabled in v1) |
| `src/core/config.py` | `BotConfig` / `VenueConfig` / `PairConfig` loading |
| `src/strategy/arb_strategy.py` | Cross-venue PnL math (fee / slippage / priority-fee) |
| `src/strategy/scanner.py` | Ranking + liquidity gating + scan-history emission |
| `src/risk/policy.py` | Rule-based risk engine (min profit, fee ratio, exposure, flags) |
| `src/pipeline/lifecycle.py` | Candidate pipeline: detect → price → risk → (Phase 3: sim/submit/verify) |
| `src/persistence/db.py` | SQLite / Postgres schema (Solana-native column names) |
| `src/execution/executor.py` | `PaperExecutor` (scanner-phase default) |
| `src/execution/solana_executor.py` | Phase-3 stub — refuses to construct |
| `src/observability/latency_tracker.py` | Per-stage latency → `logs/latency.jsonl` |
| `src/run_event_driven.py` | Production scanner loop (queue + consumer thread) |

## Latency Observability

Every scan and every pipeline execution writes a JSON record to
`logs/latency.jsonl`.  Per-scan fields include `rpc_fetch`, `scanner`.
Per-pipeline fields include `detect_ms`, `price_ms`, `risk_ms`,
`simulate_ms`, `total_ms`.

```bash
# summary report (p50 / p95 / max per stage)
PYTHONPATH=src:lib/trading_platform/src python3.11 -c \
    "from observability.latency_tracker import analyze_latency; analyze_latency()"
```

## Configuration

`config/example_config.json` is the default Phase-1 config:

- Primary pair: `SOL/USDC` — trade 1 SOL at a time
- Extra pair: `USDC/USDT` — trade 200 USDC at a time (max exposure 2,000 USDC)
- Venues: Jupiter-Best (multi-hop) + Jupiter-Direct (single-hop)
- Priority fee: 10,000 lamports (~0.00001 SOL)
- Slippage: 20 bps quoting tolerance
- Min profit: 0.002 SOL (~$0.33 at SOL=$165)

`config/prod_scan.json` raises the min profit, tightens venue liquidity
floor, and increases trade size for a production scanning run.

## Phase Map

- [x] Phase 0/1 — repo conversion + scanner-only Solana ingestion (this commit)
- [ ] Phase 2 — calibrate fee / slippage / liquidity thresholds with real data
- [ ] Phase 3 — `SolanaExecutor` (tx build, preflight sim, submit, verify)
- [ ] Phase 4 — readiness + rehearsal hardening
- [ ] Phase 5 — narrow live rollout (1 pair, 1 venue, capped size)
- [ ] Phase 6 — scale + optimization

See `docs/solana_migration_status.md` for the full phase breakdown.

## Testing

```bash
PYTHONPATH=src:lib/trading_platform/src python3.11 -m pytest tests/ -q
```

Tests cover: SPL token registry, config loading, model coercion, sim
market, Jupiter adapter (mocked), strategy PnL math, scanner ranking,
risk policy, persistence (new Solana schema), pipeline scanner-only flow,
latency tracker, executor stub.

## Safety Posture

Per `CLAUDE.md`:

- **Capital preservation > profit** — no trade is better than a bad trade
- **Default to simulation** — `PaperExecutor` is the only executor that can
  be instantiated in Phase 1
- **Never use float** — all financial math is `Decimal` or integer
- **Never commit secrets, `data/`, or `logs/`**
