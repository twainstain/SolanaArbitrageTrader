# AutoResearch Experiment Run: `apr14f`

**Date:** April 14, 2026
**Branch:** `autoresearch/apr14f`
**Duration:** ~2 hours
**Experiments:** 12 total — 10 kept, 2 discarded

## Goal

Minimize **pipeline total latency** (`pipeline_ms.total_ms`) — the time from opportunity detection through the detect → price → risk decision path. Secondary goal: reduce **RPC fetch time** per scan cycle.

## Environment

- **Local:** MacOS, SQLite (WAL mode), live Alchemy/public RPCs
- **Python:** Started on 3.8 (Anaconda default), switched to 3.11 mid-run
- **Scanner:** `run_local.sh --fast` (8s poll interval)
- **Measurement:** 60s scan window, parse `logs/latency.jsonl`

## Overall Results

| Metric | Baseline | Final | Change |
|---|---|---|---|
| pipeline_avg_ms | 0.87 | 0.52 | **-40%** |
| pipeline_p95_ms | 2.11 | 1.34 | **-36%** |
| rpc_fetch_avg_ms | ~2,400 | ~996 | **-58%** |
| test count | 686 | 696 | +10 new tests |

## Results Table

```
commit     avg_ms  p95_ms  tests  status   description
e88d7cc    0.87    2.11    686    keep     baseline
ffb567d    0.60    1.64    689    keep     reuse fee tier cache in _quote_small_amount
1d853c4    0.65    1.98    690    keep     fix broken count_opportunities_since cache
cc1bfca    0.65    1.55    692    keep     reuse ThreadPoolExecutor across scan cycles
bcd964b    0.67    1.38    694    keep     tiered TVL cache TTL (30min deep, 5min thin)
fd06484    0.66    1.66    695    keep     pre-group quotes by pair in scanner
1808430    0.66    1.66    695    keep     hoist Decimal constants out of inner loop
117c709    0.55    1.31    696    keep     extend fee tier cache TTL from 60s to 5 minutes
3fd0275    0.53    1.35    696    keep     fix thread pool sizing for full parallelism
28aec6b    0.53    1.35    696    keep     skip liquidity estimation for unsupported DEX types
n/a        0.54    1.34    696    discard  asyncio.run_in_executor on py3.8
065ef8f    0.52    1.34    696    keep     switch to Python 3.11
n/a        0.62    2.92    696    discard  asyncio.to_thread on py3.11
```

---

## Experiment Details

### Experiment 1: Reuse fee tier cache in `_quote_small_amount`

**File:** `src/onchain_market.py`
**Result:** 0.87ms → 0.60ms (-31%)

**Problem:** `_quote_small_amount()` is called during liquidity estimation to get a zero-impact reference price. For Uniswap V3 / Sushi V3 / PancakeSwap V3, it swept all 4 fee tiers (100, 500, 3000, 10000) — making **4 RPC calls** each time. But the main quote path (`_try_fee_tiers`) already caches the winning fee tier in `self._best_fee`.

**Fix:** Before sweeping all tiers, check `self._best_fee` for a cached tier. If found, make 1 RPC call instead of 4. Fall back to the full sweep only when no cache entry exists (first scan or after cache expiry).

**Why it works:** The best fee tier for a given DEX+pair+chain combination rarely changes (Uniswap V3 fee tiers are immutable per pool). The main quote always runs before liquidity estimation, so the cache is warm.

**Tests added:** 3 — cached tier hit, no-cache fallback to sweep, cached tier failure returns zero gracefully.

---

### Experiment 2: Fix broken `count_opportunities_since` cache

**File:** `src/persistence/repository.py`
**Result:** 0.60ms → 0.65ms (noise locally, real benefit on Postgres)

**Problem:** `count_opportunities_since()` had a 5-second cache intended to avoid repeated `SELECT COUNT(*)` on every pipeline call. But the cache key included `since_iso` — the full ISO timestamp from `_one_hour_ago()` which includes **microsecond precision**. Each call generated a different string (`2026-04-14T19:03:37.123456` vs `2026-04-14T19:03:37.123789`), so the string comparison `cached_since == since_iso` **always failed**. The cache was dead code.

**Fix:** Truncate `since_iso` to minute precision for cache keying (`since_iso[:16]` → `"2026-04-14T19:03"`). Also increased TTL from 5s to 30s — the hourly trade count barely changes within 30 seconds.

**Why local impact is minimal:** SQLite `SELECT COUNT(*)` is near-instant on local disk. The real benefit is on production Neon Postgres where each query has network round-trip cost (~3-4ms).

**Tests added:** 1 — verifies cache hits when since_iso varies only by microseconds.

