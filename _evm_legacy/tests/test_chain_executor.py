import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from execution.chain_executor import (
    ChainExecutor,
    ChainExecutorError,
    FLASHBOTS_CHAINS,
    FLASHBOTS_RELAY_URL,
    SWAP_ROUTERS,
    AAVE_V3_POOL,
    EXECUTOR_ABI,
    VELO_FACTORIES,
    SUPPORTED_LIVE_DEX_TYPES,
    SWAP_TYPE_V3,
    SWAP_TYPE_VELO,
    SWAP_TYPE_V3_02,
    V3_DEX_TYPES,
    V3_02_ROUTERS,
    VELO_DEX_TYPES,
)
from core.config import BotConfig, DexConfig
from core.models import Opportunity


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
    @patch("execution.chain_executor.Web3")
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

    @patch("execution.chain_executor.Web3")
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
    @patch("execution.chain_executor.Web3")
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

    @patch("execution.chain_executor.Web3")
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

    @patch("execution.chain_executor.Web3")
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
            with patch("execution.chain_executor.Web3") as mock_web3_cls:
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


class VelodromeExecutionTests(unittest.TestCase):
    """Tests for Velodrome/Aerodrome (Solidly-fork) execution support."""

    def test_velodrome_in_supported_types(self) -> None:
        self.assertIn("velodrome_v2", SUPPORTED_LIVE_DEX_TYPES)
        self.assertIn("aerodrome", SUPPORTED_LIVE_DEX_TYPES)

    def test_velo_dex_types_classification(self) -> None:
        self.assertIn("velodrome_v2", VELO_DEX_TYPES)
        self.assertIn("aerodrome", VELO_DEX_TYPES)
        self.assertNotIn("uniswap_v3", VELO_DEX_TYPES)

    def test_v3_dex_types_classification(self) -> None:
        self.assertIn("uniswap_v3", V3_DEX_TYPES)
        self.assertIn("sushi_v3", V3_DEX_TYPES)
        self.assertNotIn("velodrome_v2", V3_DEX_TYPES)

    def test_swap_type_constants(self) -> None:
        self.assertEqual(SWAP_TYPE_V3, 0)
        self.assertEqual(SWAP_TYPE_VELO, 1)

    def test_optimism_has_velodrome_router(self) -> None:
        routers = SWAP_ROUTERS.get("optimism", {})
        self.assertIn("velodrome_v2", routers)
        self.assertTrue(routers["velodrome_v2"].startswith("0x"))

    def test_base_has_aerodrome_router(self) -> None:
        routers = SWAP_ROUTERS.get("base", {})
        self.assertIn("aerodrome", routers)

    def test_optimism_has_velodrome_factory(self) -> None:
        factories = VELO_FACTORIES.get("optimism", {})
        self.assertIn("velodrome_v2", factories)
        self.assertTrue(factories["velodrome_v2"].startswith("0x"))

    def test_base_has_aerodrome_factory(self) -> None:
        factories = VELO_FACTORIES.get("base", {})
        self.assertIn("aerodrome", factories)

    def test_optimism_has_aave_pool(self) -> None:
        self.assertIn("optimism", AAVE_V3_POOL)

    def test_executor_abi_has_new_fields(self) -> None:
        """ABI must include swapTypeA/B, factoryA/B, stableA/B."""
        func = EXECUTOR_ABI[0]
        param_names = [c["name"] for c in func["inputs"][0]["components"]]
        for field in ["swapTypeA", "swapTypeB", "factoryA", "factoryB", "stableA", "stableB"]:
            self.assertIn(field, param_names, f"Missing ABI field: {field}")

    @patch("execution.chain_executor.Web3")
    def test_resolve_router_finds_velodrome(self, mock_web3_cls) -> None:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")

        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=0.1, min_profit_base=0.001, estimated_gas_cost_base=0.0001,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=30.0, poll_interval_seconds=0.0,
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
            router = executor._resolve_router("Velodrome-Optimism")
            self.assertEqual(router, SWAP_ROUTERS["optimism"]["velodrome_v2"])

    @patch("execution.chain_executor.Web3")
    def test_resolve_velo_factory(self, mock_web3_cls) -> None:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")

        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=0.1, min_profit_base=0.001, estimated_gas_cost_base=0.0001,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=30.0, poll_interval_seconds=0.0,
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
            factory = executor._resolve_velo_factory("Velodrome-Optimism")
            self.assertEqual(factory, VELO_FACTORIES["optimism"]["velodrome_v2"])

    @patch("execution.chain_executor.Web3")
    def test_supports_live_execution_accepts_velo_v3_mix(self, mock_web3_cls) -> None:
        """A Velodrome buy + V3 sell should be supported."""
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")

        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=0.1, min_profit_base=0.001, estimated_gas_cost_base=0.0001,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=30.0, poll_interval_seconds=0.0,
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

        opp = Opportunity(
            pair="WETH/USDC", buy_dex="Velodrome-Optimism", sell_dex="Uniswap-Optimism",
            trade_size=0.1, cost_to_buy_quote=230.0,
            proceeds_from_sell_quote=234.0, gross_profit_quote=4.0,
            net_profit_quote=3.0, net_profit_base=0.0013,
        )
        supported, reason = executor._supports_live_execution(opp)
        self.assertTrue(supported)
        self.assertEqual(reason, "ok")

    @patch("execution.chain_executor.Web3")
    def test_build_transaction_with_velodrome(self, mock_web3_cls) -> None:
        """_build_transaction should pass correct swap types for Velo+V3 mix."""
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")
        mock_w3.eth.get_transaction_count.return_value = 0
        mock_w3.eth.gas_price = 1_000_000
        mock_w3.to_wei = lambda v, u: v * 1_000_000_000

        mock_contract = MagicMock()
        mock_contract.functions.executeArbitrage.return_value.build_transaction.return_value = {
            "data": "0x", "from": "0xfake", "to": "0xcontract"
        }
        mock_w3.eth.contract.return_value = mock_contract

        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=0.1, min_profit_base=0.001, estimated_gas_cost_base=0.0001,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=30.0, poll_interval_seconds=0.0,
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

        opp = Opportunity(
            pair="WETH/USDC", buy_dex="Velodrome-Optimism", sell_dex="Uniswap-Optimism",
            trade_size=0.1, cost_to_buy_quote=230.0,
            proceeds_from_sell_quote=234.0, gross_profit_quote=4.0,
            net_profit_quote=3.0, net_profit_base=0.0013,
        )
        executor._build_transaction(opp)

        params = mock_contract.functions.executeArbitrage.call_args[0][0]
        # swapTypeA=1 (Velo), swapTypeB=0 (V3)
        self.assertEqual(params[8], SWAP_TYPE_VELO)   # swapTypeA
        self.assertEqual(params[9], SWAP_TYPE_V3)     # swapTypeB
        # factoryA should be Velodrome factory
        self.assertEqual(params[10], VELO_FACTORIES["optimism"]["velodrome_v2"])
        # stableA=False, stableB=False
        self.assertFalse(params[12])
        self.assertFalse(params[13])

    @patch("execution.chain_executor.Web3")
    def test_curve_still_unsupported(self, mock_web3_cls) -> None:
        """Curve should still be rejected."""
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")

        config = BotConfig(
            pair="USDT/USDC", base_asset="USDT", quote_asset="USDC",
            trade_size=1000.0, min_profit_base=0.001, estimated_gas_cost_base=0.002,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=15.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Curve-Ethereum", base_price=0, fee_bps=1.0,
                          volatility_bps=0, chain="ethereum", dex_type="curve"),
                DexConfig(name="Uniswap-Ethereum", base_price=0, fee_bps=5.0,
                          volatility_bps=0, chain="ethereum", dex_type="uniswap_v3"),
            ],
        )
        config.validate()

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(config)
            opp = Opportunity(
                pair="USDT/USDC", buy_dex="Curve-Ethereum", sell_dex="Uniswap-Ethereum",
                trade_size=1000.0, cost_to_buy_quote=1000.0,
                proceeds_from_sell_quote=1001.0, gross_profit_quote=1.0,
                net_profit_quote=0.5, net_profit_base=0.0002,
            )
            supported, reason = executor._supports_live_execution(opp)
            self.assertFalse(supported)
            self.assertIn("curve", reason)


