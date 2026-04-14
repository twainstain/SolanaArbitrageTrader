import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import unittest
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import BotConfig, DexConfig, PairConfig
from event_listener import SwapEventListener, SWAP_EVENT_TOPIC, SOLIDLY_SWAP_EVENT_TOPIC
from persistence.db import init_db, close_db
from persistence.repository import Repository


def _make_onchain_config() -> BotConfig:
    config = BotConfig(
        pair="WETH/USDC",
        base_asset="WETH",
        quote_asset="USDC",
        trade_size=1.0,
        min_profit_base=0.0,
        estimated_gas_cost_base=0.0,
        flash_loan_fee_bps=9.0,
        flash_loan_provider="aave_v3",
        slippage_bps=10.0,
        poll_interval_seconds=0.0,
        dexes=[
            DexConfig(name="Uni", base_price=0, fee_bps=30.0, volatility_bps=0,
                      chain="ethereum", dex_type="uniswap_v3"),
            DexConfig(name="Pancake", base_price=0, fee_bps=25.0, volatility_bps=0,
                      chain="ethereum", dex_type="pancakeswap_v3"),
        ],
    )
    config.validate()
    return config


class SwapEventTopicTests(unittest.TestCase):
    def test_topic_is_hex_string(self) -> None:
        self.assertTrue(SWAP_EVENT_TOPIC.startswith("0x"))
        self.assertEqual(len(SWAP_EVENT_TOPIC), 66)  # 0x + 64 hex chars

    def test_solidly_topic_is_hex_string(self) -> None:
        self.assertTrue(SOLIDLY_SWAP_EVENT_TOPIC.startswith("0x"))
        self.assertEqual(len(SOLIDLY_SWAP_EVENT_TOPIC), 66)

    def test_solidly_topic_differs_from_v3(self) -> None:
        self.assertNotEqual(SWAP_EVENT_TOPIC, SOLIDLY_SWAP_EVENT_TOPIC)


class SwapEventListenerInitTests(unittest.TestCase):
    @patch("event_listener.OnChainMarket")
    @patch("event_listener.Web3")
    def test_creates_with_valid_config(self, mock_web3_cls, mock_market_cls) -> None:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()

        config = _make_onchain_config()
        listener = SwapEventListener(config, dry_run=True)
        self.assertEqual(listener.chain, "ethereum")
        self.assertTrue(listener.dry_run)

    @patch("event_listener.OnChainMarket")
    @patch("event_listener.Web3")
    def test_pools_to_monitor_returns_list(self, mock_web3_cls, mock_market_cls) -> None:
        mock_web3_cls.return_value = MagicMock()
        mock_web3_cls.HTTPProvider = MagicMock()

        config = _make_onchain_config()
        listener = SwapEventListener(config)
        pools = listener.pools_to_monitor
        self.assertIsInstance(pools, list)
        self.assertGreater(len(pools), 0)

    @patch("event_listener.OnChainMarket")
    @patch("event_listener.Web3")
    def test_pools_to_monitor_includes_extra_pairs_when_known(self, mock_web3_cls, mock_market_cls) -> None:
        mock_web3_cls.return_value = MagicMock()
        mock_web3_cls.HTTPProvider = MagicMock()

        config = _make_onchain_config()
        object.__setattr__(config, "extra_pairs", [
            PairConfig(pair="WETH/USDT", base_asset="WETH", quote_asset="USDT", trade_size=1.0),
        ])
        listener = SwapEventListener(config)
        pools = listener.pools_to_monitor
        self.assertGreaterEqual(len(pools), 3)

    @patch("event_listener.OnChainMarket")
    @patch("event_listener.Web3")
    def test_market_and_scanner_receive_pair_aware_config(self, mock_web3_cls, mock_market_cls) -> None:
        mock_web3_cls.return_value = MagicMock()
        mock_web3_cls.HTTPProvider = MagicMock()

        config = _make_onchain_config()
        object.__setattr__(config, "extra_pairs", [
            PairConfig(
                pair="OP/USDC",
                base_asset="OP",
                quote_asset="USDC",
                trade_size=250.0,
                chain="optimism",
            ),
        ])

        listener = SwapEventListener(config)

        self.assertEqual([p.pair for p in listener._pairs], ["WETH/USDC", "OP/USDC"])
        market_pairs = mock_market_cls.call_args.kwargs["pairs"]
        self.assertEqual([p.pair for p in market_pairs], ["WETH/USDC", "OP/USDC"])
        self.assertEqual(listener.scanner.strategy._pair_configs["OP/USDC"].chain, "optimism")

    @patch("event_listener.OnChainMarket")
    @patch("event_listener.Web3")
    def test_pools_to_monitor_prefers_repository_metadata(self, mock_web3_cls, mock_market_cls) -> None:
        mock_web3_cls.return_value = MagicMock()
        mock_web3_cls.HTTPProvider = MagicMock()

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        conn = init_db(tmp.name)
        repo = Repository(conn)
        try:
            pair_id = repo.save_pair("WETH/USDC", "ethereum", "WETH", "USDC")
            repo.save_pool(pair_id, "ethereum", "Uni", "0xdeadbeef")

            config = _make_onchain_config()
            listener = SwapEventListener(config, repository=repo)
            pools = listener.pools_to_monitor
            self.assertEqual(pools, ["0xdeadbeef"])
        finally:
            close_db()
            Path(tmp.name).unlink(missing_ok=True)


