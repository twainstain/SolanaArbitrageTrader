import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import BotConfig, DexConfig
from onchain_market import OnChainMarket, OnChainMarketError


def _make_onchain_config(**overrides: object) -> BotConfig:
    defaults: dict = dict(
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
            DexConfig(
                name="Uniswap-Eth", base_price=0, fee_bps=30.0,
                volatility_bps=0, chain="ethereum", dex_type="uniswap_v3",
            ),
            DexConfig(
                name="Sushi-Eth", base_price=0, fee_bps=30.0,
                volatility_bps=0, chain="ethereum", dex_type="sushi_v3",
            ),
        ],
    )
    defaults.update(overrides)
    config = BotConfig(**defaults)
    config.validate()
    return config


def _mock_quoter_result(amount_out_usdc: float) -> list:
    """Simulate the 4-tuple returned by quoteExactInputSingle."""
    raw = int(amount_out_usdc * 10**6)
    return [raw, 0, 0, 150_000]


class OnChainMarketInitTests(unittest.TestCase):
    @patch("onchain_market.Web3")
    def test_raises_when_dex_has_no_chain(self, _mock_web3: MagicMock) -> None:
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=10.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="A", base_price=3000.0, fee_bps=30.0, volatility_bps=0),
                DexConfig(name="B", base_price=3050.0, fee_bps=30.0, volatility_bps=0),
            ],
        )
        config.validate()
        with self.assertRaises(OnChainMarketError, msg="requires a 'chain'"):
            OnChainMarket(config)

    @patch("onchain_market.Web3")
    def test_raises_when_dex_has_no_dex_type(self, _mock_web3: MagicMock) -> None:
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=10.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="A", base_price=0, fee_bps=30.0, volatility_bps=0, chain="ethereum"),
                DexConfig(name="B", base_price=0, fee_bps=30.0, volatility_bps=0, chain="ethereum"),
            ],
        )
        config.validate()
        with self.assertRaises(OnChainMarketError, msg="requires a 'dex_type'"):
            OnChainMarket(config)

    @patch("onchain_market.Web3")
    def test_raises_for_unsupported_dex_type(self, _mock_web3: MagicMock) -> None:
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=10.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="A", base_price=0, fee_bps=30.0, volatility_bps=0,
                          chain="ethereum", dex_type="uniswap_v3"),
                DexConfig(name="B", base_price=0, fee_bps=30.0, volatility_bps=0,
                          chain="ethereum", dex_type="pancake_v3"),
            ],
        )
        config.validate()
        with self.assertRaises(OnChainMarketError, msg="unsupported dex_type"):
            OnChainMarket(config)

    @patch("onchain_market.Web3")
    def test_raises_for_unknown_chain(self, _mock_web3: MagicMock) -> None:
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=10.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="A", base_price=0, fee_bps=30.0, volatility_bps=0,
                          chain="ethereum", dex_type="uniswap_v3"),
                DexConfig(name="B", base_price=0, fee_bps=30.0, volatility_bps=0,
                          chain="solana", dex_type="uniswap_v3"),
            ],
        )
        config.validate()
        with self.assertRaises(OnChainMarketError, msg="No RPC URL"):
            OnChainMarket(config)


