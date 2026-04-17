# Current State

## Session: 2026-04-17 (autonomous resume)

### Summary

Phases 2d, 3b, 4 completed in a 31-minute autonomous run. Scanner remains
live in dry-run; execution stack is built and tested but gated off by
the 7 safety checks. Test suite went **204 → 263 (+59)**. Last deployed
SHA `1770ea2`. See `claude_session/autonomous_report.md` for the per-
phase breakdown and decisions made on the user's behalf.

### Deployed

- **EC2**: `54.163.230.90` (t3.small, Ubuntu 22.04, us-east-1)
- **Dashboard**: https://arb-trader-solana.yeda-ai.com (Let's Encrypt cert)
- **DB**: Postgres 17-alpine container, `pg-data` named volume
- **Backups**: daily pg_dump → S3 `yeda-ai-solana-backups/daily/` via EC2 IAM role
- **Cron** (ubuntu user): 03:00 UTC backup, 03:30 UTC daily_analysis.py
- **Alerting**: Discord webhook + Gmail SMTP configured + verified. Telegram off.
  Phase 4 added hooks for trade_reverted / trade_dropped.

### Code on prod

- Repo: `github.com/twainstain/SolanaArbitrageTrader` (master `1770ea2`)
- Bot image: `local/solana-trader:latest` (python:3.11-slim)
- Submodule: `lib/trading_platform` at `495b4c2`

### Test count: **263**

Growth:
- 133 baseline after migration
- +47 alerting + quote_diagnostics
- +24 metrics + time_windows + opportunity_detail (fixed 2 prod bugs)
- +30 Phase 2d (Orca LST pools, Raydium CPMM, AdaptivePoll, Jito stub)
- +7 SolanaMarket cooldown + rotation (Jupiter rate-limit fix)
- +15 Phase 3b (atomic swap + verifier balance deltas)
- +10 Phase 4 (rehearsal wiring is script-only; ops fee card + alert hooks covered)

### What works

| Layer | Scope |
|---|---|
| Scanner loop | live, ~1 scan/sec, SOL/USDC + USDC/USDT evaluating; LST pairs register but need 2+ venues to surface opps |
| Multi-venue market | Jupiter (rotating pairs) + Raydium AMM V4 + Orca Whirlpool (incl. 3 LST pools) |
| Adaptive poll | downshifts 0.75s → 0.25s on near-hit; feature ON via `prod_scan.json` |
| Atomic swap builder | `plan_two_leg` + `build_atomic_tx` (Jupiter→Jupiter), tests mock Jupiter + compile MessageV0 |
| Realized-profit verifier | parses SOL native fee add-back + SPL balance deltas; populates `actual_profit_base` |
| Ops `/ops` page | 3 new cards live: Wallet SOL, Fees 24h, Kill switch |
| Rehearsal `scripts/rehearsal.py` | scan-smoke verified; full flow requires RPC + ALT fetch |
| Alerting | Discord + Gmail verified; revert/drop hooks wired in pipeline |

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
PYTHONPATH=src python3 scripts/rehearsal.py --skip-tx   # scan-only smoke
PYTHONPATH=src python3 scripts/rehearsal.py             # full wiring rehearsal (dry-run)
```

### Open blockers before Phase 5

1. **Jupiter paid tier (or Helius)** — free tier caps us at 4 req/s. With 5 pairs we need rotation; with a paid tier we can scan all 5 every tick. Upgrade + set `SolanaMarket._max_pairs_per_scan = 0` to disable rotation.
2. **ALT fetcher for atomic swap** — `rehearsal.py` passes an empty ALT list; any real Jupiter swap that routes through an ALT will fail to compile. Wire `SolanaRPC.get_address_lookup_tables(keys)` to decode `AddressLookupTable` accounts from `getMultipleAccounts` output.
3. **Wallet keypair** — `SOLANA_WALLET_KEYPAIR_PATH` is unset. Required before `--execute-live` can clear gate #2.
4. **Jito credentials** — for competitive landing of atomic swaps. Optional; Phase 3b's default plain-RPC path works without.

### AWS identities (unchanged)

- IAM role + instance profile: `solana-trader-ec2-role`
- Role policy: `SolanaTraderS3Backup`
- S3 bucket: `yeda-ai-solana-backups` (versioning, BPA, Glacier IR @ 30d, expire @ 180d)
- SSH keypair: `arb-trader-solana` (pem at `~/.ssh/arb-trader-solana.pem`, chmod 400)
- Security group: `sg-0f9f8b883fdeaf566` — 22/80/443 open
- Elastic IP: `54.163.230.90`

### Dashboard creds

- User: `admin`
- Pass: `.env` `DASHBOARD_PASS` (same value in local `.env`)

### Known residuals

- `src/observability/log_parser.py` / `perf_tracker.py` / `data/liquidity_cache.py` / `data/rpc_failover.py` — dead code on Solana per earlier analysis; kept per user ask.
- Jupiter rate-limit: 0-1 pairs per scan during peak contention. Mitigated by rotation + cooldown, not eliminated.
