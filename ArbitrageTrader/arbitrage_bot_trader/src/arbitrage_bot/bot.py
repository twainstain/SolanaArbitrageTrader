"""Main bot loop: fetch quotes -> evaluate strategy -> execute or log."""

from __future__ import annotations

import time
from typing import Protocol

from arbitrage_bot.config import BotConfig, PairConfig
from arbitrage_bot.executor import PaperExecutor
from arbitrage_bot.log import (
    get_logger,
    log_execution,
    log_scan,
    log_summary,
)
from arbitrage_bot.market import SimulatedMarket
from arbitrage_bot.models import ExecutionResult, MarketQuote, Opportunity
from arbitrage_bot.strategy import ArbitrageStrategy

logger = get_logger(__name__)


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
    ) -> None:
        self.config = config
        self.market: MarketSource = market or SimulatedMarket(config)
        self.strategy = strategy or ArbitrageStrategy(config)
        self.executor: Executor = executor or PaperExecutor(config)
        # Pairs to scan — passed directly (e.g. from pair_scanner discovery)
        # or falls back to config's primary pair + extra_pairs.
        self._pairs = pairs

    def _build_pair_list(self) -> list[PairConfig]:
        """Build the list of pairs to scan each cycle.

        Priority:
          1. self._pairs if passed directly (e.g. from --discover)
          2. config.extra_pairs + primary pair from config
        """
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
    def _filter_outliers(quotes: list[MarketQuote], max_deviation: float = 0.5) -> list[MarketQuote]:
        """Remove quotes whose mid-price deviates more than max_deviation from the pair median.

        This catches bad data from low-liquidity pools (e.g. a pool returning $39
        for WETH when others show ~$2200).
        """
        from collections import defaultdict
        import statistics

        by_pair: dict[str, list[MarketQuote]] = defaultdict(list)
        for q in quotes:
            by_pair[q.pair].append(q)

        filtered: list[MarketQuote] = []
        for pair, pqs in by_pair.items():
            if len(pqs) < 2:
                filtered.extend(pqs)
                continue

            mids = [(q.buy_price + q.sell_price) / 2 for q in pqs]
            median = statistics.median(mids)
            if median == 0:
                filtered.extend(pqs)
                continue

            for q, mid in zip(pqs, mids):
                deviation = abs(mid - median) / median
                if deviation <= max_deviation:
                    filtered.append(q)
                else:
                    logger.warning(
                        "Outlier removed: %s on %s mid=$%.2f vs median=$%.2f (%.0f%% deviation)",
                        q.pair, q.dex, mid, median, deviation * 100,
                    )
        return filtered

    def run(
        self, iterations: int = 10, sleep: bool = True, dry_run: bool = False
    ) -> None:
        total_scans = 0
        opportunities_found = 0
        executed_count = 0
        total_realized_profit = 0.0

        all_pairs = self._build_pair_list()
        pair_names = [p.pair for p in all_pairs]
        logger.info("Scanning %d pair(s): %s", len(all_pairs), ", ".join(pair_names))

        for index in range(1, iterations + 1):
            total_scans += 1
            try:
                quotes = self.market.get_quotes()
            except Exception as exc:
                logger.warning("[scan %d] market error: %s — skipping", index, exc)
                continue

            # Filter outlier quotes: remove any quote whose mid-price deviates
            # more than 50% from the median for that pair.  This catches bad data
            # from low-liquidity pools (e.g. Sushi-Arbitrum returning $39 for WETH).
            quotes = self._filter_outliers(quotes)

            # Find the best opportunity across all pairs.
            opportunity: Opportunity | None = None
            for pair_cfg in all_pairs:
                pair_quotes = [q for q in quotes if q.pair == pair_cfg.pair]
                if len(pair_quotes) < 2:
                    if index == 1 and pair_quotes:
                        logger.debug("Skipping %s — only %d venue(s), need 2+", pair_cfg.pair, len(pair_quotes))
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

                if dry_run:
                    decision = "dry_run_skip"
                    logger.info(
                        "[scan %d] %s buy on %s, sell on %s, "
                        "size=%.4f %s, expected net=%.6f %s (dry-run)",
                        index, opportunity.pair,
                        opportunity.buy_dex, opportunity.sell_dex,
                        opportunity.trade_size, self.config.base_asset,
                        opportunity.net_profit_base, self.config.base_asset,
                    )
                    log_scan(logger, index, quotes, opportunity, decision)
                else:
                    result = self.executor.execute(opportunity)
                    if result.success:
                        decision = "executed"
                        executed_count += 1
                        total_realized_profit += result.realized_profit_base
                        logger.info(
                            "[scan %d] %s buy on %s, sell on %s, "
                            "size=%.4f %s, expected net=%.6f %s",
                            index, opportunity.pair,
                            opportunity.buy_dex, opportunity.sell_dex,
                            opportunity.trade_size, self.config.base_asset,
                            opportunity.net_profit_base, self.config.base_asset,
                        )
                        logger.info(
                            "[exec %d] executed, realized profit=%.6f %s",
                            index, result.realized_profit_base, self.config.base_asset,
                        )
                    else:
                        decision = f"skipped:{result.reason}"
                        logger.info("[exec %d] skipped: %s", index, result.reason)

                    log_scan(logger, index, quotes, opportunity, decision)
                    log_execution(logger, index, result)

            if sleep and index < iterations:
                time.sleep(self.config.poll_interval_seconds)

        mode = "DRY-RUN" if dry_run else "LIVE"
        logger.info(
            "\n--- Summary (%s) ---\n"
            "Scans: %d\n"
            "Opportunities found: %d\n"
            "Executed: %d\n"
            "Total realized profit: %.6f %s",
            mode, total_scans, opportunities_found, executed_count,
            total_realized_profit, self.config.base_asset,
        )
        log_summary(
            logger, mode, total_scans, opportunities_found,
            executed_count, total_realized_profit, self.config.base_asset,
        )
