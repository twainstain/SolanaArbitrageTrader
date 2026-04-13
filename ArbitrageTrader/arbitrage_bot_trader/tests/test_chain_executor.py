import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arbitrage_bot.chain_executor import (
    ChainExecutor,
    ChainExecutorError,
    SWAP_ROUTERS,
    AAVE_V3_POOL,
    EXECUTOR_ABI,
)
from arbitrage_bot.config import BotConfig, DexConfig
from arbitrage_bot.models import Opportunity


def _make_config() -> BotConfig:
    config = BotConfig(
        pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
        trade_size=1.0, min_profit_base=0.001, estimated_gas_cost_base=0.002,
        flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
        slippage_bps=15.0, poll_interval_seconds=0.0,
        dexes=[
            DexConfig(name="Uniswap", base_price=0, fee_bps=30.0,
                      volatility_bps=0, chain="ethereum", dex_type="uniswap_v3"),
            DexConfig(name="PancakeSwap", base_price=0, fee_bps=25.0,
                      volatility_bps=0, chain="ethereum", dex_type="pancakeswap_v3"),
        ],
    )
    config.validate()
    return config


def _make_opportunity() -> Opportunity:
    return Opportunity(
        pair="WETH/USDC", buy_dex="Uniswap", sell_dex="PancakeSwap",
        trade_size=1.0, cost_to_buy_quote=2200.0,
        proceeds_from_sell_quote=2210.0, gross_profit_quote=10.0,
        net_profit_quote=8.0, net_profit_base=0.004,
    )


class ChainExecutorInitTests(unittest.TestCase):
    def test_raises_without_private_key(self) -> None:
        with patch.dict("os.environ", {"EXECUTOR_PRIVATE_KEY": "", "EXECUTOR_CONTRACT": "0xfake"}):
            with self.assertRaises(ChainExecutorError, msg="EXECUTOR_PRIVATE_KEY"):
                ChainExecutor(_make_config())

    def test_raises_without_contract_address(self) -> None:
        with patch.dict("os.environ", {"EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32, "EXECUTOR_CONTRACT": ""}):
            with self.assertRaises(ChainExecutorError, msg="EXECUTOR_CONTRACT"):
                ChainExecutor(_make_config())


class SwapRouterRegistryTests(unittest.TestCase):
    def test_ethereum_has_uniswap_and_pancake(self) -> None:
        routers = SWAP_ROUTERS.get("ethereum", {})
        self.assertIn("uniswap_v3", routers)
        self.assertIn("pancakeswap_v3", routers)

    def test_all_routers_are_checksum_length(self) -> None:
        for chain, dexes in SWAP_ROUTERS.items():
            for dex, addr in dexes.items():
                self.assertTrue(addr.startswith("0x"), f"{chain}/{dex}: {addr}")
                self.assertEqual(len(addr), 42, f"{chain}/{dex}: {addr}")


class AavePoolRegistryTests(unittest.TestCase):
    def test_ethereum_pool_exists(self) -> None:
        self.assertIn("ethereum", AAVE_V3_POOL)

    def test_arbitrum_pool_exists(self) -> None:
        self.assertIn("arbitrum", AAVE_V3_POOL)


class ExecutorABITests(unittest.TestCase):
    def test_abi_has_execute_arbitrage(self) -> None:
        names = [f["name"] for f in EXECUTOR_ABI]
        self.assertIn("executeArbitrage", names)


class ResolveRouterTests(unittest.TestCase):
    @patch("arbitrage_bot.chain_executor.Web3")
    def test_resolve_router_finds_matching_dex(self, mock_web3_cls) -> None:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x

        mock_account = MagicMock()
        mock_account.address = "0xfake_wallet"
        mock_w3.eth.account.from_key.return_value = mock_account

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(_make_config())
            router = executor._resolve_router("Uniswap")
            self.assertTrue(router.startswith("0x"))

    @patch("arbitrage_bot.chain_executor.Web3")
    def test_resolve_router_unknown_dex_raises(self, mock_web3_cls) -> None:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x

        mock_account = MagicMock()
        mock_account.address = "0xfake_wallet"
        mock_w3.eth.account.from_key.return_value = mock_account

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(_make_config())
            with self.assertRaises(ChainExecutorError, msg="No swap router"):
                executor._resolve_router("UnknownDEX")


if __name__ == "__main__":
    unittest.main()
