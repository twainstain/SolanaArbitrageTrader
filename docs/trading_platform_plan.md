# Trading Platform — Shared Codebase Design

> Extract reusable infrastructure from ArbitrageTrader into a shared
> platform library (`trading-platform`) that both ArbitrageTrader and
> PolymarketTrader import as a dependency.

---

## Why

ArbitrageTrader and PolymarketTrader share ~60% of their code: pipeline
lifecycle, risk evaluation, circuit breaker, alerting, dashboards,
observability, persistence. Duplicating this across two repos means:
- Bug fixes applied twice (or forgotten in one)
- Design drift between products
- No shared learnings from operational experience

A shared platform means: fix once, test once, deploy everywhere.

---

## Repository Structure

```
github.com/twainstain/
├── trading-platform/            # Shared library (pip-installable)
├── ArbitrageTrader/             # DEX arbitrage product
└── PolymarketTrader/            # Prediction market product
```

### trading-platform (the shared library)

```
trading-platform/
├── pyproject.toml               # Package config, dependencies
├── src/
│   └── trading_platform/        # Import as: from trading_platform.pipeline import ...
│       │
│       ├── pipeline/            # Core pipeline pattern
│       │   ├── lifecycle.py     # 6-stage pipeline (abstract stages 4-6)
│       │   ├── queue.py         # Heapq-based priority queue
│       │   └── models.py       # PipelineResult, QueuedCandidate
│       │
│       ├── risk/                # Risk evaluation framework
│       │   ├── policy.py        # RuleBasedPolicy (abstract rules, per-chain thresholds)
│       │   ├── circuit_breaker.py  # State machine (CLOSED/OPEN/HALF_OPEN)
│       │   └── retry.py         # Bounded retry with re-evaluation
│       │
│       ├── alerting/            # Notification backends
│       │   ├── dispatcher.py    # Fan-out to multiple backends
│       │   ├── gmail.py         # SMTP email
│       │   ├── telegram.py      # Telegram Bot API
│       │   ├── discord.py       # Discord webhooks
│       │   └── smart_alerts.py  # Hourly/daily reports, threshold alerts
│       │
│       ├── observability/       # Monitoring and logging
│       │   ├── metrics.py       # Thread-safe counters, percentiles
│       │   ├── latency_tracker.py  # Per-stage timing (JSONL output)
│       │   ├── log.py           # Centralized logging setup
│       │   └── time_windows.py  # Windowed aggregations (5m, 1h, 24h, ...)
│       │
│       ├── persistence/         # Database layer
│       │   ├── db.py            # SQLite + Postgres connection (DbConnection)
│       │   └── base_repository.py  # Base CRUD patterns (create, update status, aggregate)
│       │
│       ├── api/                 # Web framework
│       │   ├── base_app.py      # FastAPI factory with auth, health, pause, scanner control
│       │   └── design_system.py # Revolut design tokens (colors, CSS, HTML helpers)
│       │
│       ├── config/              # Configuration framework
│       │   ├── base_config.py   # Dataclass loader from JSON + env
│       │   └── env.py           # .env file loading
│       │
│       └── data/                # Shared utilities
│           ├── cache.py         # TTL cache (LiquidityCache pattern)
│           └── rpc_failover.py  # Multi-endpoint failover
│
└── tests/
    ├── test_pipeline.py
    ├── test_risk.py
    ├── test_alerting.py
    ├── test_observability.py
    └── test_persistence.py
```

### ArbitrageTrader (product — imports trading-platform)

```
ArbitrageTrader/
├── pyproject.toml               # depends on: trading-platform
├── src/
│   ├── main.py
│   ├── run_event_driven.py
│   │
│   ├── strategy/                # PRODUCT-SPECIFIC — arb detection
│   │   ├── scanner.py           # Cross-DEX opportunity detection
│   │   └── arb_strategy.py      # Cost model, net profit calculation
│   │
│   ├── execution/               # PRODUCT-SPECIFIC — flash loan execution
│   │   ├── chain_executor.py    # Tx build, sign, Flashbots submission
│   │   └── verifier.py          # On-chain receipt verification
│   │
│   ├── market/                  # PRODUCT-SPECIFIC — DEX quote sources
│   │   ├── onchain_market.py    # RPC quote fetching
│   │   └── live_market.py       # DeFi Llama prices
│   │
│   ├── registry/                # PRODUCT-SPECIFIC — pool/pair discovery
│   │   ├── discovery.py
│   │   ├── pool_discovery.py
│   │   └── pair_refresher.py
│   │
│   ├── core/                    # PRODUCT-SPECIFIC — arb domain models
│   │   ├── contracts.py         # ABIs, addresses, RPC URLs
│   │   ├── tokens.py            # Token registry
│   │   └── models.py            # Opportunity, MarketQuote (extends platform models)
│   │
│   └── dashboards/              # PRODUCT-SPECIFIC — arb dashboard views
│       ├── main_dashboard.py
│       ├── ops_dashboard.py
│       └── analytics_dashboard.py
│
├── config/
└── tests/
```

### PolymarketTrader (product — imports trading-platform)

