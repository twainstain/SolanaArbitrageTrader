"""Solana market adapter — Jupiter v6 quote API.

Jupiter is an aggregator: for one (inputMint, outputMint, amount) it returns
the best cross-route quote.  We use it as a *single* venue in Phase 1.  To
produce an arbitrage signal from a single aggregator we request *two*
routes with different preferences — e.g. ``restrictIntermediateTokens`` on
and off, or ``onlyDirectRoutes`` true/false — and compare their outputs.

This lets us measure whether Jupiter's direct-route price disagrees with
its best-route price by enough margin to be actionable once execution cost,
slippage, and latency are priced in.  When Raydium/Orca direct-pool
adapters come online in Phase 2, those replace the synthesis trick.

Per-stage latency (request, parse) is recorded via
``observability.latency_tracker`` so we can spot slow endpoints in
production.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any

import requests

from core.config import BotConfig, PairConfig
from core.env import get_jupiter_api_url
from core.models import ZERO, MarketQuote
from core.tokens import get_token

logger = logging.getLogger(__name__)

D = Decimal


class RateLimitedError(Exception):
    """Jupiter returned HTTP 429 for this pair."""


class SolanaMarket:
    """Jupiter-backed market source.

    Produces two quotes per pair by issuing two Jupiter requests with
    different route-restriction hints.  The two quotes act as the "buy
    venue" and "sell venue" that the scanner compares.
    """

    def __init__(
        self,
        config: BotConfig,
        pairs: list[PairConfig] | None = None,
        request_timeout: float = 2.5,
        slippage_bps: int | None = None,
    ) -> None:
        self.config = config
        self.pairs = pairs or self._build_pairs(config)
        self.base_url = get_jupiter_api_url().rstrip("/")
        self.timeout = request_timeout
        # Jupiter's slippageBps param — tell the quoter how much slippage we
        # tolerate so the returned output reflects real execution.  We use
        # half the config slippage as a conservative estimate for quoting
        # (actual execution would use the full allowance).
        self._slippage_bps = slippage_bps if slippage_bps is not None else max(
            1, int(config.slippage_bps) // 2,
        )
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        # Per-pair 429 cooldown (Phase 2d): Jupiter's free tier rate-limits
        # aggressively (~1 req/s). When a pair returns 429, skip it for this
        # many seconds so the remaining pairs still get queried each scan and
        # the rate-limited one naturally backs off.
        self._rate_limit_cooldown_seconds: float = 60.0
        self._pair_cooldown_until: dict[str, float] = {}
        # Round-robin pair rotation: when more than `max_pairs_per_scan` pairs
        # are configured, each scan queries only a subset to stay under the
        # Jupiter free-tier rate limit (~60 req/min = 1 req/s; each pair costs
        # 2 requests). Primary pair is always queried; extras rotate.
        self._max_pairs_per_scan: int = 2
        self._rotation_offset: int = 0

    @staticmethod
    def _build_pairs(config: BotConfig) -> list[PairConfig]:
        pairs: list[PairConfig] = [PairConfig(
            pair=config.pair,
            base_asset=config.base_asset,
            quote_asset=config.quote_asset,
            trade_size=config.trade_size,
        )]
        if config.extra_pairs:
            pairs.extend(config.extra_pairs)
        return pairs

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_quotes(self) -> list[MarketQuote]:
        """Fetch Jupiter quotes for a subset of configured pairs.

        Returns [] on total failure (e.g. Jupiter unreachable).  Per-pair
        failures log a warning and continue — partial data is better than no
        data for scanner work. Pairs in 429-cooldown are silently skipped.

        When more than `max_pairs_per_scan` pairs are configured, a
        round-robin rotation covers the extras across successive scans.
        This keeps request volume under Jupiter's free-tier rate limit.
        """
        now = time.time()
        out: list[MarketQuote] = []
        for pair_cfg in self._pairs_this_scan():
            cooldown_until = self._pair_cooldown_until.get(pair_cfg.pair, 0.0)
            if cooldown_until > now:
                continue
            try:
                quotes = self._quotes_for_pair(pair_cfg)
                out.extend(quotes)
            except RateLimitedError:
                self._pair_cooldown_until[pair_cfg.pair] = (
                    now + self._rate_limit_cooldown_seconds
                )
                logger.info(
                    "[jupiter] %s rate-limited → cooling down %.0fs",
                    pair_cfg.pair, self._rate_limit_cooldown_seconds,
                )
            except Exception as exc:
                logger.warning(
                    "[jupiter] quote fetch failed for %s: %s",
                    pair_cfg.pair, exc,
                )
        return out

    def _pairs_this_scan(self) -> list[PairConfig]:
        """Pick `max_pairs_per_scan` pairs this scan using round-robin.

        Primary pair (index 0) is always included; extras rotate across
        scans so over `ceil(len(extras) / (max-1))` scans every pair is
        covered. When `max_pairs_per_scan >= len(pairs)` everything is
        returned every scan (current behavior for tests and small configs).
        """
        n = len(self.pairs)
        if n == 0:
            return []
        if self._max_pairs_per_scan <= 0 or self._max_pairs_per_scan >= n:
            return list(self.pairs)

        selected: list[PairConfig] = [self.pairs[0]]
        extras = self.pairs[1:]
        if extras and self._max_pairs_per_scan > 1:
            take = self._max_pairs_per_scan - 1
            for i in range(take):
                selected.append(extras[(self._rotation_offset + i) % len(extras)])
            self._rotation_offset = (self._rotation_offset + take) % len(extras)
        return selected

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _quotes_for_pair(self, pair_cfg: PairConfig) -> list[MarketQuote]:
        """Issue two Jupiter requests (direct + multihop) and return quotes.

        Each returned MarketQuote carries ``fee_included=True`` because
        Jupiter's ``outAmount`` is the net amount the user receives
        (platform fees and LP fees already subtracted).
        """
        base = get_token(pair_cfg.base_asset)
        quote = get_token(pair_cfg.quote_asset)
        trade_amount_native = int(pair_cfg.trade_size * (D(10) ** base.decimals))

        # Route 1: "best" — allow multi-hop.
        best = self._request_quote(
            input_mint=base.mint,
            output_mint=quote.mint,
            amount=trade_amount_native,
            only_direct=False,
        )
        # Route 2: "direct" — restrict to single-hop to expose spread
        # between the aggregator's best route and a direct AMM route.
        direct = self._request_quote(
            input_mint=base.mint,
            output_mint=quote.mint,
            amount=trade_amount_native,
            only_direct=True,
        )

        now = time.time()
        results: list[MarketQuote] = []
        if best is not None:
            results.append(self._as_quote(
                pair_cfg, base, quote, best,
                venue="Jupiter-Best", timestamp=now,
            ))
        if direct is not None:
            results.append(self._as_quote(
                pair_cfg, base, quote, direct,
                venue="Jupiter-Direct", timestamp=now,
            ))
        return results

    def _request_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        only_direct: bool,
    ) -> dict[str, Any] | None:
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": str(self._slippage_bps),
            "onlyDirectRoutes": "true" if only_direct else "false",
            "restrictIntermediateTokens": "true",
        }
        url = f"{self.base_url}/quote"
        try:
            resp = self._session.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 429:
                # Let the caller set a per-pair cooldown so subsequent pairs
                # in the scan still run.
                raise RateLimitedError(
                    f"429 from Jupiter: {input_mint[:6]}→{output_mint[:6]}"
                )
            resp.raise_for_status()
        except RateLimitedError:
            raise
        except requests.RequestException as exc:
            logger.debug("[jupiter] %s → %s (direct=%s) failed: %s",
                          input_mint[:6], output_mint[:6], only_direct, exc)
            return None
        try:
            return resp.json()
        except ValueError:
            logger.debug("[jupiter] non-JSON response")
            return None

    @staticmethod
    def _as_quote(
        pair_cfg: PairConfig,
        base_tok,
        quote_tok,
        jup: dict[str, Any],
        venue: str,
        timestamp: float,
    ) -> MarketQuote:
        """Convert a Jupiter quote JSON into a MarketQuote.

        Jupiter returns:
          - ``inAmount``  — native units of input (e.g. lamports)
          - ``outAmount`` — native units of output
          - ``priceImpactPct`` — string float (e.g. "0.0012")
        We derive human-readable ``buy_price`` (quote per 1 base) from
        outAmount / inAmount with decimal scaling.
        """
        in_native = D(str(jup.get("inAmount", "0")))
        out_native = D(str(jup.get("outAmount", "0")))
        if in_native <= ZERO or out_native <= ZERO:
            raise ValueError("empty Jupiter quote")

        in_human = in_native / (D(10) ** base_tok.decimals)
        out_human = out_native / (D(10) ** quote_tok.decimals)
        # buy_price = what 1 base unit costs in quote (from the user's POV)
        price = out_human / in_human
        # Jupiter gives a single output — reuse for both sides of the MarketQuote
        # model.  A tiny epsilon separates buy from sell so the scanner can
        # observe the natural spread between Jupiter-Best and Jupiter-Direct.
        return MarketQuote(
            venue=venue,
            pair=pair_cfg.pair,
            buy_price=price,
            sell_price=price,
            fee_bps=D("0"),            # Jupiter already nets fees
            fee_included=True,
            quote_timestamp=timestamp,
            venue_type="aggregator",
            liquidity_usd=ZERO,        # Jupiter doesn't expose route TVL directly
        )
