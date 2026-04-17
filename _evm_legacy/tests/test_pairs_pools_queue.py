"""Tests for DB-backed pairs/pools tables and candidate queue."""

import sys
import tempfile
from decimal import Decimal
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persistence.db import init_db, close_db
from persistence.repository import Repository
from platform_adapters import CandidateQueue
from core.models import Opportunity
from registry.monitored_pools import sync_monitored_pools

D = Decimal


# ---------------------------------------------------------------
# Pairs table tests
# ---------------------------------------------------------------

class PairTableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self):
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_save_and_get_pair(self):
        pid = self.repo.save_pair(
            pair="WETH/USDC", chain="ethereum",
            base_token="WETH", quote_token="USDC",
            base_decimals=18, quote_decimals=6,
        )
        self.assertIsNotNone(pid)
        p = self.repo.get_pair_on_chain("WETH/USDC", "ethereum")
        self.assertIsNotNone(p)
        self.assertEqual(p["chain"], "ethereum")
        self.assertEqual(p["base_decimals"], 18)

    def test_get_enabled_pairs(self):
        self.repo.save_pair("WETH/USDC", "ethereum", "WETH", "USDC")
        self.repo.save_pair("WETH/USDT", "ethereum", "WETH", "USDT")
        enabled = self.repo.get_enabled_pairs()
        self.assertEqual(len(enabled), 2)

    def test_disable_pair(self):
        self.repo.save_pair("WETH/USDC", "ethereum", "WETH", "USDC")
        self.repo.set_pair_enabled("WETH/USDC", False, chain="ethereum")
        enabled = self.repo.get_enabled_pairs()
        self.assertEqual(len(enabled), 0)

    def test_duplicate_pair_ignored(self):
        self.repo.save_pair("WETH/USDC", "ethereum", "WETH", "USDC")
        self.repo.save_pair("WETH/USDC", "ethereum", "WETH", "USDC")
        enabled = self.repo.get_enabled_pairs()
        self.assertEqual(len(enabled), 1)

    def test_same_pair_can_exist_on_multiple_chains(self):
        eth_id = self.repo.save_pair("WETH/USDC", "ethereum", "WETH", "USDC")
        arb_id = self.repo.save_pair("WETH/USDC", "arbitrum", "WETH", "USDC")
        self.assertNotEqual(eth_id, arb_id)

        enabled = self.repo.get_enabled_pairs()
        self.assertEqual(len(enabled), 2)
        self.assertEqual(
            {row["chain"] for row in enabled if row["pair"] == "WETH/USDC"},
            {"ethereum", "arbitrum"},
        )

    def test_disable_pair_only_on_requested_chain(self):
        self.repo.save_pair("WETH/USDC", "ethereum", "WETH", "USDC")
        self.repo.save_pair("WETH/USDC", "arbitrum", "WETH", "USDC")
        self.repo.set_pair_enabled("WETH/USDC", False, chain="ethereum")

        enabled = self.repo.get_enabled_pairs()
        self.assertEqual(len(enabled), 1)
        self.assertEqual(enabled[0]["chain"], "arbitrum")

    def test_get_nonexistent_pair(self):
        self.assertIsNone(self.repo.get_pair("FAKE/PAIR"))


# ---------------------------------------------------------------
# Pools table tests
# ---------------------------------------------------------------

class PoolTableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self):
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_save_and_get_pool(self):
        pair_id = self.repo.save_pair("WETH/USDC", "ethereum", "WETH", "USDC")
        pool_id = self.repo.save_pool(
            pair_id=pair_id, chain="ethereum", dex="Uniswap",
            address="0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640",
            fee_tier_bps=D("5"), dex_type="uniswap_v3",
            liquidity_class="high",
        )
        self.assertIsNotNone(pool_id)

        pools = self.repo.get_pools_for_pair(pair_id)
        self.assertEqual(len(pools), 1)
        self.assertEqual(pools[0]["dex"], "Uniswap")
        self.assertEqual(pools[0]["fee_tier_bps"], "5")

    def test_multiple_pools_per_pair(self):
        pair_id = self.repo.save_pair("WETH/USDC", "ethereum", "WETH", "USDC")
        self.repo.save_pool(pair_id, "ethereum", "Uniswap", "0xaaa", D("5"))
        self.repo.save_pool(pair_id, "ethereum", "PancakeSwap", "0xbbb", D("25"))
        pools = self.repo.get_pools_for_pair(pair_id)
        self.assertEqual(len(pools), 2)

    def test_disable_pool(self):
        pair_id = self.repo.save_pair("WETH/USDC", "ethereum", "WETH", "USDC")
        pool_id = self.repo.save_pool(pair_id, "ethereum", "Uni", "0xaaa")
        self.repo.set_pool_enabled(pool_id, False)
        pools = self.repo.get_pools_for_pair(pair_id)
        self.assertEqual(len(pools), 0)

    def test_save_pool_if_missing_is_idempotent(self):
        pair_id = self.repo.save_pair("WETH/USDC", "ethereum", "WETH", "USDC")
        created = self.repo.save_pool_if_missing(pair_id, "ethereum", "Uni", "0xaaa")
        skipped = self.repo.save_pool_if_missing(pair_id, "ethereum", "Uni", "0xaaa")
        self.assertIsNotNone(created)
        self.assertIsNone(skipped)
        pools = self.repo.get_pools_for_pair(pair_id)
        self.assertEqual(len(pools), 1)


class MonitoredPoolBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self):
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_sync_monitored_pools_inserts_bootstrap_metadata(self):
        inserted = sync_monitored_pools(self.repo)
        self.assertGreater(inserted, 0)
        pair = self.repo.get_pair_on_chain("WETH/USDC", "ethereum")
        self.assertIsNotNone(pair)
        pools = self.repo.get_enabled_pools_for_pair_name("WETH/USDC", chain="ethereum")
        self.assertGreaterEqual(len(pools), 2)

    def test_sync_monitored_pools_is_idempotent(self):
        first = sync_monitored_pools(self.repo)
        second = sync_monitored_pools(self.repo)
        self.assertGreater(first, 0)
        self.assertEqual(second, 0)

    def test_count_enabled_pools(self):
        sync_monitored_pools(self.repo)
        self.assertGreater(self.repo.count_enabled_pools(), 0)
        self.assertGreater(self.repo.count_enabled_pools(chain="ethereum"), 0)

    def test_sync_monitored_pools_creates_distinct_pair_rows_per_chain(self):
        sync_monitored_pools(self.repo)
        eth = self.repo.get_pair_on_chain("WETH/USDC", "ethereum")
        arb = self.repo.get_pair_on_chain("WETH/USDC", "arbitrum")
        self.assertIsNotNone(eth)
        self.assertIsNotNone(arb)
        self.assertNotEqual(eth["pair_id"], arb["pair_id"])


# ---------------------------------------------------------------
# Candidate Queue tests
# ---------------------------------------------------------------

def _make_opp(profit: float = 0.005) -> Opportunity:
    return Opportunity(
        pair="WETH/USDC", buy_dex="A", sell_dex="B",
        trade_size=D("1"), cost_to_buy_quote=D("2200"),
        proceeds_from_sell_quote=D("2210"), gross_profit_quote=D("10"),
        net_profit_quote=D("8"), net_profit_base=D(str(profit)),
    )


class QueueBasicTests(unittest.TestCase):
    def test_push_and_pop(self):
        q = CandidateQueue(max_size=10)
        q.push(_make_opp(), priority=1.0)
        self.assertEqual(q.size, 1)
        c = q.pop()
        self.assertIsNotNone(c)
        self.assertEqual(c.opportunity.pair, "WETH/USDC")
        self.assertEqual(q.size, 0)

    def test_pop_empty_returns_none(self):
        q = CandidateQueue()
        self.assertIsNone(q.pop())

    def test_is_empty(self):
        q = CandidateQueue()
        self.assertTrue(q.is_empty)
        q.push(_make_opp())
        self.assertFalse(q.is_empty)


class QueuePriorityTests(unittest.TestCase):
    def test_pops_highest_priority_first(self):
        q = CandidateQueue()
        q.push(_make_opp(0.001), priority=1.0)
        q.push(_make_opp(0.010), priority=5.0)
        q.push(_make_opp(0.005), priority=3.0)
        c = q.pop()
        self.assertEqual(c.priority, 5.0)

    def test_pop_batch(self):
        q = CandidateQueue()
        for i in range(5):
            q.push(_make_opp(), priority=float(i))
        batch = q.pop_batch(3)
        self.assertEqual(len(batch), 3)
        self.assertEqual(batch[0].priority, 4.0)  # highest first
        self.assertEqual(q.size, 2)


class QueueBackpressureTests(unittest.TestCase):
    def test_drops_lowest_when_full(self):
        q = CandidateQueue(max_size=3)
        q.push(_make_opp(), priority=1.0)
        q.push(_make_opp(), priority=2.0)
        q.push(_make_opp(), priority=3.0)
        # Queue is full. Push with priority 5 — should drop priority 1.
        ok = q.push(_make_opp(), priority=5.0)
        self.assertTrue(ok)
        self.assertEqual(q.size, 3)
        stats = q.stats()
        self.assertEqual(stats["total_dropped"], 1)

    def test_drops_new_if_lowest_priority(self):
        q = CandidateQueue(max_size=2)
        q.push(_make_opp(), priority=5.0)
        q.push(_make_opp(), priority=3.0)
        ok = q.push(_make_opp(), priority=1.0)
        self.assertFalse(ok)  # new candidate was dropped
        self.assertEqual(q.size, 2)


class QueueStatsTests(unittest.TestCase):
    def test_stats(self):
        q = CandidateQueue(max_size=5)
        q.push(_make_opp(), priority=1.0)
        q.push(_make_opp(), priority=2.0)
        s = q.stats()
        self.assertEqual(s["current_size"], 2)
        self.assertEqual(s["max_size"], 5)
        self.assertEqual(s["total_enqueued"], 2)

    def test_clear(self):
        q = CandidateQueue()
        q.push(_make_opp())
        q.push(_make_opp())
        cleared = q.clear()
        self.assertEqual(cleared, 2)
        self.assertTrue(q.is_empty)


if __name__ == "__main__":
    unittest.main()
