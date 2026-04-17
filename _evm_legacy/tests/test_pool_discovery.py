import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from registry.pool_discovery import (
    ZERO_ADDRESS,
    discover_solidly_pools,
    discover_uniswap_v3_pools,
    discover_and_persist_pools,
)


class DiscoverUniswapV3PoolsTests(unittest.TestCase):
    @patch("registry.pool_discovery.Web3")
    def test_returns_nonzero_addresses(self, mock_web3_cls) -> None:
        mock_web3_cls.to_checksum_address = lambda x: x

        mock_w3 = MagicMock()
        factory = MagicMock()

        call_results = {
            500: "0xPoolFor500",
            3000: "0xPoolFor3000",
            10000: ZERO_ADDRESS,  # no pool at this tier
        }

        def get_pool(_a, _b, fee):
            result = MagicMock()
            result.call.return_value = call_results.get(fee, ZERO_ADDRESS)
            return result

        factory.functions.getPool.side_effect = get_pool
        mock_w3.eth.contract.return_value = factory

        pools = discover_uniswap_v3_pools(mock_w3, "ethereum", "0xweth", "0xusdc")

        self.assertEqual(len(pools), 2)
        addresses = {p["address"] for p in pools}
        self.assertEqual(addresses, {"0xPoolFor500", "0xPoolFor3000"})

    def test_returns_empty_for_unknown_chain(self) -> None:
        pools = discover_uniswap_v3_pools(MagicMock(), "solana", "0xa", "0xb")
        self.assertEqual(pools, [])

    @patch("registry.pool_discovery.Web3")
    def test_handles_rpc_errors_gracefully(self, mock_web3_cls) -> None:
        mock_web3_cls.to_checksum_address = lambda x: x

        mock_w3 = MagicMock()
        factory = MagicMock()
        factory.functions.getPool.side_effect = Exception("RPC timeout")
        mock_w3.eth.contract.return_value = factory

        pools = discover_uniswap_v3_pools(mock_w3, "ethereum", "0xweth", "0xusdc")
        self.assertEqual(pools, [])


class DiscoverSolidlyPoolsTests(unittest.TestCase):
    @patch("registry.pool_discovery.Web3")
    def test_discovers_stable_and_volatile(self, mock_web3_cls) -> None:
        mock_web3_cls.to_checksum_address = lambda x: x

        mock_w3 = MagicMock()
        factory = MagicMock()

        def get_pool(_a, _b, stable):
            result = MagicMock()
            result.call.return_value = "0xStablePool" if stable else "0xVolatilePool"
            return result

        factory.functions.getPool.side_effect = get_pool
        mock_w3.eth.contract.return_value = factory

        pools = discover_solidly_pools(mock_w3, "optimism", "0xop", "0xusdc")

        self.assertEqual(len(pools), 2)
        addresses = {p["address"] for p in pools}
        self.assertEqual(addresses, {"0xStablePool", "0xVolatilePool"})

    @patch("registry.pool_discovery.Web3")
    def test_tries_bridged_usdc_fallback(self, mock_web3_cls) -> None:
        """When native USDC returns zero-address, should try USDC.e."""
        mock_web3_cls.to_checksum_address = lambda x: x

        mock_w3 = MagicMock()
        factory = MagicMock()

        native_usdc = "0x0b2c639c533813f4aa9d7837caf62653d097ff85"
        bridged_usdc = "0x7f5c764cbc14f9669b88837ca1490cca17c31607"

        def get_pool(a, b, stable):
            result = MagicMock()
            # Return zero for native USDC, valid for bridged.
            if b.lower() == native_usdc.lower() or a.lower() == native_usdc.lower():
                result.call.return_value = ZERO_ADDRESS
            else:
                result.call.return_value = "0xBridgedPool"
            return result

        factory.functions.getPool.side_effect = get_pool
        mock_w3.eth.contract.return_value = factory

        pools = discover_solidly_pools(mock_w3, "optimism", "0xweth", native_usdc)

        self.assertGreater(len(pools), 0)
        self.assertEqual(pools[0]["address"], "0xBridgedPool")


class DiscoverAndPersistTests(unittest.TestCase):
    @patch("registry.pool_discovery.Web3")
    def test_persists_discovered_pools(self, mock_web3_cls) -> None:
        mock_web3_cls.to_checksum_address = lambda x: x
        mock_web3_cls.HTTPProvider = MagicMock()

        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3

        factory = MagicMock()

        def get_pool(_a, _b, fee):
            result = MagicMock()
            result.call.return_value = f"0xPool{fee}"
            return result

        factory.functions.getPool.side_effect = get_pool
        mock_w3.eth.contract.return_value = factory

        from persistence.db import init_db, close_db
        from persistence.repository import Repository
        from core.config import PairConfig

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        conn = init_db(tmp.name)
        repo = Repository(conn)
        try:
            pairs = [
                PairConfig(
                    pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
                    trade_size=1.0, chain="ethereum",
                ),
            ]
            count = discover_and_persist_pools(
                repo=repo,
                chains=["ethereum"],
                pairs=pairs,
            )
            # 3 fee tiers × 1 pair = 3 pools (if all return valid addresses)
            self.assertEqual(count, 3)

            # Calling again should insert 0 (idempotent).
            count2 = discover_and_persist_pools(
                repo=repo,
                chains=["ethereum"],
                pairs=pairs,
            )
            self.assertEqual(count2, 0)
        finally:
            close_db()
            Path(tmp.name).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
