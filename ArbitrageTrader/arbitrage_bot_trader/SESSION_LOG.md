# Session Log — Arbitrage Bot Development

## Commands Used During This Session

### Running the Bot

```bash
cd /Users/tamir.wainstain/src/ArbitrageTrader/arbitrage_bot_trader

# Simulated market (3 DEXs, 3 pairs)
PYTHONPATH=src python -m arbitrage_bot.main --iterations 3 --no-sleep

# Live DeFi Llama prices (static pairs from config)
PYTHONPATH=src python -m arbitrage_bot.main --config config/live_config.json --live --dry-run --no-sleep --iterations 3

# Live with pair discovery from DexScreener (10 pairs, 12 chains)
PYTHONPATH=src python -m arbitrage_bot.main --config config/live_config.json --live --dry-run --no-sleep --iterations 3 --discover --discover-min-volume 100000

# On-chain RPC per-DEX quotes (Uniswap vs Sushi on Ethereum/Arbitrum)
PYTHONPATH=src python -m arbitrage_bot.main --config config/onchain_config.json --onchain --dry-run --no-sleep --iterations 1

# On-chain with live discovery
PYTHONPATH=src python -m arbitrage_bot.main --config config/onchain_config.json --onchain --dry-run --no-sleep --iterations 1 --discover --discover-min-volume 100000
```

### Tools

```bash
# Pair scanner — DexScreener cross-DEX pair discovery
PYTHONPATH=src python -m arbitrage_bot.pair_scanner --recommended --chain ethereum
PYTHONPATH=src python -m arbitrage_bot.pair_scanner --token ETH --min-volume 50000
PYTHONPATH=src python -m arbitrage_bot.pair_scanner --token USDC --min-volume 100000

# Fork scanner — DeFiLlama DEX discovery
PYTHONPATH=src python -m arbitrage_bot.fork_scanner --chain Ethereum --min-tvl 50000000
PYTHONPATH=src python -m arbitrage_bot.fork_scanner --parent "Uniswap" --min-tvl 10000000

# Show live prices
PYTHONPATH=src python -m arbitrage_bot.show_prices

# Download historical data
PYTHONPATH=src python -m arbitrage_bot.price_downloader --dex uniswap_v3 --chain ethereum --days 1 --output data/test_uni_eth_1d.json
PYTHONPATH=src python -m arbitrage_bot.price_downloader --dex sushi_v3 --chain ethereum --days 1 --output data/test_sushi_eth_1d.json
PYTHONPATH=src python -m arbitrage_bot.price_downloader --dex uniswap_v3 --chain arbitrum --days 1 --output data/test_uni_arb_1d.json

# Parse logs
PYTHONPATH=src python -m arbitrage_bot.log_parser --show-quotes
PYTHONPATH=src python -m arbitrage_bot.log_parser --opportunities-only
PYTHONPATH=src python -m arbitrage_bot.log_parser logs/bot_2026-04-13_04-47-27.jsonl --show-quotes

# Performance tracker
PYTHONPATH=src python -m arbitrage_bot.perf_tracker
PYTHONPATH=src python -m arbitrage_bot.perf_tracker --output data/perf_report.json
```

### Tests

```bash
PYTHONPATH=src python -m pytest tests/ -v
PYTHONPATH=src python -m pytest tests/ --tb=short -q
```

## Key Findings from Live Runs

### DeFi Llama (aggregated prices, 12 chains)
- Cross-chain spreads: ~0.03-0.08% (WETH/USDC across Ethereum/Base/Arbitrum)
- Cost floor: ~0.54% (30bps fees + 9bps flash + 15bps slippage)
- Result: No profitable opportunities (spreads too tight)
- Best observed: Linea vs Scroll WETH/USDC spread of +0.38% (still below cost floor)

### On-chain RPC (per-DEX quotes)
- Uniswap-Ethereum: $2,191.05 | Sushi-Ethereum: $2,190.24 | Uniswap-Arbitrum: $2,190.40
- Real per-DEX spread: ~$0.81 (0.037%) between Uniswap and Sushi
- Sushi-Arbitrum returned $39.77 (bad data) — caught by outlier filter
- Result: No profitable opportunities after fees

### Live Discovery (DexScreener)
- Discovered 10 cross-DEX pairs across 15 token searches
- Top pairs by volume: USDC/WHYPE ($34M), WETH/USDT ($14.5M), WETH/WBNB ($7.7M)
- 28 quotes generated across 8 pairs and 12 chains (up from 7 quotes/3 pairs initially)

## Build Progress

| Phase | Status | Tests |
|---|---|---|
| Phase 1: Quote-only searcher | Done | 202 |
| Phase 2: Simulation layer | Done | - |
| Phase 3: On-chain executor | Written, not deployed | - |
| Phase 4: Performance tracking | Done | - |

## Files Created/Modified

### Source files (25)
bot.py, chain_executor.py, config.py, contracts.py, env.py, event_listener.py,
executor.py, fork_scanner.py, historical_market.py, live_market.py, log.py,
log_parser.py, main.py, market.py, models.py, onchain_market.py, pair_scanner.py,
perf_tracker.py, scanner.py, show_prices.py, strategy.py, subgraph_market.py,
subgraphs.py, tokens.py, price_downloader.py

### Test files (15)
test_bot.py, test_chain_executor.py, test_config.py, test_event_listener.py,
test_executor.py, test_fork_scanner.py, test_historical_market.py,
test_live_market.py, test_market.py, test_multi_pair.py, test_onchain_market.py,
test_outlier_filter.py, test_pair_scanner.py, test_perf_tracker.py,
test_price_downloader.py, test_scanner.py, test_strategy.py,
test_subgraph_market.py

### Config files (7)
example_config.json, historical_config.json, live_config.json,
multi_pair_config.json, onchain_config.json, subgraph_config.json,
uniswap_pancake_config.json

### Other
FlashArbExecutor.sol, .env, .env.example, .gitignore, README.md, SESSION_LOG.md
