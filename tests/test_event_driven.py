"""Tests for the event-driven pipeline flow: event → queue → pipeline → DB."""

import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.models import Opportunity, ZERO
from observability.metrics import MetricsCollector
from persistence.db import init_db, close_db
from persistence.repository import Repository
from pipeline.lifecycle import CandidatePipeline
from pipeline.queue import CandidateQueue
from risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from risk.policy import RiskPolicy
from run_event_driven import (
    assess_launch_readiness,
    build_risk_policy,
    build_execution_stack,
    compute_live_execution_summary,
    enforce_safe_execution_mode,
)

D = Decimal


def _make_opp(pair="WETH/USDC", chain="ethereum", spread=D("3.0"),
              profit=D("0.005"), **kw) -> Opportunity:
    defaults = dict(
        pair=pair, buy_dex="Uni", sell_dex="Sushi",
        trade_size=D("1"), cost_to_buy_quote=D("2200"),
        proceeds_from_sell_quote=D("2210"), gross_profit_quote=D("10"),
        net_profit_quote=D("8"), net_profit_base=profit,
        gross_spread_pct=spread, chain=chain,
    )
    defaults.update(kw)
    return Opportunity(**defaults)


class EventToQueueTests(unittest.TestCase):
    """Test that opportunities flow from scanner into the queue."""

    def test_push_and_pop(self):
        queue = CandidateQueue(max_size=10)
        opp = _make_opp()
        queue.push(opp, priority=1.0)
        c = queue.pop()
        self.assertIsNotNone(c)
        self.assertEqual(c.opportunity.pair, "WETH/USDC")

    def test_priority_ordering(self):
        queue = CandidateQueue(max_size=10)
        queue.push(_make_opp(profit=D("0.001")), priority=1.0)
        queue.push(_make_opp(profit=D("0.010")), priority=5.0)
        queue.push(_make_opp(profit=D("0.005")), priority=3.0)
        c = queue.pop()
        self.assertEqual(c.priority, 5.0)

    def test_back_pressure_drops_lowest(self):
        queue = CandidateQueue(max_size=2)
        queue.push(_make_opp(), priority=5.0)
        queue.push(_make_opp(), priority=3.0)
        # Queue full. New with priority 1 should be dropped.
        ok = queue.push(_make_opp(), priority=1.0)
        self.assertFalse(ok)
        self.assertEqual(queue.size, 2)


class QueueToPipelineTests(unittest.TestCase):
    """Test that the pipeline consumer processes queued opportunities."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self):
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_queue_to_pipeline_persists_to_db(self):
        """Opportunity from queue → pipeline → appears in DB."""
        policy = RiskPolicy(execution_enabled=False, min_net_profit=0)
        pipeline = CandidatePipeline(repo=self.repo, risk_policy=policy)
        queue = CandidateQueue()

        opp = _make_opp(chain="ethereum")
        queue.push(opp, priority=1.0)

        # Simulate consumer: pop and process.
        candidate = queue.pop()
        result = pipeline.process(candidate.opportunity)

        self.assertIn(result.final_status, ("rejected", "dry_run", "simulation_approved"))
        # Verify it's in the DB.
        opps = self.repo.get_recent_opportunities(10)
        self.assertEqual(len(opps), 1)
        self.assertEqual(opps[0]["chain"], "ethereum")

    def test_multiple_chains_in_db(self):
        """Multiple chains flow through queue → pipeline → all in DB."""
        policy = RiskPolicy(execution_enabled=False, min_net_profit=0)
        pipeline = CandidatePipeline(repo=self.repo, risk_policy=policy)
        queue = CandidateQueue()

        for chain in ["ethereum", "arbitrum", "optimism"]:
            queue.push(_make_opp(chain=chain), priority=1.0)

        while not queue.is_empty:
            c = queue.pop()
            pipeline.process(c.opportunity)

        opps = self.repo.get_recent_opportunities(10)
        chains = {o["chain"] for o in opps}
        self.assertEqual(chains, {"ethereum", "arbitrum", "optimism"})


class CircuitBreakerIntegrationTests(unittest.TestCase):
    """Test that circuit breaker stops the pipeline consumer."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self):
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_breaker_open_skips_processing(self):
        """When circuit breaker is open, opportunities are skipped."""
        config = CircuitBreakerConfig(max_reverts=1, cooldown_seconds=9999)
        breaker = CircuitBreaker(config)
        breaker.record_revert()  # trips the breaker

        self.assertTrue(breaker.is_open)

        # Consumer would check breaker before processing.
        allowed, reason = breaker.allows_execution()
        self.assertFalse(allowed)
        self.assertIn("repeated_reverts", reason)

    def test_breaker_closed_allows_processing(self):
        breaker = CircuitBreaker()
        allowed, reason = breaker.allows_execution()
        self.assertTrue(allowed)
        self.assertEqual(reason, "circuit_closed")


