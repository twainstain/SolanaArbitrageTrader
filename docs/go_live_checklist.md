# Going Live — Execution Checklist

Last updated: 2026-04-15

This document covers everything needed to move from simulation mode to live execution. The bot is designed to run safely in simulation indefinitely — only follow this checklist when you're ready to commit capital.

---

## Current Status

| Chain | Contract | Wallet | Readiness | Config |
|-------|----------|--------|-----------|--------|
| Arbitrum | `0x95AFF4...8115` | 0.010 ETH | GREEN | `config/arbitrum_live_execution_config.json` |
| Optimism | `0x95AFF4...8115` | 0.005 ETH | GREEN | `config/optimism_live_execution_config.json` |
| Ethereum | Not deployed | 0.011 ETH | NOT READY | — |
| Base | Not deployed | 0.000005 ETH | NOT READY (underfunded) | — |

Supported swap interfaces:
- **V3 routers**: Uniswap V3, Sushi V3, PancakeSwap V3
- **Solidly-fork routers**: Velodrome V2 (Optimism), Aerodrome (Base)

Go-live verdict as of 2026-04-15:
- Arbitrum config readiness: GREEN
- Optimism config readiness: GREEN
- Overall production readiness: READY FOR NARROW ARBITRUM ROLLOUT, NOT YET FULLY PROVEN

Code review fixes completed:
- multichain pair persistence now keys `pairs` by `(pair, chain)` with migration support for SQLite and PostgreSQL/Neon
- execution persistence now stores the real submission path
- readiness / verification tooling now runs cleanly end-to-end

Remaining rollout risks:
- no real live trades have been observed yet
- Optimism route quality is still affected by the Velodrome outlier problem

---

## Prerequisites

Before enabling live execution, confirm:

- [x] Bot running in simulation with consistent opportunity detection
- [x] Dashboard shows realistic spreads (not false positives)
- [x] DEX Health panel shows 80%+ success rate on target chains
- [x] Outlier filter removing stale/thin pools
- [x] Alerting configured (Discord + Gmail)
- [x] Fork rehearsal passed (`scripts/fork_rehearsal.py --auto-anvil`)
- [x] Launch readiness check passes for Arbitrum and Optimism (`scripts/check_readiness.py`)
- [x] `PYTHONPATH=src /usr/local/bin/python3.11 -m pytest tests/ -q` passes
- [x] Full `PYTHONPATH=src /usr/local/bin/python3.11 -m pytest -q` passes
- [x] Multichain pair identity fixed in persistence and covered by regression tests
- [x] Execution metadata stores real submission path (`public` vs `flashbots`)
- [ ] At least one narrow-chain live rehearsal with actual execution enabled has been completed and reviewed

---

## Step 1: Fund Wallet

Bot wallet: `0xcfF46971b1BA42d74C4c51ec850c7F33f903EAeB`

Minimum gas needed per chain:

| Chain | Min Gas | Recommended | Deploy Cost | Per-Trade Gas |
|-------|---------|-------------|-------------|---------------|
| Arbitrum | 0.003 ETH | 0.01 ETH | ~0.00004 ETH | ~0.0003 ETH |
| Optimism | 0.002 ETH | 0.005 ETH | ~0.000003 ETH | ~0.0001 ETH |
| Base | 0.002 ETH | 0.005 ETH | ~0.00005 ETH | ~0.0002 ETH |
| Ethereum | 0.05 ETH | 0.1 ETH | ~0.01 ETH | ~0.005 ETH |

Bridge options: https://superbridge.app or https://bridge.arbitrum.io

```bash
# Check balances
PYTHONPATH=src python -c "
from api.app import create_app
# Or just hit the running instance:
# curl -u admin:test http://localhost:8000/wallet/balance
"
```

---

## Step 2: Deploy Contract

Already deployed on Arbitrum + Optimism. For new chains:

```bash
cd contracts/

# Deploy via forge script (forge create --broadcast has a bug in v1.5.1)
source ../.env
AAVE_POOL=<aave_pool_address> forge script script/Deploy.s.sol:DeployFlashArb \
  --rpc-url $RPC_<CHAIN> --broadcast -vvv
```

Aave V3 Pool addresses:
- Arbitrum: `0x794a61358D6845594F94dc1DB02A252b5b4814aD`
- Optimism: `0x794a61358D6845594F94dc1DB02A252b5b4814aD`
- Base: `0xA238Dd80C259a72e81d7e4664a9801593F98d1c5`
- Ethereum: `0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2`

Save the address:
```bash
# Add to .env
EXECUTOR_CONTRACT=0x<deployed_address>
```

---

## Step 3: Run Readiness Check

```bash
# For Arbitrum
PYTHONPATH=src python scripts/check_readiness.py --config config/arbitrum_live_execution_config.json

# For Optimism
PYTHONPATH=src python scripts/check_readiness.py --config config/optimism_live_execution_config.json

# Check a running instance via API
PYTHONPATH=src python scripts/check_readiness.py --api http://localhost:8000
```

All must show `Launch Readiness: READY`.

Important:
- This verifies config/env readiness for a single target chain.
- It does **not** prove route quality or profitable execution by itself.
- Do not treat a green readiness result as a final go-live signoff by itself.

---

## Step 4: Fork Rehearsal (Optional but Recommended)

Test the full execution path without spending real gas:

```bash
PYTHONPATH=src python scripts/fork_rehearsal.py --auto-anvil
```

