"""Trading Platform — shared infrastructure for trading products.

Provides reusable components for building trading bots:
  - pipeline: 6-stage candidate lifecycle with priority queue
  - risk: rule-based evaluation framework with circuit breaker
  - alerting: multi-backend notifications (email, Telegram, Discord)
  - observability: metrics, latency tracking, logging
  - persistence: database layer (SQLite + Postgres)
  - api: FastAPI base app with auth, health, controls
  - config: environment and configuration loading
  - data: caching and failover utilities

Products (ArbitrageTrader, PolymarketTrader) import from here and
plug in their own strategy, execution, and market data layers.
"""

__version__ = "0.1.0"