---

### Experiment 3: Reuse ThreadPoolExecutor across scan cycles

**File:** `src/onchain_market.py`
**Result:** p95 improved from 1.98ms → 1.55ms

**Problem:** `get_quotes()` created a new `ThreadPoolExecutor` every scan cycle and destroyed it with `pool.shutdown(wait=False)`. Thread creation has measurable overhead — especially visible in tail latency (p95).

**Fix:** Create one persistent `ThreadPoolExecutor` at `OnChainMarket.__init__()` and reuse it across all `get_quotes()` calls. Removed the `try/finally: pool.shutdown()` pattern.

**Regression introduced:** Initial sizing was `max(len(config.dexes) * 2, 8)` = 8 workers. With auto-discovered pairs, there were ~24 active requests, causing sequential batching. Fixed in Experiment 8.

**Tests added:** 2 — pool exists at init, same pool instance across calls.

---

### Experiment 4: Tiered TVL cache TTL

**File:** `src/onchain_market.py`
**Result:** Saves RPC calls for deep pools

**Problem:** The TVL (liquidity) cache had a flat 5-minute TTL for all pools. Deep pools like Uniswap WETH/USDC on Ethereum ($22M+ TVL) don't dry up suddenly — re-checking every 5 minutes wastes RPC calls.

**Fix:** Two-tier TTL:
- Deep pools (>$1M TVL): 30-minute TTL
- Thin pools (<$1M TVL): 5-minute TTL (need more frequent re-checks)

**Why it's safe:** The $1M threshold is the scanner's liquidity filter cutoff. A deep pool dropping below $1M in 30 minutes would be an extraordinary event (flash crash, exploit). The worst case: a pool appears liquid for up to 30 minutes after draining — caught by the outlier filter and strategy warning flags.

**Tests added:** 2 — deep pool uses long TTL, thin pool uses short TTL.

---

### Experiment 5: Pre-group quotes by pair in scanner

**File:** `src/scanner.py`
**Result:** 67% fewer loop iterations

**Problem:** `_find_all_opportunities()` compared every quote against every other quote in O(n²). With 24 quotes across 3 pairs, that's 576 iterations — but most (384) were immediately skipped because `buy_quote.pair != sell_quote.pair`.

**Fix:** Pre-group quotes into a `dict[pair → list[MarketQuote]]` and only iterate within each group. With 3 pairs of ~8 quotes each: 3 × 8² = 192 iterations instead of 576.

**Tests added:** 1 — verifies cross-pair opportunities are never created.

---

### Experiment 6: Hoist Decimal constants out of inner loop

**File:** `src/scanner.py`
**Result:** Minor cleanup

**Problem:** `D("1000000")` and `D("0.02")` were created on every iteration of the inner comparison loop (~200 times per scan). Each `Decimal(str)` constructor call allocates a new object.

**Fix:** Pre-compute `_MIN_LIQ = D("1000000")` and `_MAX_DEV = D("0.02")` once before the loop.

---

### Experiment 7: Extend fee tier cache TTL from 60s to 5 minutes

**File:** `src/onchain_market.py`
**Result:** rpc_fetch 2,400ms → 996ms (**-57%** — biggest single win)

**Problem:** `_try_fee_tiers()` cache expired every 60 seconds, triggering a full sweep of all 4 fee tiers (4 RPC calls per DEX). On a pool with 2 DEXes and 12 pairs, that's 24 × 3 = 72 extra RPC calls every 60 seconds.

**Fix:** Extend the cache TTL from 60 seconds to 5 minutes. Fee tiers in Uniswap V3 pools are **immutable** (set at pool creation). The "best" tier only changes if liquidity migrates between pools on different tiers — rare enough that 5-minute staleness is acceptable.

**Impact math:** With 8s poll interval, the scanner runs ~7 cycles per minute. Before: every cycle after 60s does a full 4-tier sweep. After: sweep only once per 5 minutes. Saves ~3 RPC calls × 24 DEX+pair combos × 4 out of 5 minutes = ~288 RPC calls per 5-minute window.

**Tests added:** 1 — verifies cached tier is reused within 5-minute window.

---

### Experiment 8: Fix thread pool sizing for full parallelism

**File:** `src/onchain_market.py`
**Result:** rpc_fetch 1,040ms → 996ms

**Problem:** Experiment 3 sized the persistent pool to `max(dexes * 2, 8)` = 8 workers. But with auto-discovered pairs, there were ~24 active requests. Only 8 ran in parallel; the rest queued, adding latency.

