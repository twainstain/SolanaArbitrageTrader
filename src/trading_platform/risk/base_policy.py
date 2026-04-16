"""Rule-based risk evaluation framework.

Products register their own rules. The policy evaluates them sequentially —
any failure is a hard veto. This enforces "capital preservation > profit"
across all products.

Usage:
    from trading_platform.risk import RuleBasedPolicy, RiskRule, RiskVerdict

    class MinProfitRule:
        name = "min_profit"
        def evaluate(self, candidate, context):
            if candidate.profit < context["min_profit"]:
                return RiskVerdict(False, "below_min_profit", {"required": ...})
            return RiskVerdict(True, "ok")

    policy = RuleBasedPolicy(rules=[MinProfitRule(), ...])
    verdict = policy.evaluate(candidate, min_profit=0.005)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass
class RiskVerdict:
    """Result of a risk evaluation."""
    approved: bool
    reason: str
    details: dict = field(default_factory=dict)


class RiskRule(Protocol):
    """One risk check. Products implement their own rules."""
    name: str

    def evaluate(self, candidate: Any, context: dict) -> RiskVerdict:
        """Evaluate one rule. Return approved=False to veto."""
        ...


class RuleBasedPolicy:
    """Evaluates a list of rules sequentially. Any failure = hard veto.

    The rule list is ordered — early rules are checked first. Put cheap
    checks (spread, profit) before expensive ones (DB queries, RPC calls).

    Args:
        rules: Ordered list of RiskRule implementations.
        simulation_mode: If True, passing all rules returns an approved verdict
            tagged with ``reason="simulation_approved"`` and
            ``details["simulation"] = True`` so callers can distinguish
            paper approval from live approval without breaking pipeline flow.
    """

    def __init__(
        self,
        rules: list[RiskRule] | None = None,
        simulation_mode: bool = False,
    ) -> None:
        self.rules = list(rules or [])
        self.simulation_mode = simulation_mode

    def add_rule(self, rule: RiskRule) -> None:
        """Append a rule to the evaluation chain."""
        self.rules.append(rule)

    def evaluate(self, candidate: Any, **context) -> RiskVerdict:
        """Run all rules sequentially. First failure stops evaluation.

        Args:
            candidate: The opportunity/signal/position to evaluate.
            **context: Additional data passed to each rule (e.g., hour_trades,
                       chain, current_exposure).

        Returns:
            RiskVerdict with approved=True only if ALL rules pass.
        """
        for rule in self.rules:
            try:
                verdict = rule.evaluate(candidate, context)
            except Exception as exc:
                logger.error("Risk rule '%s' raised: %s", getattr(rule, 'name', '?'), exc)
                return RiskVerdict(False, f"rule_error:{getattr(rule, 'name', '?')}", {"error": str(exc)})

            if not verdict.approved:
                logger.debug("Risk rejected by '%s': %s", getattr(rule, 'name', '?'), verdict.reason)
                return verdict

        if self.simulation_mode:
            return RiskVerdict(True, "simulation_approved", {"simulation": True})

        return RiskVerdict(True, "all_rules_passed")