```
PolymarketTrader/
├── pyproject.toml               # depends on: trading-platform
├── src/
│   ├── main.py
│   │
│   ├── feeds/                   # PRODUCT-SPECIFIC — real-time data
│   │   ├── binance.py           # WebSocket spot ticks
│   │   └── polymarket.py        # WebSocket order book
│   │
│   ├── strategy/                # PRODUCT-SPECIFIC — latency arb
│   │   ├── latency_arb.py       # Signal detection
│   │   └── probability_model.py # Fair value estimation
│   │
│   ├── execution/               # PRODUCT-SPECIFIC — CLOB orders
│   │   ├── clob_executor.py     # py-clob-client wrapper
│   │   └── position_manager.py  # Track open positions
│   │
│   ├── core/                    # PRODUCT-SPECIFIC — PM domain models
│   │   └── models.py            # Signal, Position, Market
│   │
│   └── dashboards/              # PRODUCT-SPECIFIC — PM dashboard views
│       └── pm_dashboard.py
│
├── config/
└── tests/
```

---

## What Moves to the Platform

### Module-by-module breakdown

| Current location | Platform location | Lines | Changes needed |
|-----------------|-------------------|-------|----------------|
| `pipeline/lifecycle.py` | `trading_platform/pipeline/lifecycle.py` | 379 | Make stages 4-6 pluggable via Protocol classes (already done) |
| `pipeline/queue.py` | `trading_platform/pipeline/queue.py` | 168 | Replace `Opportunity` import with generic type |
| `risk/policy.py` | `trading_platform/risk/policy.py` | 287 | Extract base `RuleBasedPolicy` class; product-specific rules stay in products |
| `risk/circuit_breaker.py` | `trading_platform/risk/circuit_breaker.py` | 217 | Fully generic already |
| `risk/retry.py` | `trading_platform/risk/retry.py` | 97 | Fully generic already |
| `alerting/*.py` | `trading_platform/alerting/*.py` | 961 | Extract report content generation (product-specific) from sending (generic) |
| `observability/metrics.py` | `trading_platform/observability/metrics.py` | 148 | Replace arb-specific counter names with configurable ones |
| `observability/latency_tracker.py` | `trading_platform/observability/latency_tracker.py` | 248 | Fully generic already |
| `observability/log.py` | `trading_platform/observability/log.py` | 246 | Fully generic already |
| `observability/time_windows.py` | `trading_platform/observability/time_windows.py` | 254 | Generalize SQL queries to use configurable table names |
| `persistence/db.py` | `trading_platform/persistence/db.py` | 500 | Extract base schema; product-specific tables stay in products |
| `persistence/repository.py` | `trading_platform/persistence/base_repository.py` | 888 | Extract generic CRUD patterns; arb-specific queries stay |
| `api/app.py` | `trading_platform/api/base_app.py` | 675 | Extract health, auth, pause, scanner control; product endpoints stay |
| `data/liquidity_cache.py` | `trading_platform/data/cache.py` | 148 | Rename to generic TTL cache |
| `data/rpc_failover.py` | `trading_platform/data/rpc_failover.py` | 122 | Fully generic already |
| `core/env.py` | `trading_platform/config/env.py` | 67 | Fully generic already |

**Total platform code: ~4,625 lines (46% of current codebase)**

### What stays in ArbitrageTrader

| Module | Lines | Why it's product-specific |
|--------|-------|--------------------------|
| `strategy/scanner.py` | 360 | DEX cross-pair evaluation, composite scoring |
| `strategy/arb_strategy.py` | 302 | Flash loan cost model (fees, slippage, gas) |
| `execution/chain_executor.py` | 630 | Flashbots, flash loans, router resolution |
| `market/onchain_market.py` | 1181 | V3 quoter calls, Balancer queries |
| `core/contracts.py` | 433 | ABIs, addresses, RPC URLs |
| `core/tokens.py` | 300 | Token registry, dynamic resolution |
| `core/models.py` | 194 | Opportunity, MarketQuote |
| `registry/*.py` | 1007 | Pool/pair discovery |
| `dashboards/*.py` | 1688 | Arb-specific dashboard views |

**Total product-specific: ~6,095 lines (54%)**

---

## Platform Abstractions

### Pipeline (generic)

```python
# trading_platform/pipeline/lifecycle.py

class BasePipeline:
    """Generic 6-stage pipeline. Products override stages 4-6."""

    def __init__(self, repo, risk_policy, simulator=None, submitter=None, verifier=None):
        ...

    def process(self, candidate) -> PipelineResult:
        """Run a candidate through all stages."""
        with self.repo.batch():
            opp_id = self.detect(candidate)       # Stage 1 — always
            self.price(opp_id, candidate)          # Stage 2 — always
            verdict = self.evaluate_risk(candidate) # Stage 3 — always

        if verdict.approved:
            self.simulate(candidate)               # Stage 4 — if wired
            self.submit(candidate)                  # Stage 5 — if wired
            self.verify(candidate)                  # Stage 6 — if wired
```

### Risk policy (generic framework, product-specific rules)