class GasEstimationTests(unittest.TestCase):
    @patch("execution.chain_executor.Web3")
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

    @patch("execution.chain_executor.Web3")
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

    @patch("execution.chain_executor.Web3")
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

    @patch("execution.chain_executor.Web3")
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


class ExecuteFlowTests(unittest.TestCase):
    """Test the full execute() flow: simulate → sign → send → receipt."""

    def _make_executor(self, mock_web3_cls):
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_web3_cls.keccak = lambda data: b"\xaa" * 32
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake_wallet")
        mock_w3.to_wei = lambda v, u: v * 1_000_000_000

        mock_contract = MagicMock()
        call_data = MagicMock()
        call_data.estimate_gas.return_value = 300_000
        call_data.build_transaction.return_value = {
            "data": "0xcalldata", "from": "0xfake_wallet",
            "to": "0xcontract", "nonce": 0, "gas": 360_000,
            "maxFeePerGas": 50_000_000_000, "maxPriorityFeePerGas": 2_000_000_000,
        }
        mock_contract.functions.executeArbitrage.return_value = call_data
        mock_w3.eth.contract.return_value = mock_contract
        mock_w3.eth.get_transaction_count.return_value = 42
        mock_w3.eth.fee_history.return_value = {
            "baseFeePerGas": [20_000_000_000],
            "reward": [[1_000_000_000, 2_000_000_000, 3_000_000_000]],
        }
        # sign_transaction returns an object with rawTransaction.
        signed = MagicMock()
        signed.rawTransaction = b"\xcc" * 100
        mock_w3.eth.account.sign_transaction.return_value = signed

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(_make_config())
        return executor, mock_w3

    @patch("execution.chain_executor.Web3")
    def test_execute_success(self, mock_web3_cls) -> None:
        executor, mock_w3 = self._make_executor(mock_web3_cls)
        mock_w3.eth.call.return_value = b""
        mock_w3.eth.send_raw_transaction.return_value = b"\xbb" * 32
        mock_w3.eth.wait_for_transaction_receipt.return_value = {
            "status": 1, "blockNumber": 12345,
        }

        result = executor.execute(_make_opportunity())
        self.assertTrue(result.success)
        self.assertIn("tx:", result.reason)

    @patch("execution.chain_executor.Web3")
    def test_execute_simulation_failure_skips(self, mock_web3_cls) -> None:
        executor, mock_w3 = self._make_executor(mock_web3_cls)
        mock_w3.eth.call.side_effect = Exception("execution reverted: profit below minimum")

        result = executor.execute(_make_opportunity())
        self.assertFalse(result.success)
        self.assertIn("simulation_failed", result.reason)
        self.assertIn("profit_below_minimum", result.reason)
        mock_w3.eth.send_raw_transaction.assert_not_called()

    @patch("execution.chain_executor.Web3")
    def test_execute_tx_reverted(self, mock_web3_cls) -> None:
        executor, mock_w3 = self._make_executor(mock_web3_cls)
        mock_w3.eth.call.return_value = b""
        mock_w3.eth.send_raw_transaction.return_value = b"\xbb" * 32
        mock_w3.eth.wait_for_transaction_receipt.return_value = {
            "status": 0, "blockNumber": 12345,
        }

        result = executor.execute(_make_opportunity())
        self.assertFalse(result.success)
        self.assertIn("tx_reverted", result.reason)

    @patch("execution.chain_executor.Web3")
    def test_execute_exception_returns_error(self, mock_web3_cls) -> None:
        executor, mock_w3 = self._make_executor(mock_web3_cls)
        # Simulation passes but send_raw_transaction raises.
        mock_w3.eth.call.return_value = b""
        mock_w3.eth.send_raw_transaction.side_effect = ConnectionError("RPC down")

        result = executor.execute(_make_opportunity())
        self.assertFalse(result.success)
        self.assertIn("error:", result.reason)
        self.assertEqual(result.realized_profit_base, 0)


