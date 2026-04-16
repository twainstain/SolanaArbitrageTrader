# Trading Platform Migration Log

Tracks completed phases, what changed, how other bots (SolanaTrader, PolymarketTrader) use the same patterns.

---

## Phase 1: Circuit Breaker + Retry (completed)

### What Changed

| Item | Before | After |
|------|--------|-------|
| `src/risk/circuit_breaker.py` | 170-line local implementation | **Deleted** — uses `trading_platform.risk.circuit_breaker` via adapter |
| `src/risk/retry.py` | 80-line local implementation | **Deleted** — uses `trading_platform.risk.retry` via adapter |
| `src/platform_adapters.py` | Did not exist | **Created** — thin wrappers mapping AT domain terms to TP generic API |

### Adapter Pattern

The platform uses **generic names** that work across all products. Each product creates an adapter layer mapping its domain language:

```
trading_platform (generic)     ArbitrageTrader (EVM arb)
─────────────────────────────  ──────────────────────────
record_failure()           →   record_revert()
record_error()             →   record_rpc_error()
record_success()           →   record_execution_success()
record_fresh_data()        →   record_fresh_quote()
record_event(seq)          →   record_trade_at_block(block)
should_block()             →   allows_execution() → (bool, reason)
is_still_valid             →   is_still_profitable
"repeated_failures"        →   "repeated_reverts"
"external_errors"          →   "rpc_degradation"
```

### How Other Bots Use This

**SolanaTrader** would create its own `platform_adapters.py`:

```python
from trading_platform.risk.circuit_breaker import CircuitBreaker as _PlatformBreaker

class CircuitBreaker:
    """Solana-flavored circuit breaker."""

    _REASON_MAP = {
        "repeated_failures": "repeated_tx_drops",
        "external_errors": "rpc_degradation",
    }

    def record_tx_drop(self) -> None:
        """Solana tx dropped from block (equivalent of EVM revert)."""
        self._breaker.record_failure()

    def record_rpc_error(self) -> None:
        """Solana RPC node error."""
        self._breaker.record_error()

    def record_slot_confirmation(self) -> None:
        """Tx confirmed in a slot."""
        self._breaker.record_success()
```

**PolymarketTrader** would create its own:

```python
class CircuitBreaker:
    """Polymarket-flavored circuit breaker."""

    _REASON_MAP = {
        "repeated_failures": "repeated_order_rejects",
        "external_errors": "api_degradation",
    }

    def record_order_rejected(self) -> None:
        self._breaker.record_failure()

    def record_api_error(self) -> None:
        self._breaker.record_error()

    def record_order_filled(self) -> None:
        self._breaker.record_success()
```