```python
# trading_platform/risk/policy.py

class RiskRule(Protocol):
    """One risk check. Products register their own rules."""
    name: str
    def evaluate(self, candidate, context: dict) -> RiskVerdict: ...

class RuleBasedPolicy:
    """Evaluates a list of rules sequentially. Any failure = hard veto."""

    def __init__(self, rules: list[RiskRule]):
        self.rules = rules

    def evaluate(self, candidate, **context) -> RiskVerdict:
        for rule in self.rules:
            verdict = rule.evaluate(candidate, context)
            if not verdict.approved:
                return verdict
        return RiskVerdict(approved=True, reason="all_rules_passed")
```

```python
# ArbitrageTrader: registers arb-specific rules
policy = RuleBasedPolicy(rules=[
    MinSpreadRule(chain_thresholds=CHAIN_MIN_SPREAD_PCT),
    MinProfitRule(chain_thresholds=CHAIN_MIN_NET_PROFIT),
    WarningFlagRule(max_flags=1),
    GasProfitRatioRule(max_ratio=0.5),
    RateLimitRule(max_per_hour=100),
    ExposureRule(max_per_pair=10),
])
```

```python
# PolymarketTrader: registers PM-specific rules
policy = RuleBasedPolicy(rules=[
    MinEdgeRule(min_edge=0.10),
    SpreadRule(max_spread=0.05),
    SettlementBufferRule(buffer_seconds=60),
    DailyLossRule(max_loss_usdc=100),
    ConcurrencyRule(max_positions=1),
    DailyTradeCountRule(max_trades=50),
])
```

### Alerting (generic sending, product-specific content)

```python
# trading_platform/alerting/smart_alerts.py

class BaseAlerter:
    """Generic hourly/daily email report sender."""

    def send_report(self, event_type, plain_text, html_body, details): ...
    def start_background(self): ...

    # Products override these to generate content:
    def build_hourly_report(self) -> tuple[str, str, dict]:
        """Return (plain_text, html, details). Override in product."""
        raise NotImplementedError

    def build_daily_report(self) -> tuple[str, str, dict]:
        raise NotImplementedError
```

### Dashboard (generic shell, product-specific views)

```python
# trading_platform/api/base_app.py

def create_base_app(risk_policy, repo, metrics):
    """Create FastAPI app with shared endpoints."""
    app = FastAPI(...)

    # These are the same for every product:
    app.get("/health")(health)
    app.get("/execution")(execution_status)
    app.post("/execution")(toggle_execution)
    app.get("/pause")(pause_status)
    app.get("/scanner")(scanner_status)
    app.get("/metrics")(get_metrics)
    app.get("/pnl")(get_pnl)

    return app  # Product adds its own endpoints on top
```

```python
# ArbitrageTrader: adds arb-specific endpoints
app = create_base_app(risk_policy, repo, metrics)
app.get("/opportunities")(get_opportunities)
app.get("/dashboard/window/{key}")(dashboard_window)
app.get("/wallet/balance")(wallet_balance)
```

---

## Packaging

### trading-platform as a pip package

```toml
# trading-platform/pyproject.toml
[project]
name = "trading-platform"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "fastapi>=0.100",
    "uvicorn",
    "python-dotenv",
]

[project.optional-dependencies]
postgres = ["psycopg2-binary"]
alerting = ["requests"]  # for telegram, discord
gmail = []  # uses stdlib smtplib
```

### Products depend on it

```toml
# ArbitrageTrader/pyproject.toml
[project]
name = "arbitrage-trader"
dependencies = [
    "trading-platform @ git+https://github.com/twainstain/trading-platform.git",
    "web3>=6.0",
]
```

```toml
# PolymarketTrader/pyproject.toml
[project]
name = "polymarket-trader"
dependencies = [
    "trading-platform @ git+https://github.com/twainstain/trading-platform.git",
    "py-clob-client",
    "websockets",
]
```

---

## Migration Plan

### Phase 1: Extract platform (no product changes)
1. Create `trading-platform/` repo
2. Copy generic modules from ArbitrageTrader
3. Add abstractions (BasePipeline, RuleBasedPolicy, BaseAlerter)
4. Write platform tests (independent of any product)
5. Publish as installable package

### Phase 2: Migrate ArbitrageTrader
1. Add `trading-platform` as dependency
2. Replace local imports with `from trading_platform.pipeline import ...`
3. Subclass platform abstractions for arb-specific logic
4. Delete moved files from ArbitrageTrader
5. Verify all 874 tests pass

### Phase 3: Build PolymarketTrader
1. Create `PolymarketTrader/` repo
2. Import `trading-platform`
3. Build product-specific modules (feeds, strategy, execution)
4. Subclass platform abstractions for PM-specific logic
5. Reuse dashboard design system for PM-specific views

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Breaking ArbitrageTrader during extraction | Phase 2 is behind a feature branch; merge only when all tests pass |
| Over-abstracting (making platform too generic) | Start with concrete extraction, abstract only when PolymarketTrader needs differ |
| Version drift between platform and products | Pin platform version in each product; bump intentionally |
| Circular dependencies | Platform has zero product imports; dependency flows one way only |
| Development velocity slowdown | Platform changes require bumping version in products — use git branch deps during active dev |
