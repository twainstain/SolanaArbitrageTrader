"""Alert dispatcher — ArbitrageTrader extension of trading_platform's AlertDispatcher.

Adds arbitrage-specific convenience methods and helper functions on top
of the generic fan-out dispatcher from the shared platform.

The base class (trading_platform.alerting.AlertDispatcher) provides:
  - add_backend(), alert(), backend_count

This module adds:
  - opportunity_found(), trade_executed(), trade_reverted()
  - daily_summary(), system_error()
  - tx_explorer_url(), opp_dashboard_url() helpers
"""

from __future__ import annotations

import logging

from trading_platform.alerting.dispatcher import (
    AlertBackend,
    AlertDispatcher as _PlatformDispatcher,
)

logger = logging.getLogger(__name__)

# Block explorer base URLs per chain.
BLOCK_EXPLORERS: dict[str, str] = {
    "ethereum": "https://etherscan.io",
    "arbitrum": "https://arbiscan.io",
    "base": "https://basescan.org",
    "optimism": "https://optimistic.etherscan.io",
    "polygon": "https://polygonscan.com",
    "bsc": "https://bscscan.com",
    "avax": "https://snowtrace.io",
    "scroll": "https://scrollscan.com",
    "linea": "https://lineascan.build",
    "zksync": "https://era.zksync.network",
}

DEFAULT_DASHBOARD_URL = "https://arb-trader.yeda-ai.com"


def tx_explorer_url(chain: str, tx_hash: str) -> str:
    """Return a block explorer URL for a transaction hash."""
    base = BLOCK_EXPLORERS.get(chain.lower(), "https://etherscan.io")
    return f"{base}/tx/{tx_hash}"


def opp_dashboard_url(opp_id: str, dashboard_url: str = DEFAULT_DASHBOARD_URL) -> str:
    """Return the dashboard detail page URL for an opportunity."""
    return f"{dashboard_url}/opportunity/{opp_id}"


class AlertDispatcher(_PlatformDispatcher):
    """ArbitrageTrader alert dispatcher with domain-specific convenience methods.

    Inherits from trading_platform's AlertDispatcher:
      - add_backend(backend) — register an alert backend
      - alert(event_type, message, details) — fan-out to all backends
      - backend_count — number of registered backends
    """

    def __init__(self, backends: list | None = None) -> None:
        super().__init__()
        if backends:
            for b in backends:
                self.add_backend(b)

    def add_backend(self, backend) -> None:
        """Add a backend without requiring the 'configured' property.

        The platform's add_backend checks backend.configured, but AT's
        alert backends may not have that property (pre-migration).
        """
        self._backends.append(backend)

    def alert(
        self,
        event_type: str,
        message: str,
        details: dict | None = None,
    ) -> int:
        """Send alert to all backends. Returns count of successful deliveries.

        Overrides platform's void return to return delivery count (AT convention).
        """
        delivered = 0
        for backend in self._backends:
            try:
                ok = backend.send(event_type, message, details)
                if ok:
                    delivered += 1
                else:
                    logger.warning("Alert backend '%s' returned failure for %s",
                                   backend.name, event_type)
            except Exception as exc:
                logger.error("Alert backend '%s' error for %s: %s",
                             backend.name, event_type, exc)
        return delivered

    def opportunity_found(self, pair: str, buy_dex: str, sell_dex: str,
                          spread_pct: float, net_profit: float,
                          opp_id: str = "", chain: str = "",
                          dashboard_url: str = DEFAULT_DASHBOARD_URL) -> int:
        lines = [
            f"Opportunity: {pair}",
            f"Buy: {buy_dex} → Sell: {sell_dex}",
            f"Spread: {spread_pct:.4f}%",
            f"Net profit: {net_profit:.6f}",
        ]
        details: dict = {
            "pair": pair, "buy_dex": buy_dex, "sell_dex": sell_dex,
            "spread_pct": spread_pct, "net_profit": net_profit,
        }
        if opp_id:
            link = opp_dashboard_url(opp_id, dashboard_url)
            lines.append(f"Dashboard: {link}")
            details["opp_id"] = opp_id
            details["dashboard_link"] = link
        if chain:
            details["chain"] = chain
        return self.alert("opportunity_found", "\n".join(lines), details)

    def trade_executed(self, pair: str, tx_hash: str, profit: float,
                       opp_id: str = "", chain: str = "",
                       dashboard_url: str = DEFAULT_DASHBOARD_URL) -> int:
        lines = [
            f"Trade Executed: {pair}",
            f"TX: {tx_hash}",
            f"Profit: {profit:.6f}",
        ]
        details: dict = {
            "pair": pair, "tx_hash": tx_hash, "profit": profit,
        }
        if chain and tx_hash:
            tx_link = tx_explorer_url(chain, tx_hash)
            lines.append(f"Explorer: {tx_link}")
            details["tx_link"] = tx_link
            details["chain"] = chain
        if opp_id:
            link = opp_dashboard_url(opp_id, dashboard_url)
            lines.append(f"Dashboard: {link}")
            details["opp_id"] = opp_id
            details["dashboard_link"] = link
        return self.alert("trade_executed", "\n".join(lines), details)

    def trade_reverted(self, pair: str, tx_hash: str, reason: str,
                       opp_id: str = "", chain: str = "",
                       dashboard_url: str = DEFAULT_DASHBOARD_URL) -> int:
        lines = [
            f"Trade REVERTED: {pair}",
            f"TX: {tx_hash}",
            f"Reason: {reason}",
        ]
        details: dict = {
            "pair": pair, "tx_hash": tx_hash, "reason": reason,
        }
        if chain and tx_hash:
            tx_link = tx_explorer_url(chain, tx_hash)
            lines.append(f"Explorer: {tx_link}")
            details["tx_link"] = tx_link
            details["chain"] = chain
        if opp_id:
            link = opp_dashboard_url(opp_id, dashboard_url)
            lines.append(f"Dashboard: {link}")
            details["opp_id"] = opp_id
            details["dashboard_link"] = link
        return self.alert("trade_reverted", "\n".join(lines), details)

    def system_error(self, component: str, error: str) -> int:
        msg = f"System Error in {component}:\n{error}"
        return self.alert("system_error", msg, {
            "component": component, "error": error,
        })

    def daily_summary(self, scans: int, opportunities: int, executed: int,
                      total_profit: float, reverts: int) -> int:
        msg = (f"Daily Summary\n"
               f"Scans: {scans}\n"
               f"Opportunities: {opportunities}\n"
               f"Executed: {executed}\n"
               f"Reverts: {reverts}\n"
               f"Total Profit: {total_profit:.6f}")
        return self.alert("daily_summary", msg, {
            "scans": scans, "opportunities": opportunities,
            "executed": executed, "total_profit": total_profit, "reverts": reverts,
        })