**RetryPolicy** follows the same pattern — the `is_still_valid` callback maps to:
- ArbitrageTrader: `is_still_profitable` (spread still exists?)
- SolanaTrader: `is_still_profitable` (price still favorable?)
- PolymarketTrader: `is_odds_still_favorable` (market hasn't moved?)

### Tests

949/949 pass. Zero regressions.

---

## Phase 2: Alert Dispatcher + Queue (completed)

### What Changed

| Item | Before | After |
|------|--------|-------|
| `src/alerting/dispatcher.py` | 187-line standalone implementation | **Rewritten** — subclasses `trading_platform.alerting.AlertDispatcher`, keeps AT convenience methods |
| `src/pipeline/queue.py` | 168-line local `CandidateQueue` | **Deleted** — uses `trading_platform.pipeline.PriorityQueue` via adapter in `platform_adapters.py` |

### Dispatcher: Subclass Pattern

The platform provides the generic fan-out engine. Products subclass it and add domain-specific convenience methods:

```
trading_platform.AlertDispatcher (generic)
├── alert(event_type, message, details) → fan-out to all backends
├── add_backend(backend)
└── backend_count

ArbitrageTrader.AlertDispatcher (subclass)
├── inherits alert(), add_backend(), backend_count
├── opportunity_found(pair, buy_dex, sell_dex, spread, profit, ...)
├── trade_executed(pair, tx_hash, profit, ...)
├── trade_reverted(pair, tx_hash, reason, ...)
├── daily_summary(scans, opportunities, executed, profit, reverts)
└── system_error(component, error)
```

### Queue: Adapter Pattern

Platform provides a generic `PriorityQueue` with `QueuedItem(item: Any, metadata: dict)`. Products wrap it:

```
trading_platform.PriorityQueue    ArbitrageTrader.CandidateQueue
─────────────────────────────────  ──────────────────────────────
push(item, priority, metadata)  →  push(opportunity, priority, scan_marks)
pop() → QueuedItem              →  pop() → QueuedCandidate
QueuedItem.item                 →  QueuedCandidate.opportunity
QueuedItem.metadata             →  QueuedCandidate.scan_marks
```

### How Other Bots Use This

**SolanaTrader dispatcher:**

```python
from trading_platform.alerting.dispatcher import AlertDispatcher as _Base

class AlertDispatcher(_Base):
    """Solana-specific alerts."""

    def swap_executed(self, pair: str, tx_sig: str, profit: float) -> int:
        return self.alert("swap_executed", f"Swap: {pair}\nTX: {tx_sig}\nProfit: {profit:.6f}")

    def swap_failed(self, pair: str, tx_sig: str, reason: str) -> int:
        return self.alert("swap_failed", f"FAILED: {pair}\nReason: {reason}")

    def jito_bundle_landed(self, pair: str, slot: int, profit: float) -> int:
        return self.alert("bundle_landed", f"Jito bundle in slot {slot}: +{profit:.6f} SOL")
```

**PolymarketTrader dispatcher:**

```python
class AlertDispatcher(_Base):
    """Polymarket-specific alerts."""

    def position_opened(self, market: str, outcome: str, size: float, odds: float) -> int:
        return self.alert("position_opened", f"Opened: {market}\n{outcome} @ {odds:.2f}\nSize: ${size:.2f}")

    def position_closed(self, market: str, pnl: float) -> int:
        return self.alert("position_closed", f"Closed: {market}\nPnL: ${pnl:.2f}")
```

**SolanaTrader queue:**

```python
@dataclass
class QueuedSwap:
    swap: SolanaSwapOpportunity
    enqueued_at: float
    priority: float
    metadata: dict

class SwapQueue:
    def __init__(self, max_size=100):
        self._queue = PriorityQueue(max_size=max_size)

    def push(self, swap: SolanaSwapOpportunity, priority: float) -> bool:
        return self._queue.push(swap, priority=priority)

    def pop(self) -> QueuedSwap | None:
        item = self._queue.pop()
        if item is None: return None
        return QueuedSwap(swap=item.item, enqueued_at=item.enqueued_at,
                          priority=item.priority, metadata=item.metadata or {})
```

### Database Migration (Deferred)

AT's `src/persistence/db.py` has 33 CREATE TABLE/INDEX statements and 2 migration functions (`_ensure_pairs_chain_uniqueness`, `_ensure_trade_result_columns`) tightly coupled to the arbitrage schema. The platform provides a generic `DbConnection` + `init_db(schema=...)`, but extracting AT's schema is a larger refactor with lower ROI than the other Phase 2 items.

**For other bots**: Use `trading_platform.persistence.init_db(schema=YOUR_SCHEMA)` directly. Only AT has this migration debt because it predates the platform.

### Tests

949/949 pass. Zero regressions.

---

## Summary So Far

### Lines of Code

| Metric | Count |
|--------|------:|
| Lines deleted from ArbitrageTrader | ~390 |
| Lines added (adapters + rewritten dispatcher) | ~200 |
| **Net reduction** | **~190 lines** |
| Local modules replaced by TP | 4 (circuit_breaker, retry, queue, dispatcher core) |
| Tests passing | 949/949 |

### Pattern Catalog

Three patterns emerged for integrating with the platform:

1. **Adapter** (circuit_breaker, retry, queue) — Thin wrapper class in `platform_adapters.py` that maps domain terms to generic API. Used when the method signatures differ but the logic is identical.

2. **Subclass** (alert dispatcher) — Product's class extends the platform's base class, inheriting generic logic and adding domain-specific convenience methods. Used when the product needs all of the platform's behavior plus extra methods.

3. **Direct use** (future: logging, env) — Import and use the platform module directly when APIs are already compatible. Used when no domain-term mapping is needed.

### File Layout After Migration

```
src/
├── platform_adapters.py          ← NEW: domain-term adapters (CB, Retry, Queue)
├── alerting/
│   ├── dispatcher.py             ← REWRITTEN: subclasses TP AlertDispatcher
│   ├── smart_alerts.py           (unchanged — AT-specific alert rules)
│   ├── gmail.py, discord.py, telegram.py  (unchanged — migrate in Phase 3)
├── risk/
│   ├── policy.py                 (unchanged — migrate in Phase 4)
│   ├── circuit_breaker.py        ← DELETED (now in platform_adapters.py)
│   ├── retry.py                  ← DELETED (now in platform_adapters.py)
├── pipeline/
│   ├── lifecycle.py              (unchanged — migrate in Phase 4)
│   ├── queue.py                  ← DELETED (now in platform_adapters.py)
│   ├── verifier.py               (unchanged — AT-specific)
├── persistence/
│   ├── db.py                     (unchanged — deferred, tightly coupled schema)
│   ├── repository.py             (unchanged — migrate in Phase 3)
...
```

---

## Phase 3: Config, Repository, Metrics, Backends (evaluated — mostly skipped)

### Findings

Phase 3 modules are **overwhelmingly product-specific**. The platform provides tiny base classes (3-4 methods each) while ArbitrageTrader needs 10-30x more domain logic. Subclassing would technically work but provides almost no code reduction.

| Module | AT Lines | TP Base Methods | AT Domain Methods | Migration ROI |
|--------|:--------:|:---------------:|:-----------------:|:-------------:|
| Alerting backends | 101/67/61 | send(), configured, name | Per-event subject prefixes, HTML formatting | **Skip** — AT's are more polished |
| MetricsCollector | 148 | increment, set_gauge, record_latency | 9 methods, rich snapshot with rates/percentiles | **Skip** — adapter bigger than original |
| BotConfig | 260 | from_file, validate, to_dict | DexConfig, PairConfig, gas_cost_for_chain, min_liquidity | **Skip** — custom JSON parsing |
| Repository | 888 | checkpoint, update_status | 30+ domain methods, PnL analytics, scan history | **Skip** — 3 reusable out of 30+ |

### Decision: Skip, but document the pattern for new bots

These modules stay as-is in ArbitrageTrader. But **new bots** (SolanaTrader, PolymarketTrader) should build their domain modules on top of the platform from day one, not extract later.

### How New Bots Should Use the Platform

**MetricsCollector** — use TP's generic API directly:

```python
# SolanaTrader / PolymarketTrader — use generic API from the start
from trading_platform.observability import MetricsCollector

metrics = MetricsCollector()
metrics.increment("opportunities_detected")
metrics.increment("rejected", tag="below_min_profit")
metrics.increment("executions_submitted")
metrics.set_gauge("spread_bps", 15.5)
metrics.record_latency(145.2)

snap = metrics.snapshot()
# → {"counters": {"opportunities_detected": 1, ...}, "p95_latency_ms": 145.2}
```

No need for domain wrappers if you design with generic counters from the start.

**Config** — subclass BaseConfig:

```python
from trading_platform.config import BaseConfig

@dataclass(frozen=True)
class SolanaConfig(BaseConfig):
    pair: str = "SOL/USDC"
    trade_size_sol: Decimal = Decimal("10")
    min_profit_sol: Decimal = Decimal("0.01")
    jupiter_slippage_bps: int = 50
    rpc_urls: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if self.trade_size_sol <= 0:
            raise ValueError("trade_size must be positive")

cfg = SolanaConfig.from_file("config/solana.json")
```

**Repository** — subclass BaseRepository:

```python
from trading_platform.persistence import BaseRepository, init_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS swaps (
    swap_id TEXT PRIMARY KEY,
    pair TEXT NOT NULL,
    dex_a TEXT NOT NULL,
    dex_b TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'detected',
    profit_sol TEXT NOT NULL DEFAULT '0',
    detected_at TEXT NOT NULL
);
"""

class SolanaRepository(BaseRepository):
    def create_swap(self, pair, dex_a, dex_b) -> str:
        swap_id = f"swap_{uuid.uuid4().hex[:12]}"
        self.conn.execute("INSERT INTO swaps ...", (...))
        self.conn.commit()
        return swap_id

    def get_pnl_summary(self) -> dict:
        # Product-specific analytics
        ...

db = init_db(schema=SCHEMA)
repo = SolanaRepository(db)
```

**Alert backends** — use TP's directly or subclass for custom formatting:

```python
from trading_platform.alerting import GmailAlert, DiscordAlert, AlertDispatcher

# Use as-is (generic [Trading] subject prefix)
gmail = GmailAlert(subject_prefix="[Solana]")
discord = DiscordAlert()

dispatcher = AlertDispatcher()
dispatcher.add_backend(gmail)
dispatcher.add_backend(discord)
```

### Key Lesson

> **Build on the platform from day one. Don't extract later.**
>
> ArbitrageTrader was built before the platform existed, so its modules are self-contained
> and tightly coupled to the domain. Migrating them provides minimal code reduction.
> New bots should start with the platform's generic APIs and only add domain wrappers
> where the generic names are confusing (like CircuitBreaker's record_failure vs record_revert).

### Tests

949/949 pass. No code changes in Phase 3 (evaluation only).

---

## Phase 4.1: Risk Policy → Pluggable Rules (completed)

### What Changed

| Item | Before | After |
|------|--------|-------|
| `src/risk/policy.py` | 310-line monolith with 8 inline rules | **Rewritten** — delegates to rule chain, 170 lines |
| `src/risk/rules.py` | Did not exist | **Created** — 9 pluggable rule classes, 230 lines |

### Architecture

The monolithic `evaluate()` with 8 inline `if` blocks was refactored into separate rule classes that follow the `trading_platform.contracts.RiskRule` protocol:

```
RiskPolicy.evaluate(opportunity, ...)
  │
  ├─ ExecutionModeRule    — chain disabled? set simulation_mode in context
  ├─ MinSpreadRule        — per-chain spread thresholds
  ├─ MinProfitRule        — per-chain profit thresholds
  ├─ PoolLiquidityRule    — stale pool safety net
  ├─ WarningFlagRule      — compounding risk veto
  ├─ LiquidityScoreRule   — pool quality check
  ├─ GasProfitRatioRule   — economics check
  ├─ RateLimitRule        — velocity control
  └─ ExposureLimitRule    — position sizing
```

Each rule:
- Has a `name` for rejection tracking
- Takes `(opportunity, context)` where context is a shared dict
- Returns `RiskVerdict(approved, reason, details)`
- Is independently testable

The shared `context` dict passes state between rules (e.g., `ExecutionModeRule` sets `simulation_mode` for the final verdict).

### How Other Bots Use This

**PolymarketTrader** — prediction market rules:

```python
from trading_platform.risk import RuleBasedPolicy
from trading_platform.contracts import RiskVerdict

class MinEdgeRule:
    name = "min_edge"
    def evaluate(self, bet, context) -> RiskVerdict:
        if bet.edge_pct < context.get("min_edge", 0.05):
            return RiskVerdict(False, "edge_too_thin", {"edge": bet.edge_pct})
        return RiskVerdict(True, "ok")

class MaxPositionRule:
    name = "max_position"
    def evaluate(self, bet, context) -> RiskVerdict:
        if context["current_exposure"] + bet.size > context["max_exposure"]:
            return RiskVerdict(False, "position_limit", {})
        return RiskVerdict(True, "ok")

class MarketLiquidityRule:
    name = "market_liquidity"
    def evaluate(self, bet, context) -> RiskVerdict:
        if bet.order_book_depth < 1000:
            return RiskVerdict(False, "thin_book", {"depth": bet.order_book_depth})
        return RiskVerdict(True, "ok")

# Wire up
policy = RuleBasedPolicy(rules=[
    MinEdgeRule(),
    MaxPositionRule(),
    MarketLiquidityRule(),
])
verdict = policy.evaluate(bet, current_exposure=500, max_exposure=10000, min_edge=0.03)
```

**SolanaTrader** — DEX arb rules:

```python
class MinProfitSolRule:
    name = "min_profit"
    def evaluate(self, swap, context) -> RiskVerdict:
        if swap.net_profit_sol < context.get("min_profit_sol", 0.01):
            return RiskVerdict(False, "below_min_profit", {})
        return RiskVerdict(True, "ok")

class JitoTipRule:
    name = "jito_tip"
    def evaluate(self, swap, context) -> RiskVerdict:
        if swap.expected_tip > swap.net_profit_sol * 0.5:
            return RiskVerdict(False, "tip_too_high", {"tip": swap.expected_tip})
        return RiskVerdict(True, "ok")

class SlotFreshnessRule:
    name = "slot_freshness"
    def evaluate(self, swap, context) -> RiskVerdict:
        if swap.slot_age > 2:
            return RiskVerdict(False, "stale_slot", {"age": swap.slot_age})
        return RiskVerdict(True, "ok")
```

### Benefits

1. **Independently testable** — each rule is a small class with clear inputs/outputs
2. **Reorderable** — change rule priority by reordering the list
3. **Toggleable** — remove a rule from the list to disable it
4. **Reusable** — rules like RateLimitRule and ExposureLimitRule work across products
5. **Observable** — the rule `name` appears in rejection reasons for dashboard tracking

### Tests

949/949 pass. Zero regressions. All existing risk policy tests pass unchanged.

---

## Remaining: Phase 4.2 (pipeline)

### Pipeline → BasePipeline

Convert `CandidatePipeline` to subclass `BasePipeline` with abstract stage methods.

**For other bots**: Each product implements its own stages:
```python
class SolanaPipeline(BasePipeline):
    def detect(self, swap) -> str:
        return self.repo.create_swap(swap.pair, swap.dex_a, swap.dex_b)

    def price(self, swap_id, swap) -> None:
        self.repo.save_pricing(swap_id, swap.cost, swap.output)

    def evaluate_risk(self, swap) -> RiskVerdict:
        return self.risk_policy.evaluate(swap)
```

These are the highest-effort items. Recommend deferring until after SolanaTrader or PolymarketTrader actively needs the shared pipeline.
