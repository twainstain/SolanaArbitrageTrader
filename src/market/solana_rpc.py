"""Shared Solana JSON-RPC client used by Raydium/Orca direct-pool adapters.

Uses ``SOLANA_RPC_URL`` from the environment (default: Alchemy) and exposes
a batched ``get_multiple_accounts`` that returns decoded account data for N
pubkeys in a single round-trip.

Design notes
------------

- ``getMultipleAccounts`` is the canonical Solana way to read many accounts
  in one call.  Alchemy accepts up to 100 pubkeys per request.  We batch
  larger inputs into chunks of 100.
- Accounts are returned with ``data`` base64-encoded.  This module decodes
  to raw ``bytes`` so adapters can parse the layout directly.
- No caching here — the caller (market adapter) decides TTL.  Pool state
  changes every slot, so most callers want fresh data every scan.
- ``commitment: "processed"`` is used — lowest latency and sufficient for
  quote freshness.  Phase 3 (execution) will switch to ``confirmed`` for
  transaction submission reads.
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

from core.env import get_solana_rpc_urls

logger = logging.getLogger(__name__)

_MAX_PUBKEYS_PER_REQUEST = 100


@dataclass(frozen=True)
class AccountInfo:
    """Decoded Solana account snapshot."""
    pubkey: str
    owner: str              # program ID that owns the account
    lamports: int
    data: bytes             # raw account data (base64-decoded)
    slot: int = 0           # RPC context slot for this read


class SolanaRPC:
    """Thin HTTP client around Solana JSON-RPC with multi-endpoint failover.

    The scanner loop wants fast failure so a single slow RPC doesn't stall
    the whole scan. When ``SOLANA_RPC_URL[_N]`` declares multiple endpoints,
    this client rotates to the next on any error (network, 5xx, 429) and
    the caller never sees the failure. On success the current endpoint
    sticks — no flapping.

    If only one endpoint is configured the class behaves exactly as before
    (failure raises through).
    """

    def __init__(
        self,
        url: str | None = None,
        timeout: float = 2.5,
        commitment: str = "processed",
        urls: list[str] | None = None,
    ) -> None:
        # Explicit urls list beats env lookup; single `url` beats both
        # (backwards-compat for tests that pass one URL).
        if urls:
            self._urls = list(urls)
        elif url is not None:
            self._urls = [url]
        else:
            env_urls = get_solana_rpc_urls()
            self._urls = list(env_urls) if env_urls else ["https://api.mainnet-beta.solana.com"]
        self._current_idx = 0
        # Keep ``self.url`` pointing at the active endpoint for logging
        # + backwards-compat with callers that read it.
        self.url = self._urls[self._current_idx]
        self.timeout = timeout
        self.commitment = commitment
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self._request_id = 0
        # Per-endpoint error counter so we can expose a health snapshot.
        self._endpoint_errors: list[int] = [0] * len(self._urls)

    # ------------------------------------------------------------------
    # Low-level RPC
    # ------------------------------------------------------------------

    def _call(self, method: str, params: list[Any]) -> Any:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        # Try each configured endpoint once, starting with the current one.
        # On network/server errors we rotate; on protocol-level errors
        # (JSON-RPC "error" field) we do NOT rotate — that's a request bug,
        # not an endpoint problem.
        attempts = len(self._urls)
        last_exc: Exception | None = None
        for _ in range(attempts):
            endpoint_url = self._urls[self._current_idx]
            try:
                resp = self._session.post(endpoint_url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                body = resp.json()
                if "error" in body:
                    raise RuntimeError(f"Solana RPC error: {body['error']}")
                # Success — sticky, don't rotate.
                self.url = endpoint_url
                return body.get("result")
            except (requests.RequestException, requests.HTTPError, ValueError) as exc:
                # Transport / server problem — mark the endpoint and rotate.
                self._endpoint_errors[self._current_idx] += 1
                last_exc = exc
                logger.debug(
                    "[rpc] %s failed on %s: %s — rotating",
                    method, endpoint_url.split("//", 1)[-1].split("/", 1)[0], exc,
                )
                self._current_idx = (self._current_idx + 1) % len(self._urls)
                self.url = self._urls[self._current_idx]
        # All endpoints exhausted.
        raise RuntimeError(
            f"Solana RPC: all {attempts} endpoint(s) failed for {method}: {last_exc}"
        )

    # ------------------------------------------------------------------
    # High-level helpers
    # ------------------------------------------------------------------

    def get_multiple_accounts(self, pubkeys: list[str]) -> list[AccountInfo | None]:
        """Return decoded AccountInfo for each pubkey, or None if not found.

        Batches inputs into chunks of 100 (Alchemy's per-request limit) and
        stitches the result in original order.
        """
        if not pubkeys:
            return []

        out: list[AccountInfo | None] = []
        for chunk_start in range(0, len(pubkeys), _MAX_PUBKEYS_PER_REQUEST):
            chunk = pubkeys[chunk_start:chunk_start + _MAX_PUBKEYS_PER_REQUEST]
            result = self._call(
                "getMultipleAccounts",
                [chunk, {"commitment": self.commitment, "encoding": "base64"}],
            )
            slot = (result or {}).get("context", {}).get("slot", 0)
            values = (result or {}).get("value", [])
            for pk, val in zip(chunk, values):
                if val is None:
                    out.append(None)
                    continue
                raw_b64 = val["data"][0] if isinstance(val["data"], list) else val["data"]
                out.append(AccountInfo(
                    pubkey=pk,
                    owner=val.get("owner", ""),
                    lamports=val.get("lamports", 0),
                    data=base64.b64decode(raw_b64),
                    slot=slot,
                ))
        return out

    def get_slot(self) -> int:
        """Current slot — useful for health checks."""
        return int(self._call("getSlot", []))

    def get_address_lookup_tables(self, keys: list[str]):
        """Fetch + decode Address Lookup Tables by pubkey (Phase 3c).

        Returns a list of ``AddressLookupTableAccount`` in the same order
        as ``keys``. Keys that don't resolve to an on-chain ALT (missing
        account, wrong owner, or data too short) are dropped silently —
        the caller sees ``len(result) <= len(keys)``.

        Lets ``execution.atomic_swap.AtomicSwapBuilder.build_atomic_tx``
        receive real ALT account bodies when compiling a MessageV0 rather
        than the empty-list stub used in rehearsal.
        """
        from solders.address_lookup_table_account import AddressLookupTableAccount
        from solders.pubkey import Pubkey

        if not keys:
            return []
        accounts = self.get_multiple_accounts(keys)
        result = []
        for key, acc in zip(keys, accounts):
            if acc is None or acc.owner != _ALT_PROGRAM_ID:
                logger.debug(
                    "[rpc] skipping ALT %s — owner=%s",
                    key[:8], acc.owner if acc else "None",
                )
                continue
            addresses = parse_alt_addresses(acc.data)
            if not addresses:
                # An empty table is valid but useless; drop to keep the
                # atomic-swap compile from allocating a no-op LUT lookup.
                continue
            try:
                alt_key = Pubkey.from_string(key)
            except ValueError:
                continue
            result.append(AddressLookupTableAccount(key=alt_key, addresses=addresses))
        return result


# ---------------------------------------------------------------------------
# SPL Token Account decoding (Raydium uses SPL token vaults for reserves)
# ---------------------------------------------------------------------------

_SPL_TOKEN_ACCOUNT_LEN = 165


# Address Lookup Table program + account layout (Phase 3c).
# On-chain ALT account: first 56 bytes are meta (typ, deactivation_slot,
# last_extended_slot, start_index, authority Option, padding), followed by
# a packed array of Pubkeys (32 bytes each).
_ALT_PROGRAM_ID = "AddressLookupTab1e1111111111111111111111111"
_LOOKUP_TABLE_META_SIZE = 56


def parse_alt_addresses(data: bytes):
    """Extract the Pubkey array from an Address Lookup Table account's data.

    Returns an empty list if the data is shorter than the header or its
    trailing bytes aren't a whole multiple of 32.
    """
    from solders.pubkey import Pubkey
    if len(data) < _LOOKUP_TABLE_META_SIZE:
        return []
    tail = data[_LOOKUP_TABLE_META_SIZE:]
    if len(tail) % 32 != 0:
        return []
    return [Pubkey.from_bytes(tail[i:i + 32]) for i in range(0, len(tail), 32)]


def parse_spl_token_amount(data: bytes) -> int:
    """Extract the ``amount`` field from an SPL Token Account's raw data.

    SPL Token Account layout (spl-token v1, classic Token program):
      0..32   mint                  (Pubkey)
      32..64  owner                 (Pubkey)
      64..72  amount                (u64 LE)        ← what we want
      72..   (delegate, state, is_native, delegated_amount, close_authority)

    Raises ValueError if the data is too short.
    """
    if len(data) < 72:
        raise ValueError(f"SPL token account too short: {len(data)} bytes")
    return int.from_bytes(data[64:72], "little", signed=False)


def health_check() -> dict:
    """Ping the configured RPC and return a tiny health summary."""
    rpc = SolanaRPC()
    t0 = time.monotonic()
    slot = rpc.get_slot()
    elapsed_ms = (time.monotonic() - t0) * 1000
    return {
        "url": rpc.url.rsplit("/", 1)[0] + "/…",  # hide API key in logs
        "slot": slot,
        "latency_ms": round(elapsed_ms, 1),
    }
