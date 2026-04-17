# Current State

## Session: 2026-04-16

### Summary

SolanaTrader scanner live in prod (dry-run, Phase 1) with HTTPS dashboard,
Postgres 17, daily S3 backups, Discord + Gmail alerting verified. Replaces
the EVM ArbitrageTrader. Jupiter + Raydium V4 + Orca Whirlpool feed a
Solana-native pipeline; execution stack is built but gated off by 7 checks.

### Deployed

- **EC2**: `54.163.230.90` (t3.small, Ubuntu 22.04, us-east-1)
- **Dashboard**: https://arb-trader-solana.yeda-ai.com (Let's Encrypt cert)
- **DB**: Postgres 17-alpine container, `pg-data` named volume
- **Backups**: daily `pg_dump | gzip` → `s3://yeda-ai-solana-backups/daily/` via EC2 IAM role; one verified round-trip
- **Cron** (ubuntu user): 03:00 UTC backup, 03:30 UTC daily_analysis.py
- **Alerting**: Discord webhook + Gmail SMTP configured; Telegram left off. Test alert verified end-to-end through `scripts/test_alerts.py`.

### Code on prod

- Repo: `github.com/twainstain/SolanaArbitrageTrader` (master `d3276dd` at last push)
- Bot image: `local/solana-trader:latest` (python:3.11-slim)
- Submodule: `lib/trading_platform` at `495b4c2`

### Test count: 180 (133 migration baseline + 47 new alerting/diagnostics)

### API routes (all GET unless noted; all behind basic auth)

```
/ /dashboard /scanner /ops /analytics /execution /opportunity/{opp_id}
/health /metrics /funnel /scan-history /pnl /pnl/analytics
/opportunities /opportunities/{opp_id}/full
/wallet/balance /risk/policy
POST /scanner/{pause,resume}
POST /execution/{kill,resume}
POST /pairs/{pair:path}/{disable,enable}
POST /venues/{venue:path}/{disable,enable}
```

### Ops one-liners (all default to the DNS host + `~/.ssh/arb-trader-solana.pem`)

```bash
./scripts/deploy_prod.sh                 # rsync + build + restart
./scripts/deploy_prod.sh --status        # health + wallet + container ps
./scripts/deploy_prod.sh --logs          # follow bot logs
./scripts/deploy_prod.sh --sync-env      # upload local .env (chmod 600 remote)
./scripts/deploy_prod.sh --migrate       # run migrate_db.py in the bot image
./scripts/deploy_prod.sh --restart       # recreate bot container (picks up .env)
./scripts/deploy_prod.sh --test-alerts   # fire test alert through all backends
./scripts/deploy_prod.sh --db            # interactive psql (ctrl-D to exit)
./scripts/deploy_prod.sh --scan-stats    # funnel + per-pair spreads + filter reasons
```

### Scanner state (snapshot)

| Metric | Value |
|---|---|
| Total scans since start | 3,170 (growing) |
| Opportunities detected | 0 |
| Approved / Trades | 0 / 0 |
| Pairs seen | SOL/USDC, USDC/USDT |
| Sole filter reason | `unprofitable` (fee-adjusted spread < min_profit_base) |

Expected for Phase 2c: SOL/USDC's Raydium pool fee is 25 bps, so the
fee-adjusted mid-price gap to Jupiter is ~flat — net profit under
threshold. Real arb requires LST pairs or brief volatility spikes
(Phase 2d).

### Not live

- Execution — `SOLANA_EXECUTION_ENABLED` not set, `BOT_DRY_RUN=true`. Scanner only.
- `smart_alerts.py` remains a no-op shell (Phase 4).
- Telegram backend — credentials not in `.env`.

### Known residuals

- `src/observability/log_parser.py` (280 LOC) references pre-Solana event
  shapes. Not imported by active code; keep-or-delete TBD.
- `perf_tracker.py` (280 LOC) untested. `log.py`, `metrics.py`, `time_windows.py`,
  `observability/wallet.py` untested.
- `docker-compose.yml` no longer has obsolete `version:` key.
- `scripts/install-cron.sh` now survives an empty crontab (`set -eo pipefail`
  interaction with `crontab -l` fixed).
- `src/alerting/dispatcher.py` DEFAULT_DASHBOARD_URL corrected from
  `solana-arb-trader.yeda-ai.com` (404) to `arb-trader-solana.yeda-ai.com`.

### AWS identities

- IAM role + instance profile: `solana-trader-ec2-role`
- Role policy: `SolanaTraderS3Backup` (PutObject/GetObject/ListBucket on
  `yeda-ai-solana-backups/*`)
- S3 bucket: versioning on, BPA on, Glacier IR @ 30d, expire @ 180d
- SSH keypair: `arb-trader-solana` (pem at `~/.ssh/arb-trader-solana.pem`, chmod 400)
- Security group: `solana-trader-sg` (`sg-0f9f8b883fdeaf566`) — 22/80/443 open
- Elastic IP: `54.163.230.90` (`eipalloc-0f2a693c35e518b8c`)

### Dashboard creds

- User: `admin`
- Pass: stored in `.env` `DASHBOARD_PASS=` on the EC2 host and on the
  local laptop under `/Users/tamir.wainstain/src/SolanaArbitrageTrader/.env`.
