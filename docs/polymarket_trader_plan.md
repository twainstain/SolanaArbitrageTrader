# PolymarketTrader — Design Plan

> A latency arbitrage bot for Polymarket prediction markets, reusing
> infrastructure from ArbitrageTrader.

---

## What It Does

Polymarket offers 15-minute BTC/ETH/SOL up/down prediction markets. When the
spot price moves sharply on Binance, Polymarket's odds take 2-10 seconds to
adjust. The bot detects this lag and buys the correct outcome before the
market reprices.

**Example:** BTC jumps 0.5% on Binance in 10 seconds. Polymarket's "BTC Up"
token is still priced at $0.50 (50% probability) when it should be ~$0.75.
The bot buys "BTC Up" at $0.50, waits for the 15-minute window to close,
and collects $1.00 per share. Net profit: $0.50 per share minus fees.

---

## How It Differs from ArbitrageTrader

| Dimension | ArbitrageTrader | PolymarketTrader |
|-----------|----------------|-----------------|
| **Edge source** | Price difference between DEXes | Price lag between spot exchange and prediction market |
| **Data feeds** | RPC polling every 2-8 seconds | WebSocket real-time ticks (milliseconds) |
| **Execution** | Flash loans (atomic, zero capital risk) | CLOB limit/market orders (position risk) |
| **Profit model** | Deterministic: spread - costs | Probabilistic: payout if correct - entry price |
| **Holding period** | Zero (single atomic tx) | Up to 15 minutes (until market resolves) |
| **Capital at risk** | Gas only (flash loan repays itself) | Full trade amount ($4-5K per bet) |
| **Latency sensitivity** | Low (8-sec scan cycle is fine) | Critical (2-10 sec IS the edge) |
| **Chain** | Ethereum, Arbitrum, Base, Optimism | Polygon (Polymarket's chain) |

---

## Reusable Infrastructure from ArbitrageTrader

### Directly reusable (~60% of codebase)

| Component | Source | Adaptation needed |
|-----------|--------|-------------------|
| Pipeline lifecycle (6-stage) | `pipeline/lifecycle.py` | Replace stages 4-6 with CLOB execution |
| Risk policy (rule-based gate) | `risk/policy.py` | Add daily loss limit, settlement buffer, position concurrency |
| Circuit breaker | `risk/circuit_breaker.py` | Add trip on Polymarket API errors |
| Priority queue | `pipeline/queue.py` | Reuse as-is (heapq-based) |
| Alerting (email, Telegram, Discord) | `alerting/` | Reuse as-is |
| Dashboard (FastAPI + Revolut design) | `api/`, `dashboards/` | New views for positions, win rate, edge decay |
| Observability (metrics, latency, PnL) | `observability/` | Add position tracking metrics |
| Persistence (Postgres, repository) | `persistence/` | New tables for positions, settlements, market windows |
| Configuration (dataclass + JSON) | `core/config.py` | New config fields for strategy params |
| Logging | `observability/log.py` | Reuse as-is |

### Must be built new (~40%)

| Component | Purpose |
|-----------|---------|
| **BinanceFeed** | WebSocket BTC/ETH/SOL tick stream, price change calculation |
| **PolymarketFeed** | WebSocket order book + REST API, bid/ask/midpoint |
| **LatencyArbStrategy** | Signal detection: spot move → market lag → edge calculation |
| **ProbabilityModel** | Fair value estimation from spot momentum (needs backtesting) |
| **CLOBExecutor** | Order placement via py-clob-client SDK (market + limit orders) |
| **PositionManager** | Track open positions, holding period, forced exit before settlement |
| **MarketDiscovery** | Find active 15-min BTC/ETH/SOL markets, resolve token IDs |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    POLYMARKET LATENCY ARB BOT                 │
│                                                               │
│  ┌────────────────────┐     ┌──────────────────────────┐     │
│  │  BinanceFeed        │     │  PolymarketFeed           │     │
│  │  (WebSocket)        │     │  (WebSocket + REST)        │     │
│  │  BTC/ETH/SOL ticks  │     │  Order book updates        │     │
│  │  ~10 ticks/sec      │     │  Bid/ask/midpoint          │     │
│  └──────────┬──────────┘     └────────────┬──────────────┘     │
│             │                             │                    │
│             └──────────┬──────────────────┘                    │
│                        ▼                                       │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  LatencyArbStrategy                                     │   │
│  │                                                         │   │
│  │  1. Calculate spot price change (30s window)            │   │
│  │  2. Estimate fair probability from momentum             │   │
│  │  3. Compare fair value vs Polymarket price              │   │
│  │  4. If edge > threshold → generate signal               │   │
│  └────────────────────────┬───────────────────────────────┘   │
│                           ▼                                    │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  RiskPolicy + CircuitBreaker                            │   │
│  │  - Daily loss limit ($100 default)                      │   │
│  │  - Max concurrent positions (1)                         │   │
│  │  - Settlement buffer (no entry in last 60s)             │   │
│  │  - Spread filter (skip if bid-ask > 5 cents)            │   │
│  │  - Edge threshold (min 0.10 over fair value)            │   │
│  │  - Max daily trades (50)                                │   │
│  └────────────────────────┬───────────────────────────────┘   │
│                           ▼                                    │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  CLOBExecutor                                           │   │
│  │  - Market order (FOK) for speed                         │   │
│  │  - Limit order (GTC) for better fill                    │   │
│  │  - py-clob-client SDK on Polygon                        │   │
│  └────────────────────────┬───────────────────────────────┘   │
│                           ▼                                    │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  PositionManager                                        │   │
│  │  - Track open positions with entry price, edge, time    │   │
│  │  - Force exit before settlement window closes           │   │
│  │  - Record outcome when market resolves                  │   │
│  └────────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  Dashboard + Alerting + Persistence (reused from AT)    │   │
│  └────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

---

## Strategy Logic

### Signal detection

```
1. Binance WebSocket: BTC spot price ticks (~10/sec)
2. Calculate price_change_pct over 30-second rolling window
3. If |price_change_pct| >= 0.4%:
     → spot made a significant move
4. Read Polymarket UP token midpoint price
5. If spot moved UP and UP token price < 0.65:
     → market hasn't repriced yet
     → edge = fair_probability - current_price
6. If edge >= 0.10:
     → BUY signal
```

### Why 0.4% threshold?

A 0.4% BTC move in 30 seconds is statistically significant — it predicts the
15-minute direction with ~75% accuracy based on momentum continuation. Below
0.4%, the signal-to-noise ratio drops and the edge disappears into spread +
fees.

### Why 0.10 minimum edge?

Polymarket charges ~2% in fees (maker/taker). With a $0.50 entry and $1.00
payout, you need the fair probability to be at least $0.60 to net positive
after fees. The 0.10 edge threshold provides margin above breakeven.

---

## Risk Management

| Guard | Value | Purpose |
|-------|-------|---------|
| Daily loss limit | $100 | Stop trading after cumulative $100 loss |
| Max trade size | $20 (start), $4-5K (scaled) | Start small, scale after validation |
| Settlement buffer | 60 seconds | Don't enter in the last minute of a window |
| Max spread | $0.05 | Skip illiquid markets |
| Max concurrent | 1 position | One trade at a time (initially) |
| Max daily trades | 50 | Prevent overtrading |
| Edge threshold | 0.10 | Minimum edge to enter |
| Price range filter | 0.35-0.65 | Only trade when market hasn't already repriced |

---

## Data Model

### New tables (extends ArbitrageTrader schema)

```sql
-- Prediction market opportunities
CREATE TABLE pm_signals (
    signal_id TEXT PRIMARY KEY,
    asset TEXT,                    -- BTC, ETH, SOL
    direction TEXT,                -- UP, DOWN
    spot_price REAL,
    spot_change_pct REAL,
    polymarket_price REAL,
    fair_probability REAL,
    edge REAL,
    spread REAL,
    signal_time TEXT,              -- ISO UTC
    status TEXT                    -- signal, executed, expired, settled
);

-- Executed positions
CREATE TABLE pm_positions (
    position_id TEXT PRIMARY KEY,
    signal_id TEXT REFERENCES pm_signals,
    token_id TEXT,                 -- Polymarket token ID
    side TEXT,                     -- BUY_UP, BUY_DOWN
    entry_price REAL,
    amount_usdc REAL,
    shares REAL,
    entry_time TEXT,
    settlement_time TEXT,
    outcome TEXT,                  -- won, lost, expired
    payout REAL,
    net_pnl REAL
);

-- Market windows
CREATE TABLE pm_markets (
    market_id TEXT PRIMARY KEY,
    asset TEXT,
    window_start TEXT,
    window_end TEXT,
    up_token_id TEXT,
    down_token_id TEXT,
    resolution TEXT                -- up, down, null (pending)
);
```

---

## Project Structure

```
PolymarketTrader/
├── main.py                      # CLI entry point
├── config.yaml                  # Strategy parameters
├── .env                         # Secrets (wallet, API keys)
│
├── feeds/                       # NEW — real-time data
│   ├── binance.py               # WebSocket BTC/ETH/SOL ticks
│   └── polymarket.py            # WebSocket order book + REST
│
├── strategy/                    # NEW — signal detection
│   ├── latency_arb.py           # Core strategy logic
│   └── probability_model.py     # Fair value estimation
│
├── execution/                   # NEW — CLOB orders
│   ├── clob_executor.py         # py-clob-client wrapper
│   ├── position_manager.py      # Track open positions
│   └── paper_executor.py        # Simulated fills
│
├── core/                        # REUSED from ArbitrageTrader
│   ├── models.py                # Extend with PM-specific models
│   └── config.py                # Extend with PM config fields
│
├── pipeline/                    # REUSED
│   ├── lifecycle.py             # Same 6-stage pattern
│   └── queue.py                 # Same heapq priority queue
│
├── risk/                        # REUSED + extended
│   ├── policy.py                # Add PM-specific rules
│   └── circuit_breaker.py       # Add Polymarket API errors
│
├── persistence/                 # REUSED + extended
│   ├── db.py                    # Same DB layer
│   └── repository.py            # Add PM tables
│
├── observability/               # REUSED
│   ├── metrics.py
│   ├── log.py
│   └── latency_tracker.py
│
├── alerting/                    # REUSED
│   ├── gmail.py
│   ├── telegram.py
│   └── smart_alerts.py
│
├── api/                         # REUSED + new views
│   └── app.py
│
└── dashboards/                  # REUSED + new views
    └── pm_dashboard.py          # Positions, win rate, edge decay
```

---

## Key Differences from the $438K Bot

The guide describes a bot that made $438K in 30 days with a 98% win rate.
Important caveats:

1. **The edge may be closing.** Polymarket added dynamic fees in response to
   latency arb bots. The 2-10 second lag may now be smaller.

2. **92.4% of Polymarket wallets lost money.** Having the code doesn't
   guarantee profit — timing, execution quality, and parameter tuning matter.

3. **The $438K bot used $4-5K per trade.** Starting at $20 per trade is
   safer for validation. Scale only after proving the edge exists.

4. **VPS location matters.** The bot should run close to Polymarket's
   infrastructure (Dublin/London) for minimal latency.

5. **The strategy is momentum-based.** It works in trending markets but
   fails in choppy/sideways markets where 30-second moves reverse.

---

## Implementation Phases

### Phase 1: Validate the edge (1-2 weeks)
- Build BinanceFeed + PolymarketFeed
- Implement strategy with paper executor
- Run for 1-2 weeks, analyze results.csv
- Answer: does the 2-10 second lag still exist?

### Phase 2: Live execution with tiny size (1 week)
- Build CLOBExecutor with $20 max trade size
- Wire up risk management (daily loss limit $100)
- Run live for 1 week
- Answer: does paper trading profit translate to real profit?

### Phase 3: Scale (ongoing)
- Increase trade size gradually ($20 → $100 → $500 → $2K)
- Add ETH and SOL markets
- Deploy on low-latency VPS
- Add position manager with forced exit
- Build dashboard for real-time monitoring

---

## Dependencies

```
py-clob-client    # Polymarket SDK
websockets        # Binance + Polymarket WebSocket
aiohttp           # Async HTTP
pyyaml            # Config
python-dotenv     # Secrets
pandas            # Backtesting analysis
fastapi           # Dashboard (reused)
uvicorn           # Server (reused)
```

---

## Open Questions

1. **Does the latency edge still exist?** Polymarket's dynamic fees may have
   narrowed or eliminated the 2-10 second lag. Phase 1 answers this.

2. **What's the optimal probability model?** The guide uses a fixed 0.75 fair
   value. A momentum-based model using historical tick data would be more
   accurate. Needs backtesting.

3. **Cross-platform arbitrage?** Kalshi offers similar BTC prediction markets.
   If Polymarket and Kalshi disagree on the same outcome, there's a risk-free
   arb opportunity. Worth investigating.

4. **Regulatory risk?** Polymarket operates in a gray area. CFTC enforcement
   could impact access for US-based traders.
