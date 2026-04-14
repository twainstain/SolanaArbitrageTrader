# Current State

## Session: 2026-04-14

### Summary

Production bot deployed on AWS EC2 with 22 DEX quoters implemented, dynamic pair discovery, thin pool detection. 618 tests. Fixed critical fee double-counting bug — on-chain quoters now surface real spreads (25-35 bps on major pairs).

### Deployed

- **EC2**: 18.215.6.141 (spot t3.small)
- **Dashboard**: https://arb-trader.yeda-ai.com/dashboard
- **DB**: Neon Postgres
- **Alerting**: Discord + Gmail

### What Works

| Feature | Status |
|---------|--------|
| Pipeline (detect → price → risk) | Working, ~6s per scan |
| fee_included flag (no double-counting) | Working — real spreads surfacing |
| On-chain quoters return actual fee tier | Working (V3: exact bps, others: estimated) |
| Multi-pair scanning (WETH/USDC, WETH/USDT, OP/USDC) | Working |
| Dashboard cost waterfall breakdown | Working |
| Thin pool filter (5% global median) | Working (but misses ~4.8% outliers) |
| Liquidity cache (3h/15min TTL) | Working |
| Auto pair discovery (DexScreener hourly) | Working |
| scripts/run_local.sh (local dev runner) | Working |

### Latest Scan Results (onchain mode)

| Chain | Pair | Spread | Status |
|-------|------|--------|--------|
| Ethereum | WETH/USDT | ~32 bps | Real, consistent |
| Ethereum | WETH/USDC | ~19-28 bps | Real, consistent |
| Base | WETH/USDC | ~23-29 bps | Real, consistent |
| Arbitrum | WETH/USDC | ~12-13 bps | Real, consistent |
| Arbitrum | WETH/USDT | ~480 bps | FALSE POSITIVE — Sushi stale pool |

### What Needs Fixing

| Issue | Root Cause | Fix |
|-------|-----------|-----|
| **Sushi-Arbitrum WETH/USDT outlier** | Returns $2231 (stale pool), 4.8% deviation slips under 5% filter | Tighten outlier filter or cross-validate against same-DEX other pairs |
| **Optimism all DEXes returning zero** | Uniswap, Sushi, Velodrome all fail on Optimism | Debug token addresses / quoter contracts |
| **Base USDT not in token registry** | Missing USDT address for Base chain | Add to tokens.py |
| **OP/USDC only works on Optimism** | Other chains can't resolve OP token | Expected — OP is Optimism-native |
| **CI/CD deploy fails** | AWS credentials not set in GitHub secrets | Set AWS_ACCESS_KEY_ID/SECRET in repo settings |

### Test Count: 618
