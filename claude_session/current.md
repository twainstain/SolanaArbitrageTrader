# Current State

## Session: 2026-04-16

### Summary

SolanaTrader scanner **live in prod** (dry-run, Phase 1). Replaces the EVM
ArbitrageTrader codebase end-to-end. Jupiter + Raydium V4 + Orca
Whirlpool adapters feeding a Solana-native pipeline. Execution stack
(Phase 3) is built but gated off by 7 safety checks.

### Deployed

- **EC2**: `54.163.230.90` (t3.small, us-east-1, Ubuntu 22.04)
- **Dashboard**: https://arb-trader-solana.yeda-ai.com (basic auth)
- **DB**: self-hosted Postgres 17-alpine in docker-compose, `pg-data` named volume
- **Backups**: daily pg_dump → gzip → S3 (`yeda-ai-solana-backups/daily/`) via EC2 IAM role; first backup already uploaded
- **Cron**: 03:00 UTC backup, 03:30 UTC daily analysis report
- **IaC**: IAM role `solana-trader-ec2-role` + instance profile, S3 bucket
  with versioning, BPA, Glacier IR @ 30d / expire @ 180d

### Code on prod

- Repo: `github.com/twainstain/SolanaArbitrageTrader` (master tip `c1f0e77`)
- Bot image: `local/solana-trader:latest` (python:3.11-slim base)
- Submodule: `lib/trading_platform` at `495b4c2`

### What Works

| Feature | Status |
|---|---|
| Scanner loop (~0.75s poll, 3 venues) | Running, bot healthy |
| Jupiter /quote (Best + Direct routes) | Working |
| Raydium V4 getMultipleAccounts reserves + half-fee midpoint | Working |
| Orca Whirlpool sqrt_price decoder + half-fee midpoint | Working |
| Postgres persistence (11-table Solana-native schema) | Working |
| HTTPS dashboard with Let's Encrypt cert | Working |
| S3 backup round-trip (pg_dump + restore script) | Working |
| Alchemy Solana RPC via `SOLANA_RPC_URL` | Working (~50ms p50) |
| Latency tracker (`logs/latency.jsonl`) | Preserved from EVM |

### Not Live

- **Execution** — `SOLANA_EXECUTION_ENABLED` not set; `BOT_DRY_RUN=true`. Scanner
  only; no tx submission. Phase 3 stack passes 93/93 unit tests but is un-rehearsed.
- **Alerting** — Discord / Gmail / Telegram backends present in code, untested,
  no webhooks configured in prod .env.

### Known Gaps

| Gap | Impact | Plan |
|---|---|---|
| No alerting tests | Silent failure risk | Port `test_alerting.py` from EVM next |
| No observability tests beyond latency_tracker | Metrics/log formatting could drift | Port `test_observability.py` + `test_quote_diagnostics.py` |
| `smart_alerts.py` is a no-op shell | No rich summaries | Rewrite for Solana (lamport fees, Solscan links) |
| `install-cron.sh` had pipefail bug | Already fixed locally, needs push | Commit + push |
| Dashboard `/scanner/status`, `/api/metrics` 404 | API route names may differ from EVM | Probe + fix |

### Production commands

```bash
# Status
SOLANA_EC2_HOST=54.163.230.90 SOLANA_EC2_KEY=~/.ssh/arb-trader-solana.pem \
  ./scripts/deploy_prod.sh --status

# Redeploy (after a code change)
SOLANA_EC2_HOST=54.163.230.90 SOLANA_EC2_KEY=~/.ssh/arb-trader-solana.pem \
  ./scripts/deploy_prod.sh

# Logs
SOLANA_EC2_HOST=54.163.230.90 SOLANA_EC2_KEY=~/.ssh/arb-trader-solana.pem \
  ./scripts/deploy_prod.sh --logs
```

### Test Count: 133 (prior suite was 618 EVM-specific; gap is intentional)
