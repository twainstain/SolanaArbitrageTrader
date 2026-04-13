# Arbitrage Bot

A Python crypto arbitrage bot that detects cross-DEX price discrepancies and executes atomic flash-loan trades using ERC-20 tokens. Built following the [Dapp University video guide](https://www.youtube.com/watch?v=-PWyM6adiIE) and [ArbitrageScanner spec](../arbitrage_scanner_doc.md).

## How It Works

```
1. DISCOVER    DexScreener finds cross-DEX pairs by volume (--discover)
2. FETCH       Market source gets price quotes from each DEX on each chain
3. FILTER      Outlier quotes removed (bad data from low-liquidity pools)
4. EVALUATE    Strategy checks every buy-on-A / sell-on-B combination per pair
5. RANK        Scanner scores by profit, liquidity, risk flags
6. DECIDE      net profit > min threshold + no critical risk flags -> execute
7. EXECUTE     Paper mode (simulated) or on-chain (FlashArbExecutor contract)
8. LOG         Every quote, decision, and execution saved to logs/
```

On-chain execution is atomic: flash loan -> swap A -> swap B -> repay -> keep profit. If profit drops below threshold, the entire transaction reverts (only gas is lost). Transactions are simulated via eth_call before sending to avoid wasting gas.

## Quick Start

```bash
cd arbitrage_bot_trader

# Live pair discovery + scan across 12 chains (recommended)
PYTHONPATH=src python -m arbitrage_bot.main \
    --config config/live_config.json --live --dry-run --no-sleep \
    --iterations 3 --discover --discover-min-volume 100000

# Parse the results
PYTHONPATH=src python -m arbitrage_bot.log_parser --show-quotes

# Performance report across all runs
PYTHONPATH=src python -m arbitrage_bot.perf_tracker

# Simulated market (no network needed)
PYTHONPATH=src python -m arbitrage_bot.main --iterations 5 --no-sleep

# On-chain per-DEX quotes (Uniswap vs Sushi vs PancakeSwap)
PYTHONPATH=src python -m arbitrage_bot.main \
    --config config/onchain_config.json --onchain --dry-run --no-sleep --iterations 1

# Run tests
PYTHONPATH=src python -m pytest tests/ -v
```

## Architecture

```
arbitrage_bot_trader/
|-- config/                           # JSON configs for each mode
|   |-- example_config.json           # Simulated (3 DEXs, random walk, 3 pairs)
|   |-- live_config.json              # DeFi Llama (12 chains, 3 pairs)
|   |-- onchain_config.json           # web3.py RPC (4 DEX+chain combos)
|   |-- subgraph_config.json          # The Graph (2 DEXs)
|   |-- historical_config.json        # Backtesting with downloaded data
|   |-- uniswap_pancake_config.json   # Video's recommended DEX pair
|   +-- multi_pair_config.json        # WETH/USDC + WETH/USDT + WBTC/USDC
|-- contracts/
|   +-- FlashArbExecutor.sol          # Solidity: atomic flash loan + ERC-20 swaps
|-- src/arbitrage_bot/
|   |-- main.py                       # CLI entrypoint
|   |-- bot.py                        # Scan loop + outlier filter
|   |-- strategy.py                   # Cross-DEX evaluation with cost breakdown + risk flags
|   |-- scanner.py                    # Multi-factor ranking + alerting
|   |-- models.py                     # MarketQuote, Opportunity, ExecutionResult
|   |-- config.py                     # BotConfig, DexConfig, PairConfig
|   |
|   |  -- Market Sources (pluggable, same interface) --
|   |-- market.py                     # SimulatedMarket (multi-pair)
|   |-- live_market.py                # DeFi Llama (multi-pair, discovered addresses)
|   |-- onchain_market.py             # web3.py RPC (Uniswap/PancakeSwap/Sushi/Balancer)
|   |-- subgraph_market.py            # The Graph per-DEX pool prices
|   |-- historical_market.py          # Replay downloaded data
|   |
|   |  -- Executors --
|   |-- executor.py                   # PaperExecutor (simulated)
|   |-- chain_executor.py             # ChainExecutor (on-chain + tx simulation)
|   |
|   |  -- Tools --
|   |-- pair_scanner.py               # DexScreener pair discovery + bot bridge
|   |-- fork_scanner.py               # DeFiLlama fork/DEX discovery
|   |-- event_listener.py             # Swap event poller
|   |-- price_downloader.py           # Historical OHLC from The Graph
|   |-- show_prices.py                # DeFi Llama prices + exchange info
|   |-- log_parser.py                 # Parse and display JSONL logs
|   |-- perf_tracker.py               # Phase 4 performance analytics
|   |
|   |  -- Infrastructure --
|   |-- contracts.py                  # Contract addresses, ABIs, RPC URLs
|   |-- subgraphs.py                  # Subgraph IDs, pool addresses, GraphQL queries
|   |-- tokens.py                     # ERC-20 token addresses (12 chains)
|   |-- env.py                        # .env loader
|   +-- log.py                        # Logging: console + file + structured JSONL
|-- tests/                            # 202 tests
|-- logs/                             # Generated per run (.log + .jsonl)
+-- data/                             # Downloaded price history
```

## CLI Options

| Flag | Description |
|---|---|
| `--config FILE` | JSON config file (default: from .env) |
| `--iterations N` | Number of scan cycles |
| `--no-sleep` | Disable sleeping between scans |
| `--dry-run` | Log opportunities without executing |
| `--execute` | Real on-chain ERC-20 execution (requires wallet key) |
| `--discover` | Query DexScreener for live cross-DEX pairs at startup |
| `--discover-chain CHAIN` | Filter discovery to one chain (default: all) |
| `--discover-min-volume N` | Min 24h volume for discovery (default: 50000) |
| `--live` | DeFi Llama prices (12 chains) |
| `--onchain` | web3.py RPC to DEX contracts |
| `--subgraph` | The Graph per-DEX pool queries |
| `--historical FILE [...]` | Replay downloaded JSON data |

## Supported Chains (12)

| Chain | Token Registry | Live Config | RPC |
|---|---|---|---|
| Ethereum | WETH, WBTC, USDC, USDT | Yes | eth.llamarpc.com |
| Arbitrum | WETH, WBTC, USDC, USDT | Yes | arb1.arbitrum.io/rpc |
| Base | WETH, USDC | Yes | mainnet.base.org |
| BSC | WETH, WBTC, USDC, USDT | Yes | bsc-dataseed.binance.org |
| Polygon | WETH, WBTC, USDC, USDT | Yes | - |
| Optimism | WETH, WBTC, USDC, USDT | Yes | - |
| Avalanche | WETH, WBTC, USDC, USDT | Yes | - |
| Fantom | WETH, USDC, USDT | Yes | - |
| Linea | WETH, USDC | Yes | - |
| Scroll | WETH, USDC | Yes | - |
| zkSync | WETH, USDC | Yes | - |
| Gnosis | WETH, USDC, USDT | Yes | - |

## Supported DEXs

Uniswap V3, PancakeSwap V3, SushiSwap V3, Balancer V2 (on-chain mode). Any DEX on DexScreener (live discovery mode).

## Live Pair Discovery

The `--discover` flag queries DexScreener at startup, searching 15 popular tokens for cross-DEX pairs. Discovered pairs carry their token contract addresses from DexScreener, so DeFi Llama can price ANY token -- not just the hardcoded registry.

```bash
# Discover + scan (all chains)
PYTHONPATH=src python -m arbitrage_bot.main \
    --config config/live_config.json --live --dry-run --no-sleep \
    --iterations 3 --discover

# Discover on specific chain
PYTHONPATH=src python -m arbitrage_bot.main \
    --config config/live_config.json --live --dry-run --no-sleep \
    --iterations 3 --discover --discover-chain arbitrum
```

## Tools

```bash
# Pair scanner — find cross-DEX pairs
PYTHONPATH=src python -m arbitrage_bot.pair_scanner --recommended --chain ethereum
PYTHONPATH=src python -m arbitrage_bot.pair_scanner --token PEPE --min-volume 50000

# Fork scanner — find Uniswap-style DEXes via DeFiLlama
PYTHONPATH=src python -m arbitrage_bot.fork_scanner --chain Ethereum --min-tvl 50000000

# Event listener — real-time swap monitoring
PYTHONPATH=src python -m arbitrage_bot.event_listener --config config/uniswap_pancake_config.json --dry-run

# Price downloader — historical data for backtesting
PYTHONPATH=src python -m arbitrage_bot.price_downloader --dex uniswap_v3 --chain ethereum --days 7 --output data/uni_7d.json

# Show prices — DeFi Llama + exchange info
PYTHONPATH=src python -m arbitrage_bot.show_prices

# Log parser — read any log file
PYTHONPATH=src python -m arbitrage_bot.log_parser --show-quotes
PYTHONPATH=src python -m arbitrage_bot.log_parser logs/bot_2026-04-13_04-47-27.jsonl --show-quotes

# Performance tracker — analyze all runs
PYTHONPATH=src python -m arbitrage_bot.perf_tracker
PYTHONPATH=src python -m arbitrage_bot.perf_tracker --output data/perf_report.json
```

## On-Chain Execution

```bash
# 1. Deploy contracts/FlashArbExecutor.sol
# 2. Set in .env:
#    EXECUTOR_PRIVATE_KEY=0x...
#    EXECUTOR_CONTRACT=0x...
# 3. Run (tx simulated via eth_call before sending):
PYTHONPATH=src python -m arbitrage_bot.main \
    --config config/uniswap_pancake_config.json --onchain --execute
```

## Safety Features

- **Outlier filter** — removes quotes with >50% deviation from median (catches bad data from low-liquidity pools)
- **Transaction simulation** — eth_call dry-run before sending real tx
- **Minimum profit threshold** — rejects opportunities below min_profit_base
- **Risk flags** — low_liquidity, thin_market, stale_quote, high_fee_ratio
- **Atomic execution** — contract reverts if profit < minProfit (only gas lost)
- **Paper mode by default** — real execution requires explicit --execute flag

## Logging

Each run creates two files in `logs/`:

| File | Content |
|---|---|
| `bot_*.log` | Human-readable console mirror |
| `bot_*.jsonl` | Structured JSON: discovery, scan, execution, summary |

Parse with: `PYTHONPATH=src python -m arbitrage_bot.log_parser --show-quotes`

## Running Tests

```bash
PYTHONPATH=src python -m pytest tests/ -v
```

202 tests covering: config, all 5 market sources, strategy, scanner, outlier filter, multi-pair, multi-chain, pair scanner, fork scanner, event listener, chain executor, price downloader, historical replay, performance tracker, and log parser.

## Dependencies

```bash
pip install requests python-dotenv web3
```

## Design Alignment

| Video Recommendation | Implementation |
|---|---|
| Choose one EVM chain | 12 chains supported |
| Uniswap + PancakeSwap | Both + Sushi, Balancer |
| Research pairs (DexScreener) | --discover flag, pair_scanner.py |
| Flash-loan provider | Aave V3 + Balancer |
| Smart contract executor | FlashArbExecutor.sol + chain_executor.py |
| Event-driven searcher | event_listener.py |
| Transaction simulation | eth_call before sending |
| Track revert rate + PnL | perf_tracker.py |
| Check DeFiLlama forks | fork_scanner.py |

| Scanner Doc Requirement | Implementation |
|---|---|
| Cross-exchange spread scanner | strategy.py + scanner.py |
| Risk/warning flags | low_liquidity, thin_market, stale_quote, high_fee_ratio |
| Multi-factor ranking | profit + liquidity + flags + spread quality |
| Alerting layer | scanner.py with configurable thresholds |
| Fee and risk filter | Full cost breakdown on every opportunity |