class MetricsIntegrationTests(unittest.TestCase):
    """Test that metrics are updated through the flow."""

    def test_rejection_tracked(self):
        metrics = MetricsCollector()
        metrics.record_opportunity_detected()
        metrics.record_opportunity_rejected("execution_disabled")
        s = metrics.snapshot()
        self.assertEqual(s["opportunities_detected"], 1)
        self.assertEqual(s["opportunities_rejected"], 1)
        self.assertEqual(s["rejection_reasons"]["execution_disabled"], 1)

    def test_latency_tracked(self):
        metrics = MetricsCollector()
        metrics.record_latency_ms(150.0)
        metrics.record_latency_ms(250.0)
        s = metrics.snapshot()
        self.assertAlmostEqual(s["avg_latency_ms"], 200.0)

    def test_full_flow_metrics(self):
        """Simulate a full flow and check metrics."""
        metrics = MetricsCollector()
        # Scan
        metrics.record_opportunity_detected()
        # Found opportunity
        metrics.record_expected_profit(0.005)
        # Pipeline processed
        metrics.record_latency_ms(120.0)
        # Rejected by risk
        metrics.record_opportunity_rejected("execution_disabled")

        s = metrics.snapshot()
        self.assertEqual(s["opportunities_detected"], 1)
        self.assertEqual(s["opportunities_rejected"], 1)
        self.assertAlmostEqual(s["total_expected_profit"], 0.005)
        self.assertAlmostEqual(s["avg_latency_ms"], 120.0)


