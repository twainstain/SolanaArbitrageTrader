import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chain_executor import (
    ChainExecutor,
    ChainExecutorError,
    FLASHBOTS_CHAINS,
    FLASHBOTS_RELAY_URL,
    SWAP_ROUTERS,
    AAVE_V3_POOL,
    EXECUTOR_ABI,
)
from config import BotConfig, DexConfig
from models import Opportunity


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
    @patch("chain_executor.Web3")
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

    @patch("chain_executor.Web3")
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


class DynamicPairResolutionTests(unittest.TestCase):
    @patch("chain_executor.Web3")
    def test_build_transaction_resolves_weth_usdc(self, mock_web3_cls) -> None:
        """_build_transaction should resolve WETH/USDC dynamically from config."""
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")
        mock_w3.eth.get_transaction_count.return_value = 0
        mock_w3.eth.gas_price = 30_000_000_000
        mock_w3.to_wei = lambda v, u: v * 1_000_000_000

        # Mock the contract build_transaction call.
        mock_contract = MagicMock()
        mock_contract.functions.executeArbitrage.return_value.build_transaction.return_value = {"data": "0x", "from": "0xfake", "to": "0xcontract"}
        mock_w3.eth.contract.return_value = mock_contract

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(_make_config())
            opp = _make_opportunity()
            tx = executor._build_transaction(opp)

            # Verify the contract was called (no crash from hardcoded resolution).
            mock_contract.functions.executeArbitrage.assert_called_once()

    @patch("chain_executor.Web3")
    def test_build_transaction_fails_for_unknown_asset(self, mock_web3_cls) -> None:
        """Should raise ChainExecutorError for an unresolvable asset."""
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")

        bad_config = BotConfig(
            pair="SHIB/PEPE", base_asset="SHIB", quote_asset="PEPE",
            trade_size=1000.0, min_profit_base=0.001, estimated_gas_cost_base=0.002,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=15.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Uniswap", base_price=0, fee_bps=30.0,
                          volatility_bps=0, chain="ethereum", dex_type="uniswap_v3"),
                DexConfig(name="PancakeSwap", base_price=0, fee_bps=25.0,
                          volatility_bps=0, chain="ethereum", dex_type="pancakeswap_v3"),
            ],
        )
        bad_config.validate()

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(bad_config)
            bad_opp = Opportunity(
                pair="SHIB/PEPE", buy_dex="Uniswap", sell_dex="PancakeSwap",
                trade_size=1000.0, cost_to_buy_quote=1200.0,
                proceeds_from_sell_quote=1210.0, gross_profit_quote=10.0,
                net_profit_quote=8.0, net_profit_base=0.004,
            )
            with self.assertRaises(ChainExecutorError):
                executor._build_transaction(bad_opp)

    @patch("chain_executor.Web3")
    def test_build_transaction_uses_opportunity_pair_assets(self, mock_web3_cls) -> None:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")
        mock_w3.eth.get_transaction_count.return_value = 0
        mock_w3.eth.gas_price = 30_000_000_000
        mock_w3.to_wei = lambda v, u: v * 1_000_000_000

        mock_contract = MagicMock()
        mock_contract.functions.executeArbitrage.return_value.build_transaction.return_value = {
            "data": "0x", "from": "0xfake", "to": "0xcontract"
        }
        mock_w3.eth.contract.return_value = mock_contract

        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.001, estimated_gas_cost_base=0.002,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=15.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Uniswap", base_price=0, fee_bps=30.0,
                          volatility_bps=0, chain="arbitrum", dex_type="uniswap_v3"),
                DexConfig(name="PancakeSwap", base_price=0, fee_bps=25.0,
                          volatility_bps=0, chain="arbitrum", dex_type="pancakeswap_v3"),
            ],
        )
        config.validate()

        opp = Opportunity(
            pair="ARB/USDC", buy_dex="Uniswap", sell_dex="PancakeSwap",
            trade_size=1000.0, cost_to_buy_quote=1200.0,
            proceeds_from_sell_quote=1210.0, gross_profit_quote=10.0,
            net_profit_quote=8.0, net_profit_base=0.004,
        )

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(config)
            executor._build_transaction(opp)

        params = mock_contract.functions.executeArbitrage.call_args[0][0]
        self.assertEqual(params[0], "0x912CE59144191C1204E64559FE8253a0e49E6548")
        self.assertEqual(params[1], "0xaf88d065e77c8cC2239327C5EDb3A432268e5831")

    def test_execute_rejects_cross_chain_opportunity(self) -> None:
        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            with patch("chain_executor.Web3") as mock_web3_cls:
                mock_w3 = MagicMock()
                mock_web3_cls.return_value = mock_w3
                mock_web3_cls.HTTPProvider = MagicMock()
                mock_web3_cls.to_checksum_address = lambda x: x
                mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake_wallet")
                executor = ChainExecutor(_make_config())

            opp = Opportunity(
                pair="WETH/USDC", buy_dex="Uniswap-Ethereum", sell_dex="PancakeSwap-Arbitrum",
                trade_size=1.0, cost_to_buy_quote=2200.0,
                proceeds_from_sell_quote=2210.0, gross_profit_quote=10.0,
                net_profit_quote=8.0, net_profit_base=0.004,
            )
            result = executor.execute(opp)
            self.assertFalse(result.success)
            self.assertEqual(result.reason, "cross_chain_execution_not_supported")


