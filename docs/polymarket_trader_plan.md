# PolymarketTrader Repo Plan

> A practical implementation checklist for a dedicated `polymarket_trader`
> repository, built after `ArbitrageTrader` and informed by the lessons we have
> already learned there.

Related reading:

- [Polymarket Product Guide](./polymarket_product_guide.md)

---

## Recommendation

We should build `polymarket_trader` as a **separate repository or clearly
separated sibling project**, not as a thin extension of `ArbitrageTrader`.

Why:

- `ArbitrageTrader` is a spread-driven execution system.
- `PolymarketTrader` is a latency-sensitive directional trading system.
- The surrounding platform overlaps a lot.
- The actual trading loop, execution risk, and settlement model do not.

That means the right plan is:

1. Finish `ArbitrageTrader` first.
2. Reuse ideas and selected modules from it.
3. Build `polymarket_trader` as a dedicated system.
4. Start with measurement and paper trading before live trading.

---

## Product Framing

This should be treated as a **research and paper-trading engine first**.

It is tempting to describe the idea as pure arbitrage because it reacts to a
pricing lag, but operationally it behaves differently from our current bot.

`ArbitrageTrader`:

- detects a spread
- verifies costs
- executes
- exits immediately

`PolymarketTrader`:

- detects a fast move in spot markets
- estimates whether Polymarket is mispriced
- enters a position
- holds inventory for some period
- exits or settles later

So the real risk profile includes:

- fill risk
- spread risk
- latency risk
- model risk
- holding-period risk
- settlement and reconciliation risk

This is why we should not assume this is “just another arb strategy.”

---

## Core Goal

The first goal is **not** to make money quickly.

The first goal is to answer these questions with real data:

- Does a measurable spot-to-Polymarket repricing lag still exist?
- How long does it last in practice?
- Which assets and windows matter most?
- What is the actual edge after spread, fees, and slippage?
- How much does paper alpha degrade when moved toward live execution?

If we cannot answer those questions cleanly, we should not deploy real capital.

---

## Current Caveats

As of **April 15, 2026**, these caveats matter and should stay in the repo docs.

### Geographic restrictions

Polymarket documentation currently states that order placement is restricted in
some jurisdictions, including the United States.

Reference:

- [Polymarket geoblocking docs](https://docs.polymarket.com/polymarket-learn/FAQ/geoblocking)

This means live trading should be treated as a compliance-gated phase, not as a
baseline assumption.

### WebSocket implementation details

The official docs currently describe market subscriptions and user-channel auth
flows that are more specific than the simplified examples often seen in guides.

References:

- [Polymarket WebSocket overview](https://docs.polymarket.com/market-data/websocket/overview)
- [Polymarket market channel](https://docs.polymarket.com/developers/CLOB/websocket/market-channel)
- [Polymarket user channel](https://docs.polymarket.com/market-data/websocket/user-channel)

The implementation should follow the official current wire format, not stale
examples copied from older blog posts or generated snippets.

### Fees and breakeven

Fees should not be modeled as a vague flat constant. Breakeven must be computed
explicitly from the current fee model and realistic fill assumptions.

Reference:

- [Polymarket fee docs](https://docs.polymarket.com/trading/fees)

---

## What Reuse Looks Like

We should think in terms of **conceptual reuse** and **selective code reuse**,
not repo-level copy/paste.

### Worth reusing from `ArbitrageTrader`

These areas have high carry-over value:

- risk policy patterns
- circuit breaker patterns
- metrics and observability patterns
- persistence patterns
- alerting patterns
- dashboard and API patterns
- config management patterns
- logging and audit trails

If we copy code, it should be done carefully and intentionally, with the new
repo owning its own abstractions.

### Must be new in `polymarket_trader`

These parts are specific enough that they should be built for this repo:

- market discovery for active Polymarket markets
- Binance / Coinbase spot feed clients
- Polymarket market data client
- Polymarket user-channel client
- fair-value / repricing model
- execution engine for CLOB orders
- position manager
- settlement reconciler
- replay engine
- paper trading engine

---

## High-Level Architecture

```text
spot_feed(s) -> market_normalizer -> signal_model -> risk_engine -> executor
      \                                             |
       \-> latency recorder -> replay store -> analytics

executor -> user_channel_listener -> position_manager -> settlement_reconciler
```

### Key design rule

Keep these responsibilities separate:

- market data ingestion
- signal generation
- execution
- position tracking
- settlement accounting
- analytics and replay

That separation will make the system testable and help us avoid mixing
strategy assumptions into execution code.

---

## Suggested Repo Structure

```text
polymarket_trader/
├── README.md
├── pyproject.toml
├── .env.example
├── config/
│   ├── base.yaml
│   ├── dev.yaml
│   ├── paper.yaml
│   └── live.yaml
├── docs/
│   ├── architecture.md
│   ├── strategy_hypotheses.md
│   ├── risk_model.md
│   ├── runbooks.md
│   └── go_live_checklist.md
├── src/
│   ├── polymarket_trader/
│   │   ├── cli/
│   │   │   └── main.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── models.py
│   │   │   ├── enums.py
│   │   │   └── clock.py
│   │   ├── feeds/
│   │   │   ├── spot_binance.py
│   │   │   ├── spot_coinbase.py
│   │   │   ├── polymarket_market_ws.py
│   │   │   ├── polymarket_user_ws.py
│   │   │   └── market_discovery.py
│   │   ├── strategy/
│   │   │   ├── signal_model.py
│   │   │   ├── feature_builder.py
│   │   │   ├── thresholds.py
│   │   │   └── repricing_score.py
│   │   ├── execution/
│   │   │   ├── clob_client.py
│   │   │   ├── order_router.py
│   │   │   ├── paper_executor.py
│   │   │   └── live_executor.py
│   │   ├── positions/
│   │   │   ├── position_manager.py
│   │   │   ├── pnl.py
│   │   │   └── settlement.py
│   │   ├── risk/
│   │   │   ├── policy.py
│   │   │   ├── circuit_breaker.py
│   │   │   ├── bankroll.py
│   │   │   └── limits.py
│   │   ├── persistence/
│   │   │   ├── db.py
│   │   │   ├── repository.py
│   │   │   └── migrations/
│   │   ├── observability/
│   │   │   ├── metrics.py
│   │   │   ├── logging.py
│   │   │   ├── latency.py
│   │   │   └── audit.py
│   │   ├── replay/
│   │   │   ├── recorder.py
│   │   │   ├── loader.py
│   │   │   └── simulator.py
│   │   └── services/
│   │       ├── paper_trading_service.py
│   │       ├── live_trading_service.py
│   │       └── analytics_service.py
└── tests/
    ├── unit/
    ├── integration/
    └── replay/
```

---

## Build Phases

## Phase 0: Repo bootstrap

Goal: create a clean standalone repo with a minimal skeleton.

Checklist:

- [ ] initialize `polymarket_trader` repo
- [ ] create Python project with `pyproject.toml`
- [ ] add linting, formatting, and test tooling
- [ ] add `.env.example`
- [ ] add config directory with base and environment-specific config files
- [ ] add CI for unit tests and static checks
- [ ] add `README.md` with project framing and non-go-live disclaimer

Exit criteria:

- repo installs cleanly
- test command runs cleanly
- config loading works
- CLI stub starts successfully

## Phase 1: Market discovery and data capture

Goal: prove we can collect the right data reliably.

Checklist:

- [ ] implement Polymarket market discovery for BTC/ETH/SOL short-window markets
- [ ] store market metadata including condition IDs, token IDs, start/end times
- [ ] implement Binance spot trade feed
- [ ] optionally implement Coinbase spot feed for cross-checking
- [ ] implement Polymarket market WebSocket client
- [ ] normalize timestamps into a single internal format
- [ ] record raw spot ticks and book updates
- [ ] record local receive timestamps for latency analysis
- [ ] persist raw event streams and periodic snapshots
- [ ] add health metrics for disconnects, reconnects, and event rates

Exit criteria:

- data collection can run for hours without falling over
- market windows are tracked correctly
- we can reconstruct a timeline of spot moves and Polymarket book changes

## Phase 2: Replay and offline analytics

Goal: determine whether the lag is still tradable after realistic assumptions.

Checklist:

- [ ] build replay loader for recorded sessions
- [ ] reconstruct book state over time
- [ ] define candidate triggers from spot movement
- [ ] compute Polymarket repricing delay after each trigger
- [ ] estimate fillable size at top-of-book
- [ ] model fees explicitly
- [ ] model slippage and partial fills
- [ ] label each candidate opportunity with theoretical and conservative PnL
- [ ] produce summary analytics by asset, hour, and volatility regime
- [ ] document which assumptions are empirical vs inferred

Exit criteria:

- we can quantify lag distributions
- we can estimate post-cost edge
- we have evidence for or against continuing to paper trading

## Phase 3: Paper trading engine

Goal: run the full strategy loop without risking real capital.

Checklist:

- [ ] implement feature builder for live signal inputs
- [ ] implement first-pass scoring model instead of hardcoded fair probability
- [ ] separate signal generation from execution logic
- [ ] implement paper executor with realistic fill rules
- [ ] implement position manager with one-position and multi-position modes
- [ ] implement settlement accounting for closed windows
- [ ] persist signals, simulated orders, fills, positions, and outcomes
- [ ] add dashboards for win rate, edge decay, and fill quality
- [ ] add alerts for disconnects, missed market windows, and abnormal behavior

Exit criteria:

- end-to-end paper trading runs cleanly on live feeds
- every signal and paper trade is auditable
- we understand paper PnL by strategy slice, not just aggregate

## Phase 4: Execution hardening

Goal: make live execution safe enough for a tiny pilot if compliance allows.

Checklist:

- [ ] implement authenticated Polymarket client flows
- [ ] implement user-channel listener for order and trade events
- [ ] implement live order router with acknowledgments and retries
- [ ] handle partial fills and cancel/replace flows
- [ ] implement hard daily loss limits
- [ ] implement market-state guards near settlement
- [ ] implement stale-data guards
- [ ] implement exchange-feed disagreement guards
- [ ] implement kill switch and circuit breaker behavior
- [ ] create explicit operational runbooks

Exit criteria:

- live execution paths are tested in dry runs where possible
- operational failures move the system to safe states
- go-live checklist is complete except for capital allocation approval

## Phase 5: Tiny live pilot

Goal: validate live degradation with minimal risk.

Checklist:

- [ ] use smallest practical trade sizes
- [ ] allow only one live position at a time
- [ ] cap daily loss very tightly
- [ ] log every outbound order and every venue response
- [ ] compare live results against simultaneous paper shadow trading
- [ ] review fill quality daily
- [ ] stop immediately if paper/live divergence is too large

Exit criteria:

- live behavior matches expectations closely enough to justify continuation
- compliance and operational requirements remain satisfied

---

## Strategy Guidelines

These should be rules for the repo from the beginning.

### Do not hardcode certainty

Avoid simplistic logic like:

- `fair_up_prob = 0.75`
- fixed edge assumptions without calibration
- fixed fee assumptions without current docs

Instead, start with a transparent scoring model using features like:

- spot move magnitude
- move speed
- time remaining in market window
- Polymarket spread
- top-of-book depth
- recent repricing speed
- cross-exchange confirmation
- local feed freshness

### Record non-trades too

We should persist:

- trades we took
- signals we skipped
- signals we missed
- signals blocked by risk policy

That will matter later when we try to understand whether the model was bad or
execution was late.

### Separate research from execution

The replay system should be able to evaluate strategy changes without changing
live execution code.

---

## Initial Data Model

These tables are a reasonable starting point for the dedicated repo.

```sql
CREATE TABLE markets (
    market_id TEXT PRIMARY KEY,
    asset TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    up_token_id TEXT NOT NULL,
    down_token_id TEXT NOT NULL,
    window_start TIMESTAMP NOT NULL,
    window_end TIMESTAMP NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE raw_spot_ticks (
    id TEXT PRIMARY KEY,
    venue TEXT NOT NULL,
    asset TEXT NOT NULL,
    price NUMERIC NOT NULL,
    source_ts TIMESTAMP,
    received_ts TIMESTAMP NOT NULL
);

CREATE TABLE raw_book_events (
    id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_ts TIMESTAMP,
    received_ts TIMESTAMP NOT NULL
);

CREATE TABLE signals (
    signal_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    asset TEXT NOT NULL,
    side TEXT NOT NULL,
    score NUMERIC NOT NULL,
    edge_estimate NUMERIC,
    trigger_price NUMERIC,
    pm_price NUMERIC,
    spread NUMERIC,
    created_at TIMESTAMP NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    signal_id TEXT,
    venue TEXT NOT NULL,
    token_id TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    requested_price NUMERIC,
    requested_size NUMERIC NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE fills (
    fill_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    fill_price NUMERIC NOT NULL,
    fill_size NUMERIC NOT NULL,
    fee NUMERIC,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE positions (
    position_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_cost NUMERIC NOT NULL,
    quantity NUMERIC NOT NULL,
    status TEXT NOT NULL,
    opened_at TIMESTAMP NOT NULL,
    closed_at TIMESTAMP
);
```

---

## Testing Plan

We should not rely only on unit tests.

Checklist:

- [ ] unit tests for signal and risk logic
- [ ] unit tests for fee and PnL calculations
- [ ] integration tests for feed parsing
- [ ] integration tests for order lifecycle state transitions
- [ ] replay-based tests using captured sessions
- [ ] long-run soak test for data collection
- [ ] failure tests for disconnects and stale feeds

---

## Operational Rules

Before any live pilot, the repo should enforce these policies:

- live mode is disabled by default
- paper mode is the default runtime mode
- all secrets come from environment or secret management
- every live action is audit-logged
- every risk halt is visible in metrics and alerts
- the system must fail closed on stale or conflicting data

---

## Definition Of Done For The Repo

We should consider the initial `polymarket_trader` repo successful when:

- it captures and replays real market data reliably
- it can quantify lag and edge after costs
- it paper trades end-to-end with full auditability
- it has risk controls and runbooks strong enough for a tiny pilot
- it gives us an honest answer about whether the opportunity is still real

---

## Bottom Line

`polymarket_trader` is worth building if we treat it as a disciplined research
system first.

The right sequence is:

1. build the repo
2. collect the data
3. measure the lag
4. replay the opportunities
5. paper trade the strategy
6. only then consider a tiny live pilot

That framing gives us something much more valuable than a viral bot clone: a
system that can tell us whether the edge is real, shrinking, or already gone.