class SimulateTransactionTests(unittest.TestCase):
    @patch("execution.chain_executor.Web3")
    def test_simulate_success(self, mock_web3_cls) -> None:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")
        mock_w3.eth.call.return_value = b""

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(_make_config())

        ok, reason = executor._simulate_transaction({
            "from": "0xfake", "to": "0xcontract", "data": "0x", "value": 0,
        })
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    @patch("execution.chain_executor.Web3")
    def test_simulate_revert_extracts_reason(self, mock_web3_cls) -> None:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")
        mock_w3.eth.call.side_effect = Exception("execution reverted: Profit Below Minimum")

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(_make_config())

        ok, reason = executor._simulate_transaction({
            "from": "0xfake", "to": "0xcontract", "data": "0x",
        })
        self.assertFalse(ok)
        self.assertEqual(reason, "profit_below_minimum")


class ResolveFeeTests(unittest.TestCase):
    @patch("execution.chain_executor.Web3")
    def test_resolve_fee_converts_bps_to_tier(self, mock_web3_cls) -> None:
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

        # 30 bps → 3000, 25 bps → 2500
        self.assertEqual(executor._resolve_fee("Uniswap"), 3000)
        self.assertEqual(executor._resolve_fee("PancakeSwap"), 2500)

    @patch("execution.chain_executor.Web3")
    def test_resolve_fee_default_for_unknown(self, mock_web3_cls) -> None:
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

        self.assertEqual(executor._resolve_fee("UnknownDEX"), 3000)