This forks Arbitrum via anvil and runs: build tx → simulate → sign → submit → receipt → verify. All 7 checks should pass.

---

## Step 5: Start the Bot

```bash
# Local (recommended for first go-live)
./scripts/run_local.sh

# Or with a specific config
PYTHONPATH=src python -m run_event_driven --config config/arbitrum_live_execution_config.json
```

Dashboard: http://localhost:8000/dashboard (admin / test)
Analytics: http://localhost:8000/analytics
Ops: http://localhost:8000/ops

---

## Step 6: Enable Live Execution

**This commits real capital (gas). Flash loan principal is never at risk.**

For current rollout guidance:
- Arbitrum: acceptable for a narrow first rollout
- Optimism: keep cautious until the Velodrome/outlier behavior is reviewed under live conditions

```bash
# Via API (runtime, no restart)
curl -u admin:$DASHBOARD_PASS -X POST \
    http://localhost:8000/execution \
    -H "Content-Type: application/json" \
    -d '{"enabled": true}'
```

The API will refuse if launch readiness is not green.

---

## Step 7: Monitor

### Key Dashboards

| Page | URL | What to Watch |
|------|-----|---------------|
| Dashboard | `/dashboard` | Opportunities, status, wallet balance |
| Analytics | `/analytics` | PnL breakdown, win rate, spread capture |
| Ops | `/ops` | DEX health, RPC status, risk policy |

### Key Metrics

| Metric | Target | Action if Off |
|--------|--------|---------------|
| Win rate | >50% | Check if spreads close before execution |
| Revert rate | <5% | Increase `min_profit_base` |
| Spread capture | >60% | Reduce latency, check slippage |
| Gas cost / profit | <20% | Focus on L2 chains |

### Analytics Filters

The `/analytics` page supports:
- **Chain filter**: analyze one chain at a time
- **Time window**: 1h, 4h, 8h, 24h, 3d, 1w, 1m
- **Date range**: from/to date pickers
- **Apply button**: refreshes all sections with filters

### What the Analytics Shows

| Section | Purpose |
|---------|---------|
| Summary cards | Total trades, win rate, net profit, spread capture % |
| Hourly PnL | Visual trend — are we profitable over time? |
| Profit by Pair | Which pairs are making/losing money? |
| Profit by Venue Route | Which buy→sell DEX combinations work best? |
| Expected vs Realized | Are we capturing the predicted spread? |
| Gas Efficiency | Is estimated gas accurate? Over/underpaying? |
| Rejection Reasons | Why are opportunities being filtered? Should we tune thresholds? |

---

## Step 8: Tune

After the first 24 hours of live execution:

1. **Check `/analytics` rejection reasons** — if `spread_too_low` dominates and avg expected profit is close to threshold, consider lowering `min_profit_base`
2. **Check spread capture** — if <50%, spreads are closing before execution; need faster scanning or tighter poll interval
3. **Check per-venue win rate** — if one route always loses, consider removing that DEX from the config
4. **Check gas efficiency** — if actual gas >> estimated, update `estimated_gas_cost_base` in config
5. **Lower `min_profit_base` gradually**: 0.005 → 0.003 → 0.001 to capture more opportunities

---

## Rollback

To disable execution immediately:

```bash
# Via API (instant)
curl -u admin:$DASHBOARD_PASS -X POST \
    http://localhost:8000/execution \
    -d '{"enabled": false}'

# Or kill the bot
./scripts/run_local.sh --stop
```

The contract remains deployed but idle. No funds are at risk when execution is disabled.

---

## Cost Summary

| Item | Cost | Frequency |
|------|------|-----------|
| Contract deployment (L2) | ~$0.01-0.10 | One-time per chain |
| Contract deployment (Ethereum) | ~$5-20 | One-time |
| Failed trade (revert) | Gas only (~$0.01-0.10 on L2) | Per revert |
| Successful trade | Gas + 9 bps flash loan fee | Per trade |
| Bot infrastructure (EC2) | ~$15/month | Ongoing |

No capital is locked. Flash loans provide trading capital — you only need gas money.

---

## Emergency Contacts

| Issue | Action |
|-------|--------|
| Bot keeps reverting | Disable execution via API, check `/analytics` revert reasons |
| RPC errors | Check `/ops` RPC health, switch provider (see `docs/rpc_providers.md`) |
| Wallet drained | Impossible — contract only sends profit to owner, flash loan reverts protect principal |
| Contract stuck tokens | Call `withdrawToken(address)` from owner wallet |
| Need to update contract | Deploy new version, update `EXECUTOR_CONTRACT` in `.env` |

---

## Final Recommendation

Are we ready to go live?

For a narrow Arbitrum-first rollout: yes.

For a broader “fully proven production system” answer: not yet.

What is ready:
- core execution path exists
- Arbitrum and Optimism readiness checks are green
- tests are strong and currently clean
- dashboards, persistence, and fork rehearsal are in place
- multichain persistence and submission-type issues from review are fixed

What still limits confidence:
- there is still no real live execution history to validate end-to-end PnL behavior
- Optimism route quality still needs confirmation because Velodrome quotes can be filtered as outliers

Recommended rollout now:
1. Enable live execution on Arbitrum only, with small size and close monitoring.
2. Review first included and reverted trades.
3. Tune thresholds from real analytics data.
4. Only then expand to Optimism if route quality looks real.