class PollOnceTests(unittest.TestCase):
    @patch("event_listener.OnChainMarket")
    @patch("event_listener.Web3")
    def test_no_new_blocks_does_nothing(self, mock_web3_cls, mock_market_cls) -> None:
        mock_w3 = MagicMock()
        mock_w3.eth.block_number = 100
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x

        config = _make_onchain_config()
        listener = SwapEventListener(config)
        listener._last_block = 100

        listener._poll_once(["0xfake"])
        # No logs fetched since no new blocks.
        mock_w3.eth.get_logs.assert_not_called()

    @patch("event_listener.OnChainMarket")
    @patch("event_listener.Web3")
    def test_swap_detected_increments_count(self, mock_web3_cls, mock_market_cls) -> None:
        mock_w3 = MagicMock()
        mock_w3.eth.block_number = 105
        mock_w3.eth.get_logs.return_value = [{"fake": "log"}, {"fake": "log2"}]
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x

        # Mock market to return no opportunity.
        mock_market = MagicMock()
        mock_market.get_quotes.return_value = []
        mock_market_cls.return_value = mock_market

        config = _make_onchain_config()
        listener = SwapEventListener(config)
        listener._last_block = 100
        listener.market = mock_market

        listener._poll_once(["0xfake"])

        self.assertEqual(listener._swap_count, 2)
        self.assertEqual(listener._last_block, 105)


class ScannerIntegrationTests(unittest.TestCase):
    """Verify that event listener uses OpportunityScanner for ranking/filtering."""

    @patch("event_listener.OnChainMarket")
    @patch("event_listener.Web3")
    def test_uses_scanner_not_raw_strategy(self, mock_web3_cls, mock_market_cls) -> None:
        """The listener should use scanner.scan_and_rank(), not strategy.find_best_opportunity()."""
        mock_web3_cls.return_value = MagicMock()
        mock_web3_cls.HTTPProvider = MagicMock()

        config = _make_onchain_config()
        listener = SwapEventListener(config)

        # Verify it has a scanner attribute, not just a strategy
        from scanner import OpportunityScanner
        self.assertIsInstance(listener.scanner, OpportunityScanner)
        self.assertFalse(hasattr(listener, "strategy"))

    @patch("event_listener.OnChainMarket")
    @patch("event_listener.Web3")
    def test_scanner_filters_low_quality_opportunities(self, mock_web3_cls, mock_market_cls) -> None:
        """Opportunities with too many warning flags should be rejected by the scanner."""
        from models import MarketQuote

        mock_w3 = MagicMock()
        mock_w3.eth.block_number = 105
        mock_w3.eth.get_logs.return_value = [{"fake": "log"}]
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x

        # Return quotes with low liquidity (will trigger warning flags).
        mock_market = MagicMock()
        mock_market.get_quotes.return_value = [
            MarketQuote(dex="Uni", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0,
                        fee_bps=0.0, liquidity_usd=10_000, volume_usd=5_000),
            MarketQuote(dex="Pancake", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0,
                        fee_bps=0.0, liquidity_usd=10_000, volume_usd=5_000),
        ]
        mock_market_cls.return_value = mock_market

        # Config with strict flag filtering (max 0 flags).
        config = _make_onchain_config()
        listener = SwapEventListener(config)
        listener._last_block = 100
        listener.market = mock_market
        # Override scanner to be strict about flags.
        from scanner import OpportunityScanner
        listener.scanner = OpportunityScanner(
            config, alert_max_warning_flags=0,
        )

        listener._poll_once(["0xfake"])

        # Despite a profitable spread, low_liquidity + thin_market flags
        # should cause rejection.
        self.assertEqual(listener._opportunity_count, 0)

    @patch("event_listener.OnChainMarket")
    @patch("event_listener.Web3")
    def test_scanner_tracks_history(self, mock_web3_cls, mock_market_cls) -> None:
        """Each poll should add to the scanner's scan history."""
        from models import MarketQuote

        mock_w3 = MagicMock()
        mock_w3.eth.block_number = 105
        mock_w3.eth.get_logs.return_value = [{"fake": "log"}]
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x

        mock_market = MagicMock()
        mock_market.get_quotes.return_value = [
            MarketQuote(dex="Uni", pair="WETH/USDC", buy_price=3001.0, sell_price=2999.0, fee_bps=0.0),
            MarketQuote(dex="Pancake", pair="WETH/USDC", buy_price=3081.0, sell_price=3079.0, fee_bps=0.0),
        ]
        mock_market_cls.return_value = mock_market

        config = _make_onchain_config()
        listener = SwapEventListener(config)
        listener._last_block = 100
        listener.market = mock_market

        listener._poll_once(["0xfake"])

        self.assertEqual(len(listener.scanner.recent_history), 1)


class MonitoredPoolsExpandedTests(unittest.TestCase):
    def test_optimism_has_weth_usdc(self) -> None:
        from registry.monitored_pools import MONITORED_POOLS
        self.assertIn("optimism", MONITORED_POOLS)
        self.assertIn("WETH/USDC", MONITORED_POOLS["optimism"])
        self.assertGreater(len(MONITORED_POOLS["optimism"]["WETH/USDC"]), 0)

    def test_optimism_has_op_usdc(self) -> None:
        from registry.monitored_pools import MONITORED_POOLS
        self.assertIn("OP/USDC", MONITORED_POOLS["optimism"])
        self.assertGreater(len(MONITORED_POOLS["optimism"]["OP/USDC"]), 0)


if __name__ == "__main__":
    unittest.main()