class ChainMismatchGuardTests(unittest.TestCase):
    """ChainExecutor must reject opportunities targeting a different chain."""

    @patch("execution.chain_executor.Web3")
    def test_rejects_base_opp_on_ethereum_executor(self, mock_web3_cls) -> None:
        """An Ethereum executor must reject a Base opportunity."""
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(_make_config())  # chain=ethereum
            self.assertEqual(executor.chain, "ethereum")

        opp = Opportunity(
            pair="WETH/USDC", buy_dex="Sushi-Base", sell_dex="Uniswap-Base",
            trade_size=1.0, cost_to_buy_quote=2200.0,
            proceeds_from_sell_quote=2210.0, gross_profit_quote=10.0,
            net_profit_quote=8.0, net_profit_base=0.004,
            chain="base",
        )
        result = executor.execute(opp)
        self.assertFalse(result.success)
        self.assertIn("chain_mismatch", result.reason)

    @patch("execution.chain_executor.Web3")
    def test_accepts_matching_chain(self, mock_web3_cls) -> None:
        """Ethereum executor should NOT reject an Ethereum opportunity."""
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")
        mock_w3.eth.call.side_effect = Exception("revert for test")

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(_make_config())

        opp = Opportunity(
            pair="WETH/USDC", buy_dex="Uniswap", sell_dex="PancakeSwap",
            trade_size=1.0, cost_to_buy_quote=2200.0,
            proceeds_from_sell_quote=2210.0, gross_profit_quote=10.0,
            net_profit_quote=8.0, net_profit_base=0.004,
            chain="ethereum",
        )
        result = executor.execute(opp)
        # Won't be chain_mismatch — will fail at simulation (expected)
        self.assertNotIn("chain_mismatch", result.reason)

    @patch("execution.chain_executor.Web3")
    def test_explicit_chain_param(self, mock_web3_cls) -> None:
        """ChainExecutor(config, chain='base') should use Base chain."""
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT_BASE": "0x" + "ee" * 20,
        }):
            executor = ChainExecutor(_make_config(), chain="base")
            self.assertEqual(executor.chain, "base")
            self.assertEqual(executor.contract_address, "0x" + "ee" * 20)


class ExplicitChainParamTests(unittest.TestCase):
    """ChainExecutor should accept an explicit chain parameter."""

    @patch("execution.chain_executor.Web3")
    def test_chain_param_overrides_config(self, mock_web3_cls) -> None:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT_ARBITRUM": "0x" + "ff" * 20,
        }):
            # Config has ethereum dexes, but chain="arbitrum" is explicit
            executor = ChainExecutor(_make_config(), chain="arbitrum")
            self.assertEqual(executor.chain, "arbitrum")
            self.assertFalse(executor.use_flashbots)  # Arbitrum = no flashbots

    @patch("execution.chain_executor.Web3")
    def test_chain_param_none_uses_config(self, mock_web3_cls) -> None:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(_make_config(), chain=None)
            self.assertEqual(executor.chain, "ethereum")


