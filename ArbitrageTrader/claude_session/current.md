# Current State

## Session: 2026-04-13

### Summary

Production-grade DEX arbitrage trading system. 422 tests. 6 EVM chains live.

### Live On-Chain Scanning — 6 Chains

| Chain | DEXs | Status |
|---|---|---|
| Ethereum | Uniswap V3 + SushiSwap V3 | Working |
| Arbitrum | Uniswap V3 + SushiSwap V3 | Working |
| Base | Uniswap V3 + SushiSwap V3 | Working |
| Polygon | Uniswap V3 (Sushi filtered) | Working |
| Optimism | Uniswap V3 + SushiSwap V3 | Working — best spreads (~11%) |
| Avalanche | Uniswap V3 (Sushi not deployed) | Working |

### Architecture Modules

| Module | Status |
|---|---|
| On-chain quoters (multi-fee-tier, per-chain) | Done |
| Persistence (SQLite + Postgres) | Done |
| Risk engine (9 rules + circuit breaker) | Done |
| Candidate pipeline (6-stage lifecycle) | Done |
| API control plane (FastAPI + auth) | Done |
| Dashboard (HTML, chain filter, bar chart, detail pages) | Done |
| Alerting (Telegram >5%, Discord, Gmail hourly) | Done |
| RPC failover (3 backup endpoints per chain) | Done |
| Pair discovery (DexScreener volume ranking) | Done |
| Outlier filter, Decimal math, Flashbots | Done |

### Not Yet Working

- BSC: needs WBNB/USDT pair (not WETH/USDC)
- Mantle: no Uniswap V3 deployment
- Polygon Sushi: returns $0.74 (filtered by sanity check)
- Avalanche Sushi: quoter address not deployed

### Test Count: 422