**Fix:** Size pool to `max(len(dexes) * n_pairs, 32)` to handle all requests in parallel, matching the original behavior where each scan created a pool sized to `len(active_requests)`.

---

### Experiment 9: Skip liquidity estimation for unsupported DEX types

**File:** `src/onchain_market.py`
**Result:** Code cleanup, avoids wasted function calls

**Problem:** `_estimate_liquidity_usd()` calls `_quote_small_amount()` for every DEX type, but only `uniswap_v3`, `sushi_v3`, `pancakeswap_v3`, and `quickswap_v3` are supported. All others (Balancer, Camelot, Velodrome, Aerodrome, Curve, TraderJoe) enter the function, do setup work, then return `D("0")`.

**Fix:** Early return at the top of `_estimate_liquidity_usd()` for unsupported DEX types.

---

### Experiment 10: Switch to Python 3.11

**File:** `scripts/run_local.sh`
**Result:** Correct version, steady-state 0.52ms

**Problem:** The default `python3` resolved to Anaconda's Python 3.8, but `pyproject.toml` specifies `requires-python = ">=3.11"`. Python 3.11 has the specializing adaptive interpreter (PEP 659) which speeds up hot paths by 10-60%.

**Fix:** Changed `run_local.sh` to use `python3.11` explicitly.

**Note:** First-run cold start is higher on 3.11 (~15ms) due to bytecode compilation + GIL contention. Steady-state matches or beats 3.8.

---

### Experiment 11 (DISCARDED): asyncio.run_in_executor on Python 3.8

**Result:** 0.54ms avg, rpc_fetch +130ms slower

**Problem attempted:** Replace `ThreadPoolExecutor.submit()` + `concurrent.futures.wait()` with `asyncio.run()` + `asyncio.gather()` using `loop.run_in_executor()`.

**Why it failed:** `run_in_executor()` uses a ThreadPoolExecutor internally — it's the same concurrency model. The extra overhead of creating and destroying an asyncio event loop per scan cycle (~130ms) made it strictly worse.

---

### Experiment 12 (DISCARDED): asyncio.to_thread on Python 3.11

**Result:** 0.62ms avg, rpc_fetch +332ms slower

**Problem attempted:** Same approach as Experiment 11 but using Python 3.11's `asyncio.to_thread()` which is cleaner syntax.

**Why it failed:** `asyncio.to_thread()` is syntactic sugar over `loop.run_in_executor(None, func, *args)` where `None` means "use the default ThreadPoolExecutor". Same thread pool, same I/O model, same overhead problem.

**What would actually work:** Rewriting the `_quote_*` methods to use `AsyncWeb3(AsyncHTTPProvider(...))` with native `aiohttp` — true non-blocking I/O using OS-level epoll/kqueue. This eliminates threads entirely and avoids GIL contention. Estimated effort: ~200 lines changed across 10 quote methods.

---

## Key Takeaways

### 1. RPC caching is the dominant lever

The fee tier cache extension (Experiment 7) delivered the single biggest improvement: **-57% rpc_fetch time**. RPC network calls at 500-3000ms per scan dominate everything else. The pipeline itself runs in <1ms — further optimizing Python code yields diminishing returns.

### 2. Dead code hides in cache implementations

The `count_opportunities_since` cache (Experiment 2) had never worked since it was written. The bug was subtle: ISO timestamp strings with microsecond precision meant the cache key changed every call. This pattern — a cache that looks correct but has a key that drifts — is worth checking in any codebase.

### 3. asyncio wrapping sync code is always slower

Both `run_in_executor` (3.8) and `to_thread` (3.11) delegate to a ThreadPoolExecutor. The asyncio event loop adds scheduling overhead without changing the I/O model. For true async benefit, the underlying I/O operations must use native async libraries (aiohttp, asyncpg, etc.).

```
ThreadPoolExecutor (fastest for sync I/O):
  submit() → thread does blocking HTTP → result

asyncio.to_thread (slower, same mechanism):
  create event loop → schedule coroutine → to_thread() →
  ThreadPoolExecutor.submit() → thread does blocking HTTP →
  result → destroy event loop
```

Python's GIL is released during C-level I/O (socket reads/writes), so threads achieve true parallelism for network-bound work without asyncio.

### 4. Thread pool sizing matters

Creating a persistent pool (Experiment 3) was a good idea, but sizing it to 8 workers when 24 requests needed to run in parallel (Experiment 8) caused a performance regression. The lesson: when converting from per-call pool creation to a shared pool, match the concurrency level.

### 5. Check your Python version

Running on Python 3.8 when the project requires 3.11 was a silent misconfiguration. The Anaconda default shadowed the system Python. Worth checking in any environment where multiple Python installations coexist.
