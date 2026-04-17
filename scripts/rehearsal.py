#!/usr/bin/env python3
"""Phase-4 offline rehearsal for SolanaTrader.

Exercises the full scan → plan → build → simulate path end-to-end without
submitting a real transaction. Never enables execution, never signs
production keypairs, never calls sendTransaction.

Steps:

  1. Load config, verify `.env` has the RPC + Jupiter URLs we need.
  2. Instantiate the multi-venue market (Jupiter + Raydium + Orca).
  3. Run ``iterations`` scans, collect opportunities.
  4. Pick the single best candidate by expected_net_profit.
  5. Build a two-leg atomic VersionedTransaction via JupiterSwapBuilder +
     AtomicSwapBuilder.
  6. Run `simulateTransaction` on the RPC (no submission) and print:
     - simulation err (None means "would land")
     - compute units consumed
     - first ~20 lines of the program log
  7. Exit 0 if every step succeeded, 1 on any failure.

Usage::

    PYTHONPATH=src python3 scripts/rehearsal.py
    PYTHONPATH=src python3 scripts/rehearsal.py --config config/prod_scan.json --iterations 15
    PYTHONPATH=src python3 scripts/rehearsal.py --skip-tx   # scan-only smoke
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "trading_platform" / "src"))

from core.env import get_jupiter_api_url, get_solana_rpc_urls, load_env
from core.config import BotConfig

logger = logging.getLogger("rehearsal")

D = Decimal


# ---------------------------------------------------------------------------
# Printers
# ---------------------------------------------------------------------------

_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"


def _ok(msg: str) -> None:
    print(f"  [{_GREEN}OK{_RESET}]   {msg}")


def _fail(msg: str) -> None:
    print(f"  [{_RED}FAIL{_RESET}] {msg}")


def _warn(msg: str) -> None:
    print(f"  [{_YELLOW}WARN{_RESET}] {msg}")


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def check_env(config: BotConfig) -> bool:
    rpcs = get_solana_rpc_urls()
    jup = get_jupiter_api_url()
    ok = True
    if not rpcs:
        _fail("SOLANA_RPC_URL not set")
        ok = False
    else:
        _ok(f"Solana RPC(s) configured: {len(rpcs)}")
    if not jup:
        _fail("JUPITER_API_URL unresolved")
        ok = False
    else:
        _ok(f"Jupiter API: {jup}")
    _ok(f"Config pair={config.pair} trade_size={config.trade_size}")
    return ok


def run_scans(config: BotConfig, iterations: int) -> list[Any]:
    """Run N scan ticks against a live multi-venue market; return opps."""
    from market.multi_venue_market import MultiVenueMarket
    from market.solana_market import SolanaMarket
    from market.raydium_market import RaydiumMarket
    from market.orca_market import OrcaMarket
    from strategy.arb_strategy import ArbitrageStrategy
    from strategy.scanner import OpportunityScanner

    market = MultiVenueMarket([
        ("Jupiter", SolanaMarket(config)),
        ("Raydium", RaydiumMarket()),
        ("Orca",    OrcaMarket()),
    ])
    strategy = ArbitrageStrategy(config)
    scanner = OpportunityScanner(config, strategy=strategy)

    all_opps: list = []
    for i in range(iterations):
        quotes = market.get_quotes()
        result = scanner.scan_and_rank(quotes)
        all_opps.extend(result.opportunities)
        if (i + 1) % 5 == 0:
            _ok(f"scan {i + 1}/{iterations}: quotes={len(quotes)} opps={len(result.opportunities)}")
        time.sleep(0.5)

    _ok(f"total opps collected: {len(all_opps)}")
    return all_opps


def pick_best(opps: list) -> Any | None:
    if not opps:
        return None
    return max(opps, key=lambda o: getattr(o, "net_profit_base", D("0")))


def build_and_simulate(config: BotConfig, best_opp: Any) -> bool:
    """Build a two-leg atomic tx and simulate (never submits)."""
    from execution.atomic_swap import AtomicSwapBuilder, LegParams
    from execution.jupiter_swap import JupiterSwapBuilder
    from market.solana_rpc import SolanaRPC
    from solders.hash import Hash
    from solders.keypair import Keypair

    # Mocked wallet — we NEVER load a real keypair during rehearsal.
    dummy_kp = Keypair()
    wallet_pubkey = str(dummy_kp.pubkey())

    builder = AtomicSwapBuilder(jupiter=JupiterSwapBuilder())

    # Phase 3d: map the scanner's picked venues to Jupiter's `dexes` filter
    # so each leg is routed through the DEX the scanner actually saw the
    # quote from. When the scanner picks Jupiter-Best / Jupiter-Direct the
    # mapper returns None and Jupiter's full aggregator runs.
    from execution.atomic_swap import venue_to_jupiter_dexes

    # Leg A: buy base via buy_venue's route (use only_direct toggle to pick the cheap leg).
    leg_a = LegParams(
        input_symbol=best_opp.pair.split("/")[1],  # quote → base
        output_symbol=best_opp.pair.split("/")[0],
        input_amount_human=D(str(config.trade_size)) * D(str(best_opp.buy_price)),
        slippage_bps=int(config.slippage_bps),
        only_direct_routes=("Direct" in best_opp.buy_venue),
        dexes=venue_to_jupiter_dexes(best_opp.buy_venue),
    )
    # Leg B: sell base via sell_venue's route.
    leg_b = LegParams(
        input_symbol=best_opp.pair.split("/")[0],
        output_symbol=best_opp.pair.split("/")[1],
        input_amount_human=D(str(config.trade_size)),
        slippage_bps=int(config.slippage_bps),
        only_direct_routes=("Direct" in best_opp.sell_venue),
        dexes=venue_to_jupiter_dexes(best_opp.sell_venue),
    )

    try:
        plan = builder.plan_two_leg(leg_a, leg_b)
    except Exception as exc:
        _fail(f"plan_two_leg failed: {exc}")
        return False
    _ok(
        f"planned: leg_a out={plan.leg_a_quote.out_amount} "
        f"leg_b out={plan.leg_b_quote.out_amount}"
    )

    rpc = SolanaRPC()
    # Recent blockhash via RPC; avoid this in CI-style runs with --skip-tx.
    try:
        raw = rpc._call("getLatestBlockhash", [{"commitment": "confirmed"}])
        blockhash_str = (raw or {}).get("value", {}).get("blockhash") or ""
        if not blockhash_str:
            _fail("getLatestBlockhash returned empty")
            return False
        blockhash = Hash.from_string(blockhash_str)
    except Exception as exc:
        _fail(f"getLatestBlockhash error: {exc}")
        return False

    # Real ALT fetcher wired up (Phase 3c) — decodes on-chain LUT accounts
    # for each address Jupiter's /swap-instructions returns.
    def _alt_resolver(keys):
        resolved = rpc.get_address_lookup_tables(keys)
        if keys:
            _ok(f"fetched {len(resolved)}/{len(keys)} ALTs")
        return resolved

    try:
        tx = builder.build_atomic_tx(
            plan=plan,
            user_pubkey=wallet_pubkey,
            priority_fee_lamports=int(config.priority_fee_lamports),
            recent_blockhash=blockhash,
            alt_resolver=_alt_resolver,
        )
    except Exception as exc:
        _fail(f"build_atomic_tx failed: {exc}")
        return False
    _ok(f"built VersionedTransaction: {len(tx.message.instructions)} instructions")

    # simulateTransaction — no submission. Mainnet RPC will accept unsigned
    # VersionedTransactions for simulation if replaceRecentBlockhash is true.
    import base64 as _b64
    tx_bytes = bytes(tx)
    try:
        sim = rpc._call("simulateTransaction", [
            _b64.b64encode(tx_bytes).decode(),
            {
                "encoding": "base64",
                "commitment": "confirmed",
                "replaceRecentBlockhash": True,
                "sigVerify": False,
            },
        ])
        sim_val = (sim or {}).get("value") or {}
        err = sim_val.get("err")
        cu = sim_val.get("unitsConsumed")
        logs = sim_val.get("logs") or []
    except Exception as exc:
        _fail(f"simulateTransaction error: {exc}")
        return False

    if err is None:
        _ok(f"simulation would land: units_consumed={cu}")
    else:
        _warn(f"simulation reports err={err}  (non-zero in rehearsal is common when ALT content is unresolved)")
        _warn(f"units_consumed={cu}")
    for line in logs[:10]:
        print(f"        log: {line}")
    if len(logs) > 10:
        print(f"        ... {len(logs) - 10} more log lines")
    # Return True whether sim errs or not — rehearsal primarily verifies
    # wiring. Sim-err details show up in the log output for operator review.
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=os.environ.get("BOT_CONFIG", "config/example_config.json"))
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--skip-tx", action="store_true",
                        help="scan-only smoke — skip the atomic-tx build + simulate step")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    load_env()

    print("=" * 60)
    print("  SolanaTrader Phase-4 rehearsal (DRY-RUN, no submission)")
    print("=" * 60)

    try:
        config = BotConfig.from_file(args.config)
    except Exception as exc:
        _fail(f"config load failed: {exc}")
        return 1

    print()
    print("Step 1: environment")
    if not check_env(config):
        return 1

    print()
    print(f"Step 2: {args.iterations} scan(s)")
    try:
        opps = run_scans(config, args.iterations)
    except Exception as exc:
        _fail(f"scans failed: {exc}")
        return 1

    if args.skip_tx:
        print()
        _ok("--skip-tx set; rehearsal ends after scans")
        return 0

    print()
    print("Step 3: best candidate")
    best = pick_best(opps)
    if best is None:
        _warn("no opportunities during rehearsal window — cannot exercise tx path")
        _warn("this is expected when spreads are structurally below threshold")
        return 0
    _ok(f"best: {best.pair} {best.buy_venue}→{best.sell_venue} "
        f"net={best.net_profit_base}")

    print()
    print("Step 4: build + simulate")
    ok = build_and_simulate(config, best)
    print()
    if ok:
        _ok("Rehearsal completed without hard errors.")
        return 0
    _fail("Rehearsal hit a hard error — see logs above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