class EndToEndFlowTests(unittest.TestCase):
    """Full end-to-end: create opportunity → queue → pipeline → DB → verify."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self):
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_end_to_end(self):
        policy = RiskPolicy(execution_enabled=False, min_net_profit=0)
        pipeline = CandidatePipeline(repo=self.repo, risk_policy=policy)
        queue = CandidateQueue(max_size=50)
        metrics = MetricsCollector()
        breaker = CircuitBreaker()

        # Simulate scanner output: 3 opportunities across chains.
        opportunities = [
            _make_opp(chain="ethereum", spread=D("0.15"), profit=D("0.002")),
            _make_opp(chain="optimism", spread=D("11.0"), profit=D("0.10")),
            _make_opp(chain="base", spread=D("0.09"), profit=D("-0.001")),
        ]

        # Producer: push to queue with priority.
        for opp in opportunities:
            score = float(opp.net_profit_base)
            queue.push(opp, priority=score)

        self.assertEqual(queue.size, 3)

        # Consumer: process all.
        processed = 0
        while not queue.is_empty:
            candidate = queue.pop()
            allowed, _ = breaker.allows_execution()
            if not allowed:
                continue
            breaker.record_fresh_quote()
            start = time.time() * 1000
            result = pipeline.process(candidate.opportunity)
            metrics.record_latency_ms(time.time() * 1000 - start)
            metrics.record_opportunity_detected()
            processed += 1

        self.assertEqual(processed, 3)

        # Verify DB has all 3 chains.
        opps = self.repo.get_recent_opportunities(10)
        chains = {o["chain"] for o in opps}
        self.assertIn("ethereum", chains)
        self.assertIn("optimism", chains)
        self.assertIn("base", chains)

        # Verify funnel.
        funnel = self.repo.get_opportunity_funnel()
        self.assertIn("rejected", funnel)  # all rejected (execution_disabled)

        # Verify metrics.
        s = metrics.snapshot()
        self.assertEqual(s["opportunities_detected"], 3)
        self.assertGreater(s["avg_latency_ms"], 0)

        # Verify queue is empty.
        self.assertTrue(queue.is_empty)
        stats = queue.stats()
        self.assertEqual(stats["total_enqueued"], 3)


class RiskPolicyBuilderTests(unittest.TestCase):
    def test_build_risk_policy_uses_config_min_profit_for_configured_chain(self):
        from core.config import BotConfig, DexConfig

        config = BotConfig(
            pair="WETH/USDC",
            dexes=[
                DexConfig(
                    name="Uniswap-Arbitrum",
                    base_price=D("0"),
                    fee_bps=D("5"),
                    volatility_bps=D("0"),
                    chain="arbitrum",
                    dex_type="uniswap_v3",
                )
            ],
            base_asset="WETH",
            quote_asset="USDC",
            trade_size=D("1"),
            min_profit_base=D("0.005"),
            estimated_gas_cost_base=D("0.0002"),
            flash_loan_fee_bps=D("9"),
            flash_loan_provider="aave_v3",
            slippage_bps=D("15"),
            poll_interval_seconds=8.0,
            chain_execution_mode={"arbitrum": "live"},
        )

        policy = build_risk_policy(config)
        self.assertEqual(policy.min_net_profit, D("0.005"))
        self.assertEqual(policy.chain_min_net_profit["arbitrum"], D("0.005"))

    def test_build_risk_policy_keeps_defaults_for_unconfigured_chains(self):
        from core.config import BotConfig, DexConfig

        config = BotConfig(
            pair="WETH/USDC",
            dexes=[
                DexConfig(
                    name="Uniswap-Arbitrum",
                    base_price=D("0"),
                    fee_bps=D("5"),
                    volatility_bps=D("0"),
                    chain="arbitrum",
                    dex_type="uniswap_v3",
                )
            ],
            base_asset="WETH",
            quote_asset="USDC",
            trade_size=D("1"),
            min_profit_base=D("0.005"),
            estimated_gas_cost_base=D("0.0002"),
            flash_loan_fee_bps=D("9"),
            flash_loan_provider="aave_v3",
            slippage_bps=D("15"),
            poll_interval_seconds=8.0,
            chain_execution_mode={"arbitrum": "live"},
        )

        policy = build_risk_policy(config)
        self.assertEqual(policy.chain_min_net_profit["arbitrum"], D("0.005"))
        self.assertEqual(policy.chain_min_net_profit["optimism"], D("0.0002"))


class CrossChainFilterTests(unittest.TestCase):
    """Test that cross-chain opportunities are rejected correctly."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self):
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def _is_cross_chain(self, buy_dex: str, sell_dex: str) -> bool:
        """Same logic as run_live_with_dashboard.py"""
        buy_chain = buy_dex.rsplit("-", 1)[-1].lower() if "-" in buy_dex else buy_dex.lower()
        sell_chain = sell_dex.rsplit("-", 1)[-1].lower() if "-" in sell_dex else sell_dex.lower()
        return buy_chain != sell_chain

    def test_same_chain_is_not_cross_chain(self):
        self.assertFalse(self._is_cross_chain("Uniswap-Ethereum", "SushiSwap-Ethereum"))
        self.assertFalse(self._is_cross_chain("Uniswap-Arbitrum", "SushiSwap-Arbitrum"))

    def test_different_chains_is_cross_chain(self):
        self.assertTrue(self._is_cross_chain("Scroll", "Linea"))
        self.assertTrue(self._is_cross_chain("Uniswap-Ethereum", "SushiSwap-Arbitrum"))

    def test_single_name_dex_same_chain(self):
        """When DEX names don't have a dash, compare directly."""
        self.assertFalse(self._is_cross_chain("Scroll", "Scroll"))
        self.assertFalse(self._is_cross_chain("Ethereum", "Ethereum"))

    def test_cross_chain_recorded_as_rejected_in_db(self):
        """Cross-chain opportunity should be saved to DB with rejected status."""
        opp = _make_opp(
            buy_dex="Scroll", sell_dex="Linea", chain="scroll",
            spread=D("6.2"), profit=D("0.05"),
        )

        # Simulate what run_live_with_dashboard does for cross-chain
        opp_id = self.repo.create_opportunity(
            pair=opp.pair, chain=opp.chain,
            buy_dex=opp.buy_dex, sell_dex=opp.sell_dex,
            spread_bps=opp.gross_spread_pct,
        )
        self.repo.save_pricing(
            opp_id=opp_id,
            input_amount=opp.cost_to_buy_quote,
            estimated_output=opp.proceeds_from_sell_quote,
            fee_cost=opp.dex_fee_cost_quote,
            slippage_cost=opp.slippage_cost_quote,
            gas_estimate=opp.gas_cost_base,
            expected_net_profit=opp.net_profit_base,
        )
        self.repo.save_risk_decision(
            opp_id=opp_id, approved=False,
            reason_code="cross_chain",
            threshold_snapshot="buy_chain=scroll, sell_chain=linea",
        )
        self.repo.update_opportunity_status(opp_id, "rejected")

        # Verify it's in DB as rejected
        db_opp = self.repo.get_opportunity(opp_id)
        self.assertEqual(db_opp["status"], "rejected")

        risk = self.repo.get_risk_decision(opp_id)
        self.assertEqual(risk["reason_code"], "cross_chain")
        self.assertFalse(risk["approved"])

        pricing = self.repo.get_pricing(opp_id)
        self.assertIsNotNone(pricing)

    def test_same_chain_goes_through_pipeline(self):
        """Same-chain opportunity should be processed normally."""
        policy = RiskPolicy(execution_enabled=False, min_net_profit=0)
        pipeline = CandidatePipeline(repo=self.repo, risk_policy=policy)

        opp = _make_opp(
            buy_dex="Uniswap-Arbitrum", sell_dex="SushiSwap-Arbitrum",
            chain="arbitrum", spread=D("0.3"), profit=D("0.003"),
        )

        result = pipeline.process(opp)
        self.assertIn(result.final_status, ("simulation_approved", "rejected", "dry_run"))

        db_opp = self.repo.get_opportunity(result.opportunity_id)
        self.assertIsNotNone(db_opp)
        self.assertEqual(db_opp["chain"], "arbitrum")

    def test_cross_chain_shows_in_funnel(self):
        """Cross-chain rejections should appear in the opportunity funnel."""
        opp = _make_opp(buy_dex="Scroll", sell_dex="Linea", chain="scroll")

        opp_id = self.repo.create_opportunity(
            pair=opp.pair, chain=opp.chain,
            buy_dex=opp.buy_dex, sell_dex=opp.sell_dex,
            spread_bps=opp.gross_spread_pct,
        )
        self.repo.save_risk_decision(
            opp_id=opp_id, approved=False,
            reason_code="cross_chain", threshold_snapshot="",
        )
        self.repo.update_opportunity_status(opp_id, "rejected")

        funnel = self.repo.get_opportunity_funnel()
        self.assertGreater(funnel.get("rejected", 0), 0)