class SolidlyExecutionGuardTests(unittest.TestCase):
    @patch("chain_executor.Web3")
    def test_resolve_router_rejects_velodrome(self, mock_web3_cls) -> None:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")

        config = BotConfig(
            pair="OP/USDC", base_asset="OP", quote_asset="USDC",
            trade_size=250.0, min_profit_base=0.001, estimated_gas_cost_base=0.002,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=15.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Velodrome-Optimism", base_price=0, fee_bps=20.0,
                          volatility_bps=0, chain="optimism", dex_type="velodrome_v2"),
                DexConfig(name="Uniswap-Optimism", base_price=0, fee_bps=5.0,
                          volatility_bps=0, chain="optimism", dex_type="uniswap_v3"),
            ],
        )
        config.validate()

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(config)
            with self.assertRaises(ChainExecutorError) as ctx:
                executor._resolve_router("Velodrome-Optimism")
            self.assertIn("Solidly", str(ctx.exception))

    @patch("chain_executor.Web3")
    def test_resolve_router_rejects_aerodrome(self, mock_web3_cls) -> None:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")

        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.001, estimated_gas_cost_base=0.002,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=15.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Aerodrome-Base", base_price=0, fee_bps=20.0,
                          volatility_bps=0, chain="base", dex_type="aerodrome"),
                DexConfig(name="Uniswap-Base", base_price=0, fee_bps=5.0,
                          volatility_bps=0, chain="base", dex_type="uniswap_v3"),
            ],
        )
        config.validate()

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(config)
            with self.assertRaises(ChainExecutorError) as ctx:
                executor._resolve_router("Aerodrome-Base")
            self.assertIn("Solidly", str(ctx.exception))


class GasEstimationTests(unittest.TestCase):
    @patch("chain_executor.Web3")
    def test_estimate_gas_fees_uses_fee_history(self, mock_web3_cls) -> None:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")
        mock_w3.to_wei = lambda v, u: v * 1_000_000_000

        # Simulate fee_history response.
        mock_w3.eth.fee_history.return_value = {
            "baseFeePerGas": [20_000_000_000, 22_000_000_000, 21_000_000_000],
            "reward": [
                [1_000_000_000, 2_000_000_000, 3_000_000_000],
                [1_500_000_000, 2_500_000_000, 3_500_000_000],
            ],
        }

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(_make_config())
            max_fee, priority_fee = executor._estimate_gas_fees()

            # maxFeePerGas should be > latest baseFee (21 gwei).
            self.assertGreater(max_fee, 21_000_000_000)
            # priority fee should be reasonable (not zero).
            self.assertGreater(priority_fee, 0)

    @patch("chain_executor.Web3")
    def test_estimate_gas_fees_fallback_on_error(self, mock_web3_cls) -> None:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")
        mock_w3.to_wei = lambda v, u: v * 1_000_000_000

        # fee_history raises an error (e.g., unsupported by node).
        mock_w3.eth.fee_history.side_effect = Exception("not supported")
        mock_w3.eth.gas_price = 25_000_000_000

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(_make_config())
            max_fee, priority_fee = executor._estimate_gas_fees()

            # Fallback: gas_price * 1.5
            self.assertEqual(max_fee, int(25_000_000_000 * 1.5))
            self.assertGreater(priority_fee, 0)


class FlashbotsTests(unittest.TestCase):
    def test_ethereum_uses_flashbots(self) -> None:
        """Ethereum mainnet should default to Flashbots private relay."""
        self.assertIn("ethereum", FLASHBOTS_CHAINS)

    def test_non_ethereum_chains_use_public_mempool(self) -> None:
        """Arbitrum, BSC, Base should not use Flashbots."""
        for chain in ("arbitrum", "bsc", "base"):
            self.assertNotIn(chain, FLASHBOTS_CHAINS)

    def test_flashbots_relay_url_is_set(self) -> None:
        self.assertTrue(FLASHBOTS_RELAY_URL.startswith("https://"))

    @patch("chain_executor.Web3")
    def test_executor_enables_flashbots_on_ethereum(self, mock_web3_cls) -> None:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(_make_config())
            self.assertTrue(executor.use_flashbots)

    @patch("chain_executor.Web3")
    def test_executor_disables_flashbots_on_arbitrum(self, mock_web3_cls) -> None:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")

        arb_config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.001, estimated_gas_cost_base=0.002,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=15.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Uniswap", base_price=0, fee_bps=30.0,
                          volatility_bps=0, chain="arbitrum", dex_type="uniswap_v3"),
                DexConfig(name="Sushi", base_price=0, fee_bps=30.0,
                          volatility_bps=0, chain="arbitrum", dex_type="sushi_v3"),
            ],
        )
        arb_config.validate()

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(arb_config)
            self.assertFalse(executor.use_flashbots)


if __name__ == "__main__":
    unittest.main()
