"""Verify the new pairs are wired into config/prod_scan.json."""

import json
from pathlib import Path


def test_prod_scan_contains_discovered_pairs():
    cfg = json.loads((Path(__file__).resolve().parents[1] / "config/prod_scan.json").read_text())
    pairs = [p["pair"] for p in cfg.get("extra_pairs", [])]
    # Pairs surfaced by `discover_pairs.py` as top-scored non-baseline.
    assert "JUP/SOL" in pairs
    assert "BONK/SOL" in pairs
    assert "BONK/USDC" in pairs
    # Sanity — base/quote match schema.
    for p in cfg["extra_pairs"]:
        assert p["pair"] == f"{p['base_asset']}/{p['quote_asset']}"
        assert float(p["trade_size"]) > 0
