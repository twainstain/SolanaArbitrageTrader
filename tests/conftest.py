"""Pytest fixtures shared across Solana tests."""

from __future__ import annotations

import sys
from pathlib import Path

# Make ``src`` importable as the top-level package root for tests.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
_PLATFORM = _ROOT / "lib" / "trading_platform" / "src"
for p in (_SRC, _PLATFORM):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)