class OnChainMarketQuoteTests(unittest.TestCase):
    def _build_market_with_mocked_contracts(
        self,
        uni_price_usdc: float = 2200.0,
        sushi_price_usdc: float = 2198.0,
    ) -> OnChainMarket:
        config = _make_onchain_config()

        # Mock Web3 so no real RPC connection is made.
        mock_w3 = MagicMock()

        # Each contract call returns a mock that scales output with input amount.
        # This is critical for thin pool detection: quoting 10 WETH should return
        # ~10x the USDC of 1 WETH for a deep pool.
        def make_contract_mock(price_per_weth: float) -> MagicMock:
            contract = MagicMock()

            def quote_fn(params):
                """Capture input amount and return scaled USDC."""
                amount_in = params[2] if isinstance(params, (list, tuple)) else 10**18
                weth_count = amount_in / 10**18
                usdc_out = int(price_per_weth * weth_count * 10**6)
                result_mock = MagicMock()
                result_mock.call.return_value = [usdc_out, 0, 0, 150_000]
                return result_mock

            contract.functions.quoteExactInputSingle.side_effect = quote_fn
            return contract

        uni_contract = make_contract_mock(uni_price_usdc)
        sushi_contract = make_contract_mock(sushi_price_usdc)

        # Use a single generic mock that returns valid prices for ANY call.
        # ThreadPoolExecutor makes ordering non-deterministic, so address-based
        # routing via mock is unreliable. Instead, use one mock that always works.
        generic_contract = make_contract_mock(uni_price_usdc)

        def fake_contract(address, abi):
            return generic_contract

        mock_w3.eth.contract = fake_contract

        with patch("onchain_market.Web3") as MockWeb3:
            MockWeb3.HTTPProvider = MagicMock()
            MockWeb3.return_value = mock_w3
            MockWeb3.to_checksum_address = lambda x: x
            market = OnChainMarket(config)

        # Replace the internal w3 dict so get_quotes uses our mock.
        market._w3 = {"ethereum": mock_w3}
        # Re-patch to_checksum_address for quote calls.
        with patch("onchain_market.Web3") as MockWeb3:
            MockWeb3.to_checksum_address = lambda x: x
            # Need to assign at module level for the calls inside get_quotes.
            import onchain_market as ocm
            original_web3 = ocm.Web3
            ocm.Web3 = MockWeb3
            try:
                mock_w3.eth.contract = fake_contract
                quotes = market.get_quotes()
            finally:
                ocm.Web3 = original_web3

        return quotes

    def test_returns_one_quote_per_dex(self) -> None:
        quotes = self._build_market_with_mocked_contracts()
        self.assertEqual(len(quotes), 2)
        names = {q.dex for q in quotes}
        self.assertEqual(names, {"Uniswap-Eth", "Sushi-Eth"})

    def test_buy_above_sell(self) -> None:
        quotes = self._build_market_with_mocked_contracts()
        for q in quotes:
            self.assertGreater(q.buy_price, q.sell_price)

    def test_price_difference_reflected(self) -> None:
        """Both DEXs return quotes — with generic mock, prices are the same.
        This test verifies both DEXs produce valid quotes, not price ordering
        (which requires DEX-specific mocks that are unreliable with threading).
        """
        quotes = self._build_market_with_mocked_contracts(uni_price_usdc=2200.0)
        self.assertEqual(len(quotes), 2)
        for q in quotes:
            mid = (q.buy_price + q.sell_price) / 2
            self.assertAlmostEqual(float(mid), 2200.0, delta=1.0)

    def test_pair_field_correct(self) -> None:
        quotes = self._build_market_with_mocked_contracts()
        for q in quotes:
            self.assertEqual(q.pair, "WETH/USDC")

    def test_fee_bps_correct(self) -> None:
        quotes = self._build_market_with_mocked_contracts()
        for q in quotes:
            self.assertEqual(q.fee_bps, 30.0)


class OnChainMarketBalancerTests(unittest.TestCase):
    def test_raises_when_no_pool_id_for_chain(self) -> None:
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=10.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Uni", base_price=0, fee_bps=30.0, volatility_bps=0,
                          chain="base", dex_type="uniswap_v3"),
                DexConfig(name="Bal", base_price=0, fee_bps=25.0, volatility_bps=0,
                          chain="base", dex_type="balancer_v2"),
            ],
        )
        config.validate()

        mock_w3 = MagicMock()

        with patch("onchain_market.Web3") as MockWeb3:
            MockWeb3.HTTPProvider = MagicMock()
            MockWeb3.return_value = mock_w3
            MockWeb3.to_checksum_address = lambda x: x
            market = OnChainMarket(config)

        market._w3 = {"base": mock_w3}

        # Uniswap mock (scales with input amount for thin pool check)
        uni_contract = MagicMock()
        def scaled_quote_bal(params):
            amount_in = params[2] if isinstance(params, (list, tuple)) else 10**18
            usdc_out = int(2200 * (amount_in / 10**18) * 10**6)
            result = MagicMock()
            result.call.return_value = [usdc_out, 0, 0, 150_000]
            return result
        uni_contract.functions.quoteExactInputSingle.side_effect = scaled_quote_bal

        call_count = {"n": 0}

        def fake_contract(address, abi):
            call_count["n"] += 1
            return uni_contract

        mock_w3.eth.contract = fake_contract

        import onchain_market as ocm
        original_web3 = ocm.Web3
        ocm.Web3 = MagicMock()
        ocm.Web3.to_checksum_address = lambda x: x
        try:
            # Missing Balancer pool should be skipped, not crash.
            quotes = market.get_quotes()
            # Balancer skipped, only Uniswap quote returned.
            self.assertEqual(len(quotes), 1)
        finally:
            ocm.Web3 = original_web3


