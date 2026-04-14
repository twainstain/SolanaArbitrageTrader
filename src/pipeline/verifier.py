"""On-chain result verifier — extracts actual profit from transaction receipts.

Implements the ResultVerifier protocol from lifecycle.py.
Verifies that a submitted transaction was included, checks for reverts,
extracts gas used, and calculates actual profit from on-chain state.

Usage::

    verifier = OnChainVerifier(w3, contract_address, quote_decimals=6)
    included, reverted, gas_used, actual_profit = verifier.verify(tx_hash)
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from web3 import Web3

D = Decimal
logger = logging.getLogger(__name__)

# ERC-20 Transfer event signature: Transfer(address,address,uint256)
TRANSFER_EVENT_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").hex()


class OnChainVerifier:
    """Verify on-chain transaction results and extract actual profit.

    Checks tx receipt for inclusion/revert, calculates gas cost,
    and extracts profit from Transfer events to the contract address.
    """

    def __init__(
        self,
        w3: Web3,
        contract_address: str,
        quote_decimals: int = 6,
        timeout: int = 120,
    ) -> None:
        self.w3 = w3
        self.contract_address = Web3.to_checksum_address(contract_address)
        self.quote_decimals = quote_decimals
        self.timeout = timeout

    def verify(self, tx_hash: str) -> tuple[bool, bool, int, Decimal]:
        """Verify a transaction and return (included, reverted, gas_used, actual_profit).

        - included: True if the tx was mined in a block
        - reverted: True if the tx reverted (status=0)
        - gas_used: actual gas consumed
        - actual_profit: profit extracted from Transfer events (in base units)
        """
        try:
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)
        except Exception as exc:
            logger.warning("Could not fetch receipt for %s: %s", tx_hash, exc)
            return False, False, 0, D("0")

        if receipt is None:
            return False, False, 0, D("0")

        included = receipt["blockNumber"] > 0
        reverted = receipt["status"] == 0
        gas_used = receipt["gasUsed"]

        if reverted or not included:
            return included, reverted, gas_used, D("0")

        # Extract profit from Transfer events to the contract.
        actual_profit = self._extract_profit(receipt)

        # Subtract gas cost (in ETH, converted to base units).
        gas_cost = self._calculate_gas_cost(receipt)
        net_profit = actual_profit - gas_cost

        logger.info(
            "Verified %s: included=%s gas=%d profit=%s gas_cost=%s net=%s",
            tx_hash, included, gas_used, actual_profit, gas_cost, net_profit,
        )
        return included, reverted, gas_used, net_profit

    def _extract_profit(self, receipt: dict) -> Decimal:
        """Extract profit from ERC-20 Transfer events in the receipt.

        Looks for Transfer events TO the contract address (profit transfers).
        The flash loan contract transfers profit to itself, then to the owner.
        We look for the final outgoing transfer from the contract.
        """
        profit = D("0")
        contract_lower = self.contract_address.lower()

        for log_entry in receipt.get("logs", []):
            topics = log_entry.get("topics", [])
            if not topics:
                continue

            # Check if this is a Transfer event.
            topic_hex = topics[0].hex() if isinstance(topics[0], bytes) else topics[0]
            if topic_hex != TRANSFER_EVENT_TOPIC:
                continue

            if len(topics) < 3:
                continue

            # Transfer(from, to, amount) — `to` is topics[2].
            to_addr = "0x" + (topics[2].hex() if isinstance(topics[2], bytes) else topics[2])[-40:]

            if to_addr.lower() == contract_lower:
                # Incoming transfer to contract — this is repayment or swap output.
                raw_amount = int(log_entry["data"].hex() if isinstance(log_entry["data"], bytes) else log_entry["data"], 16)
                profit += D(raw_amount) / D(10 ** self.quote_decimals)

        return profit

    def _calculate_gas_cost(self, receipt: dict) -> Decimal:
        """Calculate gas cost in ETH from the receipt."""
        gas_used = receipt["gasUsed"]
        effective_gas_price = receipt.get("effectiveGasPrice", 0)
        gas_cost_wei = gas_used * effective_gas_price
        return D(gas_cost_wei) / D(10 ** 18)


class PnLReconciler:
    """Compare actual on-chain profit with expected off-chain estimates.

    Flags significant deviations for alerting and analysis.
    """

    def __init__(self, deviation_threshold_pct: float = 20.0) -> None:
        self.deviation_threshold_pct = deviation_threshold_pct
        self._reconciliations: list[dict] = []

    def reconcile(
        self,
        opp_id: str,
        expected_profit: Decimal,
        actual_profit: Decimal,
        gas_used: int,
        estimated_gas: Decimal,
    ) -> dict:
        """Compare expected vs actual profit. Returns reconciliation report."""
        deviation = D("0")
        deviation_pct = 0.0
        if expected_profit > D("0"):
            deviation = actual_profit - expected_profit
            deviation_pct = float(deviation / expected_profit * D("100"))

        gas_deviation = 0.0
        if estimated_gas > D("0"):
            gas_deviation = float((D(gas_used) - estimated_gas) / estimated_gas * D("100"))

        is_significant = abs(deviation_pct) > self.deviation_threshold_pct

        report = {
            "opp_id": opp_id,
            "expected_profit": str(expected_profit),
            "actual_profit": str(actual_profit),
            "deviation": str(deviation),
            "deviation_pct": round(deviation_pct, 2),
            "gas_used": gas_used,
            "estimated_gas": str(estimated_gas),
            "gas_deviation_pct": round(gas_deviation, 2),
            "significant_deviation": is_significant,
        }

        self._reconciliations.append(report)

        if is_significant:
            logger.warning(
                "PnL deviation for %s: expected=%s actual=%s (%.1f%%)",
                opp_id, expected_profit, actual_profit, deviation_pct,
            )

        return report

    @property
    def recent_reconciliations(self) -> list[dict]:
        """Return the last 100 reconciliation reports."""
        return self._reconciliations[-100:]

    @property
    def summary(self) -> dict:
        """Aggregate reconciliation stats."""
        if not self._reconciliations:
            return {"total": 0}

        deviations = [r["deviation_pct"] for r in self._reconciliations]
        significant = [r for r in self._reconciliations if r["significant_deviation"]]
        return {
            "total": len(self._reconciliations),
            "significant_deviations": len(significant),
            "avg_deviation_pct": round(sum(deviations) / len(deviations), 2),
            "max_deviation_pct": round(max(deviations, key=abs), 2),
        }
