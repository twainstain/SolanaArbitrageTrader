import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arbitrage_bot.config import BotConfig, DexConfig
from arbitrage_bot.event_listener import SwapEventListener, SWAP_EVENT_TOPIC


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


class SwapEventListenerInitTests(unittest.TestCase):
    @patch("arbitrage_bot.event_listener.OnChainMarket")
    @patch("arbitrage_bot.event_listener.Web3")
    def test_creates_with_valid_config(self, mock_web3_cls, mock_market_cls) -> None:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()

        config = _make_onchain_config()
        listener = SwapEventListener(config, dry_run=True)
        self.assertEqual(listener.chain, "ethereum")
        self.assertTrue(listener.dry_run)

    @patch("arbitrage_bot.event_listener.OnChainMarket")
    @patch("arbitrage_bot.event_listener.Web3")
    def test_pools_to_monitor_returns_list(self, mock_web3_cls, mock_market_cls) -> None:
        mock_web3_cls.return_value = MagicMock()
        mock_web3_cls.HTTPProvider = MagicMock()

        config = _make_onchain_config()
        listener = SwapEventListener(config)
        pools = listener.pools_to_monitor
        self.assertIsInstance(pools, list)
        self.assertGreater(len(pools), 0)


class PollOnceTests(unittest.TestCase):
    @patch("arbitrage_bot.event_listener.OnChainMarket")
    @patch("arbitrage_bot.event_listener.Web3")
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

    @patch("arbitrage_bot.event_listener.OnChainMarket")
    @patch("arbitrage_bot.event_listener.Web3")
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


if __name__ == "__main__":
    unittest.main()