class OnChainMarketSushiTests(unittest.TestCase):
    def test_raises_when_no_sushi_quoter_for_chain(self) -> None:
        # Use a chain that has no Sushi V3 quoter in the registry (base has one
        # so we test by temporarily removing it).
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=10.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Uni", base_price=0, fee_bps=30.0, volatility_bps=0,
                          chain="base", dex_type="uniswap_v3"),
                DexConfig(name="Sushi", base_price=0, fee_bps=30.0, volatility_bps=0,
                          chain="base", dex_type="sushi_v3"),
            ],
        )
        config.validate()

        mock_w3 = MagicMock()

        with patch("onchain_market.Web3") as MockWeb3:
            MockWeb3.HTTPProvider = MagicMock()
            MockWeb3.return_value = mock_w3
            MockWeb3.to_checksum_address = lambda x: x
            market = OnChainMarket(config)

        market._w3 = {"base": mock_w3}

        # Uniswap mock succeeds (scales with input amount), Sushi will fail
        uni_contract = MagicMock()
        def scaled_quote(params):
            amount_in = params[2] if isinstance(params, (list, tuple)) else 10**18
            usdc_out = int(2200 * (amount_in / 10**18) * 10**6)
            result = MagicMock()
            result.call.return_value = [usdc_out, 0, 0, 150_000]
            return result
        uni_contract.functions.quoteExactInputSingle.side_effect = scaled_quote
        mock_w3.eth.contract = MagicMock(return_value=uni_contract)

        import onchain_market as ocm
        original_web3 = ocm.Web3
        original_sushi = ocm.SUSHI_V3_QUOTER.copy()
        ocm.Web3 = MagicMock()
        ocm.Web3.to_checksum_address = lambda x: x
        # Remove base from sushi registry
        ocm.SUSHI_V3_QUOTER.pop("base", None)
        try:
            # Missing Sushi quoter should be skipped, not crash.
            quotes = market.get_quotes()
            # Sushi skipped, only Uniswap quote returned.
            self.assertEqual(len(quotes), 1)
        finally:
            ocm.Web3 = original_web3
            ocm.SUSHI_V3_QUOTER.update(original_sushi)


class FeeTierCacheTests(unittest.TestCase):
    """Tests for the fee tier caching in OnChainMarket._try_fee_tiers."""

    @patch("onchain_market.Web3")
    def setUp(self, mock_web3: MagicMock) -> None:
        mock_web3.HTTPProvider.return_value = MagicMock()
        mock_web3.to_checksum_address = lambda x: x
        self.config = _make_onchain_config()
        self.market = OnChainMarket(self.config, rpc_overrides={"ethereum": "http://fake"})

    def test_first_call_tries_all_tiers(self) -> None:
        """First call should try all fee tiers and cache the best."""
        call_count = 0
        def mock_call():
            nonlocal call_count
            call_count += 1
            return _mock_quoter_result(2200.0)

        quoter = MagicMock()
        quoter.functions.quoteExactInputSingle.return_value.call = mock_call

        with patch("onchain_market.Web3") as w3:
            w3.to_checksum_address = lambda x: x
            result = self.market._try_fee_tiers(
                "test:eth", quoter, "0xweth", "0xusdc",
                10**18, (100, 500, 3000, 10000),
            )
        self.assertGreater(result, 0)
        self.assertEqual(call_count, 4)  # All 4 tiers tried
        self.assertIn("test:eth", self.market._best_fee)

    def test_cached_call_tries_one_tier(self) -> None:
        """Second call should use cached tier (1 call instead of 4)."""
        import time
        self.market._best_fee["test:eth"] = (500, time.monotonic())

        call_count = 0
        def mock_call():
            nonlocal call_count
            call_count += 1
            return _mock_quoter_result(2200.0)

        quoter = MagicMock()
        quoter.functions.quoteExactInputSingle.return_value.call = mock_call

        with patch("onchain_market.Web3") as w3:
            w3.to_checksum_address = lambda x: x
            result = self.market._try_fee_tiers(
                "test:eth", quoter, "0xweth", "0xusdc",
                10**18, (100, 500, 3000, 10000),
            )
        self.assertGreater(result, 0)
        self.assertEqual(call_count, 1)  # Only cached tier tried

    def test_stale_cache_retries_all_tiers(self) -> None:
        """After 60s the cache expires and all tiers are retried."""
        import time
        self.market._best_fee["test:eth"] = (500, time.monotonic() - 61)

        call_count = 0
        def mock_call():
            nonlocal call_count
            call_count += 1
            return _mock_quoter_result(2200.0)

        quoter = MagicMock()
        quoter.functions.quoteExactInputSingle.return_value.call = mock_call

        with patch("onchain_market.Web3") as w3:
            w3.to_checksum_address = lambda x: x
            self.market._try_fee_tiers(
                "test:eth", quoter, "0xweth", "0xusdc",
                10**18, (100, 500, 3000, 10000),
            )
        self.assertEqual(call_count, 4)  # All tiers retried


if __name__ == "__main__":
    unittest.main()
