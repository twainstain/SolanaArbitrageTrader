# SolanaTrader — Deploy Guide (Phase 1 scanner)

Last updated: 2026-04-16

Target shape:

```
GoDaddy DNS        arb-trader-solana.yeda-ai.com  →  EC2 elastic IP
     │
     ▼
  nginx (Let's Encrypt)  :443 → bot :8000
     │
     ▼
  bot container    ─────┐
     │                  │
     ▼                  │        (internal docker network)
  Postgres 16           │
     │                  │
     ▼                  │
  pg_dump → S3 (daily, cron 03:00 UTC)
                        │
                        ▼ external
         Alchemy Solana RPC (your key)
         Jupiter API (lite-api.jup.ag free tier)
```

## Prerequisites

You create these manually (I don't have cloud credentials):

- [ ] GitHub repo `SolanaArbitrageTrader` (empty)
- [ ] AWS key pair `arb-trader-solana` — save the `.pem` locally at `~/.ssh/arb-trader-solana.pem` (chmod 400)
- [ ] EC2 `t3.medium` running Ubuntu 22.04+, 20 GB root EBS, Docker + awscli installed, sg open on 22/80/443
- [ ] Elastic IP attached to the EC2
- [ ] **EC2 IAM role** with `s3:PutObject` + `s3:ListBucket` + `s3:DeleteObject` on the backup bucket
- [ ] **S3 bucket** e.g. `yeda-ai-solana-backups` (any region, versioning optional)
- [ ] GoDaddy A record: `arb-trader-solana` → the elastic IP
- [ ] Wallet keypair (`solana-keygen new -o wallet.json`) — chmod 600

## 1. Push code

```bash
# From this repo, on the solana-v1 branch:
git remote add solana https://github.com/<you>/SolanaArbitrageTrader.git
git push solana solana-v1:main
```

## 2. Bootstrap the EC2 (one-time)

SSH in, then:

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin git
sudo usermod -aG docker ubuntu
# log out + back in so the group takes effect

sudo mkdir -p /opt/solana-trader && sudo chown ubuntu:ubuntu /opt/solana-trader
cd /opt/solana-trader
git clone https://github.com/<you>/SolanaArbitrageTrader.git .
git checkout main
git submodule update --init   # pulls lib/trading_platform
```

## 3. Create `.env` on the server

On the EC2, create `/opt/solana-trader/.env` with:

```bash
BOT_CONFIG=config/prod_scan.json
BOT_MODE=jupiter
BOT_DRY_RUN=true                   # until Phase 5 sign-off
DASHBOARD_USER=admin
DASHBOARD_PASS='<pick a strong one>'

SOLANA_RPC_URL=https://solana-mainnet.g.alchemy.com/v2/<key>
JUPITER_API_URL=https://lite-api.jup.ag/swap/v1

# Postgres (Docker-internal — the hostname "postgres" resolves on the sol-net bridge)
POSTGRES_DB=solana_arb
POSTGRES_USER=solana
POSTGRES_PASSWORD=<generate with: openssl rand -base64 24>
DATABASE_URL=postgresql://solana:<same-password>@postgres:5432/solana_arb

# S3 backups (uses EC2 IAM role, no creds in .env)
BACKUP_S3_BUCKET=yeda-ai-solana-backups
BACKUP_S3_PREFIX=daily
BACKUP_RETENTION_DAYS=30

# Gmail for the daily analysis report (optional)
GMAIL_ADDRESS=twainstain.trader@gmail.com
GMAIL_APP_PASSWORD="..."
GMAIL_RECIPIENT=twainstain.trader@gmail.com

# Phase 3+ only — leave commented until you're ready:
# SOLANA_EXECUTION_ENABLED=true
# SOLANA_WALLET_KEYPAIR_PATH=/opt/solana-trader/wallet.json
```

`chmod 600 .env`.

## 4. Bring up Postgres + run schema migration

```bash
# Start only postgres first so migrate_db can connect
docker compose up -d postgres
# Wait for healthcheck
until docker compose ps postgres | grep -q healthy; do sleep 2; done

# Run schema against the new DB
docker compose run --rm bot python3 scripts/migrate_db.py
# Expected output: "Tables present (12):" and a list.
```

## 5. Smoke-test RPC + Jupiter

```bash
docker compose run --rm bot python3 scripts/test_rpc.py
# Expected: both OK.
```

## 6. Start the scanner

```bash
./scripts/start.sh
# Verify
./scripts/deploy_prod.sh --status
# Stream logs
./scripts/deploy_prod.sh --logs
```

The dashboard is now on `http://<ec2-ip>:8000` with HTTP basic auth.

## 7. Nginx + Let's Encrypt (optional but recommended)

```bash
# Issue cert
sudo certbot certonly --standalone -d arb-trader-solana.yeda-ai.com
# Wire the cert paths into monitoring/nginx.conf then:
docker compose --profile proxy up -d nginx
# Point GoDaddy A record at the elastic IP — done.
```

## 8. Verify public URL

```bash
curl -u admin:<pass> https://arb-trader-solana.yeda-ai.com/health
# {"status": "ok", "service": "solana-trader"}
```

## 9. Install daily cron (backup + analysis)

```bash
# Installs:
#   03:00 UTC — pg_dump → S3 via backup_to_s3.sh
#   03:30 UTC — daily_analysis.py (writes data/reports/*.html, emails via Gmail)
./scripts/install-cron.sh
crontab -l                 # verify
```

Test both right away without waiting for 03:00:
```bash
./scripts/backup_to_s3.sh        # should upload one object to s3://<bucket>/daily/
./scripts/daily_analysis.py       # writes today's report + emails
aws s3 ls s3://$BACKUP_S3_BUCKET/daily/
```

## Ongoing ops

```bash
./scripts/deploy_prod.sh            # pull + rebuild + restart after a code push
./scripts/deploy_prod.sh --status   # health + wallet + container state
./scripts/deploy_prod.sh --logs     # tail logs
./scripts/deploy_prod.sh --sync-env # upload local .env to EC2
./scripts/deploy_prod.sh --migrate  # re-run schema migrations
```

## Safety

- `BOT_DRY_RUN=true` keeps PaperExecutor active — no on-chain submission possible
- `SOLANA_EXECUTION_ENABLED` must be `true` AND `SOLANA_WALLET_KEYPAIR_PATH` must point at a 0600 file before any live tx; even then `--execute-live` is the third gate
- Kill switch: `curl -u admin:… -X POST https://arb-trader-solana.yeda-ai.com/execution/kill` (instant stop)
- Scanner pause: `POST /scanner/pause` (soft stop; in-flight scans complete)
- Per-pair disable: `POST /pairs/SOL/USDC/disable`

## Rolling back

```bash
# On EC2:
cd /opt/solana-trader
git log --oneline -5
git checkout <earlier-sha>
./scripts/start.sh
```

## Known limitations (Phase 1)

- No Phase 3b atomic two-leg execution yet — Phase 3 v1 does single-leg only
- No Jito bundle submitter — RPC-only
- Scanner-phase DB is safe to wipe; when execution is enabled, trade_results becomes the source of truth
