"""Main bot loop — fetch quotes → evaluate strategy → execute (paper) or log.

Scanner-phase (Phase 1): the executor is always ``PaperExecutor`` unless
the caller explicitly wires a real one.  Every scan cycle records per-
stage latency via ``observability.latency_tracker``.
"""

from __future__ import annotations

import statistics
import time
from collections import defaultdict
from decimal import Decimal
from typing import Protocol

from alerting.dispatcher import AlertDispatcher
from core.config import BotConfig, PairConfig
from execution.executor import PaperExecutor
from observability.log import (
    get_logger,
    log_execution,
    log_scan,
    log_summary,
)
from market.sim_market import SimulatedMarket
from core.models import ZERO, ExecutionResult, MarketQuote, Opportunity
from strategy import ArbitrageStrategy

logger = get_logger("bot")

D = Decimal
TWO = D("2")


class MarketSource(Protocol):
    def get_quotes(self) -> list[MarketQuote]: ...


class Executor(Protocol):
    def execute(self, opportunity: Opportunity) -> ExecutionResult: ...


class ArbitrageBot:
    def __init__(
        self,
        config: BotConfig,
        market: MarketSource | None = None,
        strategy: ArbitrageStrategy | None = None,
        executor: Executor | None = None,
        pairs: list[PairConfig] | None = None,
        dispatcher: AlertDispatcher | None = None,
    ) -> None:
        self.config = config
        self._pairs = pairs
        self.market: MarketSource = market or SimulatedMarket(config)
        self.strategy = strategy or ArbitrageStrategy(config, pairs=self._build_pair_list())
        self.executor: Executor = executor or PaperExecutor(config)
        self.dispatcher = dispatcher or AlertDispatcher()
        self._shutdown_requested = False

    @staticmethod
    def _base_asset_for_opportunity(opportunity: Opportunity) -> str:
        if "/" in opportunity.pair:
            return opportunity.pair.split("/", 1)[0]
        return ""

    def request_shutdown(self) -> None:
        self._shutdown_requested = True
        logger.info("Shutdown requested — will stop after current iteration")

    def _build_pair_list(self) -> list[PairConfig]:
        if self._pairs is not None:
            return list(self._pairs)
        result = [PairConfig(
            pair=self.config.pair,
            base_asset=self.config.base_asset,
            quote_asset=self.config.quote_asset,
            trade_size=self.config.trade_size,
        )]
        if self.config.extra_pairs:
            result.extend(self.config.extra_pairs)
        return result

    @staticmethod
    def _filter_outliers(
        quotes: list[MarketQuote], max_deviation: Decimal = D("0.03"),
    ) -> list[MarketQuote]:
        """Remove quotes whose mid-price deviates >max_deviation from the pair median."""
        by_pair: dict[str, list[MarketQuote]] = defaultdict(list)
        for q in quotes:
            by_pair[q.pair].append(q)
        global_median: dict[str, Decimal] = {}
        for pair, pqs in by_pair.items():
            mids = [(q.buy_price + q.sell_price) / TWO for q in pqs]
            if mids:
                global_median[pair] = statistics.median(mids)

        filtered: list[MarketQuote] = []
        for pair, pqs in by_pair.items():
            if len(pqs) < 2:
                filtered.extend(pqs)
                continue
            median = global_median.get(pair, ZERO)
            if median == ZERO:
                filtered.extend(pqs)
                continue
            for q in pqs:
                mid = (q.buy_price + q.sell_price) / TWO
                deviation = abs(mid - median) / median
                if deviation <= max_deviation:
                    filtered.append(q)
                else:
                    logger.warning(
                        "Outlier removed: %s on %s mid=%.6f vs median=%.6f (%.0f%% dev)",
                        q.pair, q.venue, float(mid), float(median), float(deviation * D("100")),
                    )
        return filtered

    def run(
        self, iterations: int = 10, sleep: bool = True, dry_run: bool = False,
    ) -> None:
        total_scans = 0
        opportunities_found = 0
        executed_count = 0
        total_realized_profit = ZERO

        all_pairs = self._build_pair_list()
        pair_names = [p.pair for p in all_pairs]
        logger.info("Scanning %d pair(s): %s", len(all_pairs), ", ".join(pair_names))

        for index in range(1, iterations + 1):
            if self._shutdown_requested:
                logger.info("Shutdown requested — stopping before scan %d", index)
                break

            total_scans += 1
            try:
                quotes = self.market.get_quotes()
            except Exception as exc:
                logger.warning("[scan %d] market error: %s — skipping", index, exc)
                self.dispatcher.system_error("market", str(exc))
                continue

            quotes = self._filter_outliers(quotes)

            opportunity: Opportunity | None = None
            for pair_cfg in all_pairs:
                pair_quotes = [q for q in quotes if q.pair == pair_cfg.pair]
                if len(pair_quotes) < 2:
                    if index == 1 and pair_quotes:
                        logger.debug("Skipping %s — only %d venue(s), need 2+",
                                     pair_cfg.pair, len(pair_quotes))
                    continue
                candidate = self.strategy.find_best_opportunity(pair_quotes)
                if candidate is not None and (
                    opportunity is None
                    or candidate.net_profit_base > opportunity.net_profit_base
                ):
                    opportunity = candidate

            if opportunity is None:
                decision = "no_opportunity"
                logger.info("[scan %d] no opportunity", index)
                log_scan(logger, index, quotes, None, decision)
            else:
                opportunities_found += 1
                self.dispatcher.opportunity_found(
                    pair=opportunity.pair,
                    buy_dex=opportunity.buy_venue,
                    sell_dex=opportunity.sell_venue,
                    spread_pct=float(opportunity.gross_spread_pct),
                    net_profit=float(opportunity.net_profit_base),
                )
                if dry_run:
                    decision = "dry_run_skip"
                    base_asset = self._base_asset_for_opportunity(opportunity) or self.config.base_asset
                    logger.info(
                        "[scan %d] %s buy on %s, sell on %s, "
                        "size=%.4f %s, expected net=%.8f %s (dry-run)",
                        index, opportunity.pair,
                        opportunity.buy_venue, opportunity.sell_venue,
                        float(opportunity.trade_size), base_asset,
                        float(opportunity.net_profit_base), base_asset,
                    )
                    log_scan(logger, index, quotes, opportunity, decision)
                else:
                    result = self.executor.execute(opportunity)
                    if result.success:
                        decision = "executed"
                        executed_count += 1
                        total_realized_profit += result.realized_profit_base
                        base_asset = self._base_asset_for_opportunity(opportunity) or self.config.base_asset
                        logger.info(
                            "[exec %d] executed, realized profit=%.8f %s",
                            index, float(result.realized_profit_base), base_asset,
                        )
                        self.dispatcher.trade_executed(
                            pair=opportunity.pair,
                            tx_hash=getattr(result, "signature", "") or "paper",
                            profit=float(result.realized_profit_base),
                        )
                    else:
                        decision = f"skipped:{result.reason}"
                        logger.info("[exec %d] skipped: %s", index, result.reason)
                        self.dispatcher.system_error("executor", result.reason)
                    log_scan(logger, index, quotes, opportunity, decision)
                    log_execution(logger, index, result)

            if sleep and index < iterations:
                time.sleep(self.config.poll_interval_seconds)

        mode = "DRY-RUN" if dry_run else "LIVE"
        logger.info(
            "\n--- Summary (%s) ---\nScans: %d\nOpportunities found: %d\n"
            "Executed: %d\nTotal realized profit: %.8f %s",
            mode, total_scans, opportunities_found, executed_count,
            float(total_realized_profit), self.config.base_asset,
        )
        log_summary(
            logger, mode, total_scans, opportunities_found,
            executed_count, total_realized_profit, self.config.base_asset,
        )
        self.dispatcher.daily_summary(
            scans=total_scans,
            opportunities=opportunities_found,
            executed=executed_count,
            total_profit=float(total_realized_profit),
            reverts=0,
        )