class SwapRouter02Tests(unittest.TestCase):
    """Tests for SwapRouter02 support (Base chain uses a different interface)."""

    def test_swap_type_v3_02_constant(self) -> None:
        self.assertEqual(SWAP_TYPE_V3_02, 2)

    def test_uniswap_base_in_v3_02_routers(self) -> None:
        self.assertIn("0x2626664c2603336E57B271c5C0b26F421741e481", V3_02_ROUTERS)

    def test_ethereum_uniswap_not_v3_02(self) -> None:
        self.assertNotIn("0xE592427A0AEce92De3Edee1F18E0157C05861564", V3_02_ROUTERS)

    def test_sushi_base_not_v3_02(self) -> None:
        self.assertNotIn("0xFB7eF66a7e61224DD6FcD0D7d9C3be5C8B049b9f", V3_02_ROUTERS)

    @patch("execution.chain_executor.Web3")
    def test_build_transaction_per_router_detection_on_base(self, mock_web3_cls) -> None:
        """On Base: Sushi uses V3 (original), Uniswap uses V3_02 (no deadline)."""
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")
        mock_w3.eth.get_transaction_count.return_value = 0
        mock_w3.eth.gas_price = 1_000_000
        mock_w3.to_wei = lambda v, u: v * 1_000_000_000

        mock_contract = MagicMock()
        mock_contract.functions.executeArbitrage.return_value.build_transaction.return_value = {
            "data": "0x", "from": "0xfake", "to": "0xcontract"
        }
        mock_w3.eth.contract.return_value = mock_contract

        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.001, estimated_gas_cost_base=0.0001,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=10.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Uniswap-Base", base_price=0, fee_bps=5.0,
                          volatility_bps=0, chain="base", dex_type="uniswap_v3"),
                DexConfig(name="Sushi-Base", base_price=0, fee_bps=30.0,
                          volatility_bps=0, chain="base", dex_type="sushi_v3"),
            ],
        )
        config.validate()

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT_BASE": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(config, chain="base")

        opp = Opportunity(
            pair="WETH/USDC", buy_dex="Sushi-Base", sell_dex="Uniswap-Base",
            trade_size=1.0, cost_to_buy_quote=2300.0,
            proceeds_from_sell_quote=2310.0, gross_profit_quote=10.0,
            net_profit_quote=8.0, net_profit_base=0.003,
            chain="base",
        )
        executor._build_transaction(opp)

        params = mock_contract.functions.executeArbitrage.call_args[0][0]
        # Sushi-Base router uses original SwapRouter → SWAP_TYPE_V3
        self.assertEqual(params[8], SWAP_TYPE_V3)      # swapTypeA (Sushi = original V3)
        # Uniswap-Base router uses SwapRouter02 → SWAP_TYPE_V3_02
        self.assertEqual(params[9], SWAP_TYPE_V3_02)   # swapTypeB (Uniswap = V3_02)

    @patch("execution.chain_executor.Web3")
    def test_build_transaction_uses_v3_on_arbitrum(self, mock_web3_cls) -> None:
        """On Arbitrum, V3 DEXes should use SWAP_TYPE_V3 (=0), not V3_02."""
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")
        mock_w3.eth.get_transaction_count.return_value = 0
        mock_w3.eth.gas_price = 1_000_000
        mock_w3.to_wei = lambda v, u: v * 1_000_000_000

        mock_contract = MagicMock()
        mock_contract.functions.executeArbitrage.return_value.build_transaction.return_value = {
            "data": "0x", "from": "0xfake", "to": "0xcontract"
        }
        mock_w3.eth.contract.return_value = mock_contract

        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.001, estimated_gas_cost_base=0.0002,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=10.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Uniswap-Arbitrum", base_price=0, fee_bps=5.0,
                          volatility_bps=0, chain="arbitrum", dex_type="uniswap_v3"),
                DexConfig(name="Sushi-Arbitrum", base_price=0, fee_bps=30.0,
                          volatility_bps=0, chain="arbitrum", dex_type="sushi_v3"),
            ],
        )
        config.validate()

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(config, chain="arbitrum")

        opp = Opportunity(
            pair="WETH/USDC", buy_dex="Uniswap-Arbitrum", sell_dex="Sushi-Arbitrum",
            trade_size=1.0, cost_to_buy_quote=2300.0,
            proceeds_from_sell_quote=2310.0, gross_profit_quote=10.0,
            net_profit_quote=8.0, net_profit_base=0.003,
            chain="arbitrum",
        )
        executor._build_transaction(opp)

        params = mock_contract.functions.executeArbitrage.call_args[0][0]
        self.assertEqual(params[8], SWAP_TYPE_V3)   # swapTypeA = 0
        self.assertEqual(params[9], SWAP_TYPE_V3)   # swapTypeB = 0

    @patch("execution.chain_executor.Web3")
    def test_base_aerodrome_still_uses_velo_type(self, mock_web3_cls) -> None:
        """Aerodrome on Base should still use SWAP_TYPE_VELO, not V3_02."""
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_w3.eth.account.from_key.return_value = MagicMock(address="0xfake")
        mock_w3.eth.get_transaction_count.return_value = 0
        mock_w3.eth.gas_price = 1_000_000
        mock_w3.to_wei = lambda v, u: v * 1_000_000_000

        mock_contract = MagicMock()
        mock_contract.functions.executeArbitrage.return_value.build_transaction.return_value = {
            "data": "0x", "from": "0xfake", "to": "0xcontract"
        }
        mock_w3.eth.contract.return_value = mock_contract

        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.001, estimated_gas_cost_base=0.0001,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=10.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Uniswap-Base", base_price=0, fee_bps=5.0,
                          volatility_bps=0, chain="base", dex_type="uniswap_v3"),
                DexConfig(name="Aerodrome-Base", base_price=0, fee_bps=20.0,
                          volatility_bps=0, chain="base", dex_type="aerodrome"),
            ],
        )
        config.validate()

        with patch.dict("os.environ", {
            "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
            "EXECUTOR_CONTRACT_BASE": "0x" + "cd" * 20,
        }):
            executor = ChainExecutor(config, chain="base")

        opp = Opportunity(
            pair="WETH/USDC", buy_dex="Uniswap-Base", sell_dex="Aerodrome-Base",
            trade_size=1.0, cost_to_buy_quote=2300.0,
            proceeds_from_sell_quote=2310.0, gross_profit_quote=10.0,
            net_profit_quote=8.0, net_profit_base=0.003,
            chain="base",
        )
        executor._build_transaction(opp)

        params = mock_contract.functions.executeArbitrage.call_args[0][0]
        self.assertEqual(params[8], SWAP_TYPE_V3_02)  # buy on Uni-Base = V3_02
        self.assertEqual(params[9], SWAP_TYPE_VELO)   # sell on Aerodrome = Velo