class LiveExecutionStackTests(unittest.TestCase):
    @unittest.mock.patch.dict("os.environ", {}, clear=True)
    def test_build_execution_stack_returns_none_without_env(self):
        from core.config import BotConfig, DexConfig

        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.001, estimated_gas_cost_base=0.002,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=15.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Uniswap", base_price=0, fee_bps=30.0,
                          volatility_bps=0, chain="ethereum", dex_type="uniswap_v3"),
                DexConfig(name="Sushi", base_price=0, fee_bps=30.0,
                          volatility_bps=0, chain="ethereum", dex_type="sushi_v3"),
            ],
        )
        config.validate()

        sim, sub, ver = build_execution_stack(config)
        self.assertIsNone(sim)
        self.assertIsNone(sub)
        self.assertIsNone(ver)

    @unittest.mock.patch("execution.chain_executor.ChainExecutor")
    def test_build_execution_stack_returns_adapters_when_env_present(
        self, mock_exec_cls,
    ):
        from core.config import BotConfig, DexConfig
        from run_event_driven import MultiChainSimulator, MultiChainSubmitter, MultiChainVerifier

        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.001, estimated_gas_cost_base=0.002,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=15.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Uniswap", base_price=0, fee_bps=30.0,
                          volatility_bps=0, chain="ethereum", dex_type="uniswap_v3"),
                DexConfig(name="Sushi", base_price=0, fee_bps=30.0,
                          volatility_bps=0, chain="ethereum", dex_type="sushi_v3"),
            ],
        )
        config.validate()

        mock_exec = MagicMock()
        mock_exec.chain = "ethereum"
        mock_exec_cls.return_value = mock_exec

        with unittest.mock.patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            sim, sub, ver = build_execution_stack(config)

        self.assertIsInstance(sim, MultiChainSimulator)
        self.assertIsInstance(sub, MultiChainSubmitter)
        self.assertIsInstance(ver, MultiChainVerifier)
        self.assertIn("ethereum", sim._by_chain)

    def test_compute_live_execution_summary_prefers_arbitrum_target(self):
        from core.config import BotConfig, DexConfig

        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.001, estimated_gas_cost_base=0.002,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=15.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Uniswap-Arbitrum", base_price=0, fee_bps=30.0,
                          volatility_bps=0, chain="arbitrum", dex_type="uniswap_v3"),
                DexConfig(name="Sushi-Arbitrum", base_price=0, fee_bps=30.0,
                          volatility_bps=0, chain="arbitrum", dex_type="sushi_v3"),
                DexConfig(name="Velodrome-Optimism", base_price=0, fee_bps=20.0,
                          volatility_bps=0, chain="optimism", dex_type="velodrome_v2"),
            ],
        )
        config.validate()

        summary = compute_live_execution_summary(config)
        self.assertEqual(summary["rollout_target"], "arbitrum")
        self.assertIn("arbitrum", summary["executable_chains"])
        self.assertIn("Uniswap-Arbitrum", summary["executable_dex_names"])
        self.assertIn("Sushi-Arbitrum", summary["executable_dex_names"])

    @unittest.mock.patch.dict("os.environ", {
        "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
        "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        "RPC_ARBITRUM": "https://arb.example",
    }, clear=True)
    def test_assess_launch_readiness_ready_for_arbitrum_only_supported_config(self):
        from core.config import BotConfig, DexConfig

        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.001, estimated_gas_cost_base=0.002,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=15.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Uniswap-Arbitrum", base_price=0, fee_bps=30.0,
                          volatility_bps=0, chain="arbitrum", dex_type="uniswap_v3"),
                DexConfig(name="Sushi-Arbitrum", base_price=0, fee_bps=30.0,
                          volatility_bps=0, chain="arbitrum", dex_type="sushi_v3"),
            ],
        )
        config.validate()

        readiness = assess_launch_readiness(config, live_stack_ready=True, target_chain="arbitrum")
        self.assertTrue(readiness["launch_ready"])
        self.assertEqual(readiness["launch_blockers"], [])
        self.assertTrue(readiness["executor_key_configured"])
        self.assertTrue(readiness["executor_contract_configured"])
        self.assertTrue(readiness["rpc_configured"])

    @unittest.mock.patch.dict("os.environ", {}, clear=True)
    def test_assess_launch_readiness_flags_off_target_and_missing_env(self):
        from core.config import BotConfig, DexConfig, PairConfig

        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.001, estimated_gas_cost_base=0.002,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=15.0, poll_interval_seconds=0.0,
            extra_pairs=[
                PairConfig(
                    pair="WETH/USDT",
                    base_asset="WETH",
                    quote_asset="USDT",
                    trade_size=1.0,
                    chain="base",
                )
            ],
            dexes=[
                DexConfig(name="Uniswap-Arbitrum", base_price=0, fee_bps=30.0,
                          volatility_bps=0, chain="arbitrum", dex_type="uniswap_v3"),
                DexConfig(name="Velodrome-Optimism", base_price=0, fee_bps=20.0,
                          volatility_bps=0, chain="optimism", dex_type="velodrome_v2"),
            ],
        )
        config.validate()

        readiness = assess_launch_readiness(config, live_stack_ready=False, target_chain="arbitrum")
        self.assertFalse(readiness["launch_ready"])
        self.assertIn("off_target_dexes:Velodrome-Optimism", readiness["launch_blockers"])
        self.assertIn("off_target_pairs:WETH/USDT", readiness["launch_blockers"])
        self.assertIn("missing_executor_private_key", readiness["launch_blockers"])
        self.assertIn("missing_executor_contract", readiness["launch_blockers"])
        self.assertIn("missing_rpc_arbitrum", readiness["launch_blockers"])
        self.assertIn("live_stack_unavailable", readiness["launch_blockers"])

    def test_enforce_safe_execution_mode_disables_when_not_ready(self):
        policy = RiskPolicy(execution_enabled=True)
        active = enforce_safe_execution_mode(
            policy,
            {"launch_ready": False, "launch_blockers": ["missing_executor_contract"]},
        )
        self.assertFalse(active)
        self.assertFalse(policy.execution_enabled)

    def test_enforce_safe_execution_mode_keeps_enabled_when_ready(self):
        policy = RiskPolicy(execution_enabled=True)
        active = enforce_safe_execution_mode(
            policy,
            {"launch_ready": True, "launch_blockers": []},
        )
        self.assertTrue(active)
        self.assertTrue(policy.execution_enabled)


if __name__ == "__main__":
    unittest.main()
