# Expanding Arbitrage Opportunities

Current state: 8 DEXes (Uniswap + SushiSwap), 4 chains, 1 pair (WETH/USDC).
Scans every ~4 seconds, 0 opportunities found — all V3 fork prices converge within 0.1%.

This doc covers what needs to change to find real, executable arbitrage opportunities.

---

## Why We're Not Finding Opportunities

**Root cause:** Uniswap V3 and SushiSwap V3 use identical AMM math (concentrated liquidity,
same fee tiers). Arbitrage bots already keep their prices within 1-2 basis points. Comparing
two V3 forks on the same chain is like comparing two copies of the same spreadsheet.

**What creates real spreads:**
1. Different AMM models (V2 vs V3, stable swap, weighted pools)
2. Different liquidity depths (one DEX has 10x more TVL)
3. Less competitive pairs (fewer bots watching)
4. Volatile events (large swaps, liquidations)

---

## Priority 1: Add Different AMM Types

These DEXes use fundamentally different pricing math from Uniswap V3:

| DEX | Chain | AMM Type | Why Spreads Exist | Status |
|-----|-------|----------|-------------------|--------|
| **Velodrome V2** | Optimism | Solidly (vAMM + sAMM) | Different curve, concentrated + stable pools | Code ready, returns zero for WETH/USDC — needs WETH/USDC.e or OP/USDC pair |
| **Aerodrome** | Base | Solidly fork | Largest TVL on Base, prices independently | Code ready, RPC call hangs — needs contract call debugging |
| **Camelot V3** | Arbitrum | Algebra (dynamic fees) | Auto-adjusting fees create different effective prices | Code ready, RPC call hangs — needs Algebra ABI fix |
| **Curve** | Multi-chain | StableSwap | Optimized for stablecoin pairs, different slippage curve | Not implemented — needs Curve registry + pool-specific ABI |
| **Balancer V2** | Ethereum | Weighted pools | 80/20, 50/50 weightings create different pricing | Partially implemented — needs pool ID resolution |
| **TraderJoe V2.1** | Avalanche | Liquidity Book | Bin-based pricing, not tick-based | Not implemented |

### Implementation plan for each:

**Velodrome V2** (highest priority — Optimism's largest DEX):
- Router: `0xa062aE8A9c5e11aaA026fc2670B0D65cCc8B2858`
- Issue: Returns zero for WETH/USDC because Velodrome uses bridged USDC.e, not native USDC
- Fix: Add USDC.e address for Optimism, or try WETH/OP pair instead
- Factory: `0x420DD381b31aEf6683db6B902084cB0FFECe40Da`

**Aerodrome** (highest priority — Base's largest DEX):
- Router: `0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43`
- Issue: `getAmountsOut` call hangs even with 8s HTTP timeout
- Fix: Debug the Route struct encoding — may need different factory address
- Factory: Try `0x420DD381b31aEf6683db6B902084cB0FFECe40Da` (same as Velodrome)

**Camelot V3** (Arbitrum's native DEX):
- Quoter: `0x0Fc73040b26E9bC8514fA028D998E73A254Fa76E`
- Issue: Uses Algebra protocol — `quoteExactInputSingle` has different params (no fee, has limitSqrtPrice)
- Fix: Uses same ABI as QuickSwap (already working on Polygon). May need to verify contract is responding.

---

## Priority 2: Dynamic Pair Discovery

### What's built

**PairRefresher** (`src/registry/pair_refresher.py`):
- Queries DexScreener every hour for top pairs by volume + multi-DEX presence
- Currently discovers 7+ pairs: ARB/USDC, LINK/WETH, USDT/USDC, WETH/USDT, etc.
- Auto-registers token addresses in the dynamic token registry
- Thread-safe, runs as background daemon thread

**Dynamic Token Registry** (`src/tokens.py`):
- Static registry: WETH, USDC, USDT, WBTC, ARB, OP, LINK, DAI, UNI, AAVE, CRV, GMX
- Dynamic registry: tokens from DexScreener auto-registered at runtime
- Unresolved tokens tracked for debugging via `get_unresolved_tokens()`

### What's missing

The on-chain quoter (`OnChainMarket`) only quotes the **primary pair** from the config.
Discovered pairs are added to `config.extra_pairs` but the quoter doesn't iterate over them.

**Fix needed in `run_event_driven.py`:**
```python
# Current: one market, one pair
market = OnChainMarket(config, ...)

# Needed: scan each discovered pair
for pair_config in all_pairs:
    pair_market = OnChainMarket(pair_config, ...)
    quotes = pair_market.get_quotes()
    # ... scan for opportunities on this pair
```

Or better: make `OnChainMarket.get_quotes()` accept a pair parameter:
```python
quotes = market.get_quotes(base="ARB", quote="USDC")
```

### High-value pairs to scan

| Pair | Chains | Why |
|------|--------|-----|
| ARB/USDC | Arbitrum | High volume, Camelot vs Uniswap spread likely |
| OP/USDC | Optimism | Velodrome vs Uniswap spread likely |
| WETH/USDT | All | Different USDT liquidity per DEX |
| USDT/USDC | Base, Ethereum | Stablecoin peg deviations (Curve vs V3) |
| LINK/WETH | Arbitrum, Ethereum | Mid-cap, less bot competition |
| WBTC/USDC | Ethereum, Arbitrum | BTC wrapper with variable premiums |
| GMX/WETH | Arbitrum | Camelot has deepest GMX liquidity |

---

## Priority 3: Fix RPC Reliability

### Issues encountered

| Issue | Root Cause | Impact | Resolution |
|-------|-----------|--------|------------|
| **Scans hang indefinitely** | `web3.py` `eth_call` ignores HTTP timeout | Bot never completes first scan | Added `concurrent.futures.wait(timeout=15)` + `pool.shutdown(wait=False)` |
| **SushiSwap thin pool** | Sushi-Optimism WETH/USDC has $5K liquidity, returns $2152 vs market $2364 | 9.6% fake spread, false positives | 5% global median outlier filter |
| **BSC WETH decimal mismatch** | BSC WETH is bridged with 18 decimals but quoter returns raw value without scaling | Returns $2.3 quadrillion | Need to use WBNB/USDT pair on BSC instead |
| **Polygon all zeros** | Public RPC rate limits; Alchemy Polygon key not configured | All 4 Polygon DEXes return zero | Add Alchemy Polygon RPC key |
| **Avalanche thin pools** | Uniswap V3 on Avax has very low WETH/USDC liquidity | 89.9% price impact at 10 WETH | Cache and skip; Avax not viable for WETH/USDC |
| **PancakeSwap quoter hangs** | PancakeSwap V3 quoter contract on some chains has very slow eth_call | Blocks thread pool | Disabled; use the 15s hard deadline + 15min cache |
| **Infura rate limiting** | Infura free tier limits concurrent calls | Optimism RPC hangs | Use Alchemy for all chains |
| **Velodrome zero quotes** | Uses bridged USDC.e, not native USDC | Router returns 0 amounts | Use USDC.e address or different pair |
| **Camelot/Aerodrome hangs** | Algebra/Solidly quoter calls hang on eth_call | Never returns | Debug ABI encoding; may need different function signature |

### Recommended RPC setup

| Chain | Provider | Status |
|-------|----------|--------|
| Ethereum | Alchemy (configured) | Working |
| Arbitrum | Alchemy (configured) | Working |
| Base | Alchemy (configured) | Working |
| Optimism | **Needs Alchemy** (currently Infura, hangs) | Blocked |
| Polygon | **Needs Alchemy** (currently using Base key, wrong) | Blocked |
| BSC | Public RPCs only | Use for WBNB pairs only |
| Avalanche | Public RPCs only | Low liquidity, skip |

**Action:** Create Alchemy apps for Optimism and Polygon, update `.env`.

---

## Priority 4: Scan Speed + Latency

### Current performance

- Scan interval: 8 seconds
- Quotes per scan: 8 (7 after outlier removal)
- Pipeline latency: ~10-30ms (detect → price → risk)
- RPC fetch: ~2-5 seconds per chain (Alchemy)

### Optimizations available

| Optimization | Impact | Effort |
|-------------|--------|--------|
| **Fee tier caching** | Skip 3 of 4 fee tier calls after first scan → 75% fewer RPC calls | Done (autoresearch) |
| **WebSocket subscriptions** | React to swap events instead of polling → sub-second latency | High effort |
| **Batch RPC calls** | `eth_call` batching via JSON-RPC batch → fewer HTTP round-trips | Medium |
| **Reduce poll interval** | 8s → 2s for high-volatility periods | Config change |
| **Async web3** | Use `web3.py` async provider → non-blocking I/O | Medium |

### Autoresearch agent

An autonomous optimization agent exists at `/Users/tamir.wainstain/src/autoresearch_arb_trader/`.
It reads `program.md`, modifies ArbitrageTrader code, measures latency, keeps/discards changes.

Run: `cd /Users/tamir.wainstain/src/autoresearch_arb_trader && claude`
Then: "Read program.md and kick off a new experiment!"

---

## Summary: Roadmap to Real Opportunities

### Phase 1 — Quick wins (hours)
- [ ] Add Alchemy keys for Optimism + Polygon
- [ ] Fix Velodrome: use USDC.e address or WETH/OP pair
- [ ] Debug Aerodrome Route struct encoding
- [ ] Make quoter scan extra_pairs (ARB/USDC, OP/USDC, LINK/WETH)

### Phase 2 — More DEXes (days)
- [ ] Fix Camelot Algebra quoter on Arbitrum
- [ ] Add Curve StableSwap pools (USDT/USDC, DAI/USDC)
- [ ] Add Balancer V2 weighted pool support
- [ ] Test with BSC WBNB/USDT pairs via PancakeSwap

### Phase 3 — Speed (weeks)
- [ ] WebSocket swap event subscriptions (replace polling)
- [ ] Batch RPC calls
- [ ] Sub-second scan cycle
- [ ] MEV protection (Flashbots bundle submission)

### Phase 4 — Execution (requires capital + approval)
- [ ] Deploy FlashArbExecutor.sol on target chains
- [ ] Enable live execution (POST /execution {enabled: true})
- [ ] Monitor realized vs expected profit
- [ ] Tune slippage + gas parameters per chain