class BaseChainRouterTests(unittest.TestCase):
    """Sushi V3 router must be present for Base chain."""

    def test_base_has_sushi_v3_router(self) -> None:
        routers = SWAP_ROUTERS.get("base", {})
        self.assertIn("sushi_v3", routers)
        self.assertTrue(routers["sushi_v3"].startswith("0x"))
        self.assertEqual(len(routers["sushi_v3"]), 42)

    def test_all_chains_have_expected_dex_types(self) -> None:
        """Each chain with sushi_v3 quoter should also have sushi_v3 router."""
        chains_with_sushi_quoter = {"ethereum", "arbitrum", "base", "optimism"}
        for chain in chains_with_sushi_quoter:
            routers = SWAP_ROUTERS.get(chain, {})
            if chain != "base":  # Base was just added
                self.assertIn("sushi_v3", routers, f"Missing sushi_v3 router for {chain}")


class MultiChainDispatchTests(unittest.TestCase):
    """Tests for MultiChainSimulator / MultiChainSubmitter dispatch."""

    def test_simulator_dispatches_to_correct_chain(self) -> None:
        from run_event_driven import MultiChainSimulator, ChainExecutorSimulator

        mock_arb_sim = MagicMock(spec=ChainExecutorSimulator)
        mock_arb_sim.simulate.return_value = (True, "ok")
        mock_base_sim = MagicMock(spec=ChainExecutorSimulator)
        mock_base_sim.simulate.return_value = (False, "revert")

        multi = MultiChainSimulator({"arbitrum": mock_arb_sim, "base": mock_base_sim})

        arb_opp = Opportunity(
            pair="WETH/USDC", buy_dex="Uni-Arb", sell_dex="Sushi-Arb",
            trade_size=1.0, cost_to_buy_quote=2200.0,
            proceeds_from_sell_quote=2210.0, gross_profit_quote=10.0,
            net_profit_quote=8.0, net_profit_base=0.004,
            chain="arbitrum",
        )
        ok, reason = multi.simulate(arb_opp)
        self.assertTrue(ok)
        mock_arb_sim.simulate.assert_called_once_with(arb_opp)
        mock_base_sim.simulate.assert_not_called()

    def test_simulator_returns_error_for_unknown_chain(self) -> None:
        from run_event_driven import MultiChainSimulator

        multi = MultiChainSimulator({"arbitrum": MagicMock()})

        opp = Opportunity(
            pair="WETH/USDC", buy_dex="Uni", sell_dex="Sushi",
            trade_size=1.0, cost_to_buy_quote=2200.0,
            proceeds_from_sell_quote=2210.0, gross_profit_quote=10.0,
            net_profit_quote=8.0, net_profit_base=0.004,
            chain="polygon",
        )
        ok, reason = multi.simulate(opp)
        self.assertFalse(ok)
        self.assertIn("no_executor_for_chain", reason)

    def test_submitter_dispatches_to_correct_chain(self) -> None:
        from run_event_driven import MultiChainSubmitter, ChainExecutorSubmitter

        mock_base_sub = MagicMock(spec=ChainExecutorSubmitter)
        mock_base_sub.submit.return_value = ("0xhash", "", 0, "public")

        multi = MultiChainSubmitter({"base": mock_base_sub})

        opp = Opportunity(
            pair="WETH/USDC", buy_dex="Sushi-Base", sell_dex="Uni-Base",
            trade_size=1.0, cost_to_buy_quote=2200.0,
            proceeds_from_sell_quote=2210.0, gross_profit_quote=10.0,
            net_profit_quote=8.0, net_profit_base=0.004,
            chain="base",
        )
        result = multi.submit(opp)
        self.assertEqual(result[0], "0xhash")
        mock_base_sub.submit.assert_called_once()

    def test_submitter_raises_for_unknown_chain(self) -> None:
        from run_event_driven import MultiChainSubmitter

        multi = MultiChainSubmitter({"arbitrum": MagicMock()})

        opp = Opportunity(
            pair="WETH/USDC", buy_dex="Uni", sell_dex="Sushi",
            trade_size=1.0, cost_to_buy_quote=2200.0,
            proceeds_from_sell_quote=2210.0, gross_profit_quote=10.0,
            net_profit_quote=8.0, net_profit_base=0.004,
            chain="optimism",
        )
        with self.assertRaises(RuntimeError):
            multi.submit(opp)

    def test_build_execution_stack_creates_per_chain(self) -> None:
        """build_execution_stack should create executors for chains with contracts."""
        from run_event_driven import build_execution_stack, MultiChainSimulator

        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.001, estimated_gas_cost_base=0.002,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=15.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Uni-Arb", base_price=0, fee_bps=5.0,
                          volatility_bps=0, chain="arbitrum", dex_type="uniswap_v3"),
                DexConfig(name="Sushi-Arb", base_price=0, fee_bps=30.0,
                          volatility_bps=0, chain="arbitrum", dex_type="sushi_v3"),
                DexConfig(name="Uni-Base", base_price=0, fee_bps=5.0,
                          volatility_bps=0, chain="base", dex_type="uniswap_v3"),
                DexConfig(name="Uni-Eth", base_price=0, fee_bps=5.0,
                          volatility_bps=0, chain="ethereum", dex_type="uniswap_v3"),
            ],
        )
        config.validate()

        with patch("execution.chain_executor.ChainExecutor") as MockExecutor:
            # Arbitrum and Base succeed, Ethereum fails (no contract)
            def side_effect(cfg, chain=None):
                if chain == "ethereum":
                    raise ChainExecutorError("no contract")
                mock_exec = MagicMock()
                mock_exec.chain = chain
                return mock_exec

            MockExecutor.side_effect = side_effect
            MockExecutor.__name__ = "ChainExecutor"

            with patch.dict("os.environ", {
                "EXECUTOR_PRIVATE_KEY": "0x" + "ab" * 32,
                "EXECUTOR_CONTRACT": "0x" + "cd" * 20,
            }):
                sim, sub, ver = build_execution_stack(config)

            self.assertIsNotNone(sim)
            self.assertIsInstance(sim, MultiChainSimulator)
            # Should have arbitrum and base, not ethereum
            self.assertIn("arbitrum", sim._by_chain)
            self.assertIn("base", sim._by_chain)
            self.assertNotIn("ethereum", sim._by_chain)

    def test_build_execution_stack_returns_none_without_key(self) -> None:
        from run_event_driven import build_execution_stack

        with patch.dict("os.environ", {"EXECUTOR_PRIVATE_KEY": ""}, clear=False):
            sim, sub, ver = build_execution_stack(_make_config())
            self.assertIsNone(sim)
            self.assertIsNone(sub)
            self.assertIsNone(ver)


if __name__ == "__main__":
    unittest.main()
