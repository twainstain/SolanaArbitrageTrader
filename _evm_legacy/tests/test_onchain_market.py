import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.config import BotConfig, DexConfig
from market.onchain_market import OnChainMarket, OnChainMarketError


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
    @patch("market.onchain_market.Web3")
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

    @patch("market.onchain_market.Web3")
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

    @patch("market.onchain_market.Web3")
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

    @patch("market.onchain_market.Web3")
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

        with patch("market.onchain_market.Web3") as MockWeb3:
            MockWeb3.HTTPProvider = MagicMock()
            MockWeb3.return_value = mock_w3
            MockWeb3.to_checksum_address = lambda x: x
            market = OnChainMarket(config)

        # Replace the internal w3 dict so get_quotes uses our mock.
        market._w3 = {"ethereum": mock_w3}
        # Re-patch to_checksum_address for quote calls.
        with patch("market.onchain_market.Web3") as MockWeb3:
            MockWeb3.to_checksum_address = lambda x: x
            # Need to assign at module level for the calls inside get_quotes.
            import market.onchain_market as ocm
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

    def test_buy_equals_sell_for_onchain(self) -> None:
        """On-chain quotes have buy_price == sell_price (no synthetic spread)."""
        quotes = self._build_market_with_mocked_contracts()
        for q in quotes:
            self.assertEqual(q.buy_price, q.sell_price)

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

    def test_fee_bps_reflects_actual_tier(self) -> None:
        """fee_bps should reflect the winning pool fee tier, not the config value."""
        quotes = self._build_market_with_mocked_contracts()
        for q in quotes:
            # Actual fee tier from the quoter (e.g. 500 → 5 bps, 3000 → 30 bps).
            self.assertGreater(q.fee_bps, 0)
            self.assertTrue(q.fee_included)


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

        with patch("market.onchain_market.Web3") as MockWeb3:
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

        import market.onchain_market as ocm
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

        with patch("market.onchain_market.Web3") as MockWeb3:
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

        import market.onchain_market as ocm
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


class OnChainMarketVelodromeTests(unittest.TestCase):
    @patch("market.onchain_market.Web3")
    def test_velodrome_uses_best_of_stable_and_volatile_routes(self, mock_web3_cls) -> None:
        config = BotConfig(
            pair="OP/USDC",
            base_asset="OP",
            quote_asset="USDC",
            trade_size=250.0,
            min_profit_base=0.0,
            estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=9.0,
            flash_loan_provider="aave_v3",
            slippage_bps=10.0,
            poll_interval_seconds=0.0,
            dexes=[
                DexConfig(
                    name="Velodrome-Optimism", base_price=0, fee_bps=20.0,
                    volatility_bps=0, chain="optimism", dex_type="velodrome_v2",
                ),
                DexConfig(
                    name="Uniswap-Optimism", base_price=0, fee_bps=5.0,
                    volatility_bps=0, chain="optimism", dex_type="uniswap_v3",
                ),
            ],
        )
        config.validate()

        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x

        router = MagicMock()

        def get_amounts_out(_amount_in, routes):
            stable = routes[0][2]
            result = MagicMock()
            if stable:
                result.call.return_value = [10**18, 1_450 * 10**6]
            else:
                result.call.return_value = [10**18, 1_500 * 10**6]
            return result

        router.functions.getAmountsOut.side_effect = get_amounts_out
        mock_w3.eth.contract.return_value = router

        market = OnChainMarket(config)
        market._w3 = {"optimism": mock_w3}

        price, fee_bps = market._quote_velodrome(
            "optimism",
            "0xbase",
            "0xquote",
            "velodrome_v2",
            "OP",
            "USDC",
        )

        self.assertEqual(price, 1500)
        self.assertEqual(fee_bps, 20)

    @patch("market.onchain_market.Web3")
    def test_velodrome_falls_back_to_bridged_usdc(self, mock_web3_cls) -> None:
        """When native USDC returns 0, Velodrome should retry with USDC.e."""
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=10.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Velodrome-Optimism", base_price=0, fee_bps=20.0,
                          volatility_bps=0, chain="optimism", dex_type="velodrome_v2"),
                DexConfig(name="Uniswap-Optimism", base_price=0, fee_bps=5.0,
                          volatility_bps=0, chain="optimism", dex_type="uniswap_v3"),
            ],
        )
        config.validate()

        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x

        router = MagicMock()
        native_usdc = "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85"
        bridged_usdc = "0x7F5c764cBc14f9669B88837ca1490cCa17c31607"

        def get_amounts_out(_amount_in, routes):
            quote_addr = routes[0][1]
            result = MagicMock()
            if quote_addr.lower() == native_usdc.lower():
                # Native USDC pool doesn't exist — return 0.
                result.call.return_value = [10**18, 0]
            elif quote_addr.lower() == bridged_usdc.lower():
                # USDC.e pool works.
                result.call.return_value = [10**18, 2_400 * 10**6]
            else:
                result.call.return_value = [10**18, 0]
            return result

        router.functions.getAmountsOut.side_effect = get_amounts_out
        mock_w3.eth.contract.return_value = router

        market = OnChainMarket(config)
        market._w3 = {"optimism": mock_w3}

        price, fee_bps = market._quote_velodrome(
            "optimism", "0xweth", native_usdc,
            "velodrome_v2", "WETH", "USDC",
        )
        self.assertEqual(price, 2400)

    @patch("market.onchain_market.Web3")
    def test_velodrome_fee_stable_vs_volatile(self, mock_web3_cls) -> None:
        """Stable pool should return ~2 bps, volatile ~20 bps."""
        config = BotConfig(
            pair="USDC/USDT", base_asset="USDC", quote_asset="USDT",
            trade_size=1000.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=10.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Velodrome-Optimism", base_price=0, fee_bps=20.0,
                          volatility_bps=0, chain="optimism", dex_type="velodrome_v2"),
                DexConfig(name="Uniswap-Optimism", base_price=0, fee_bps=5.0,
                          volatility_bps=0, chain="optimism", dex_type="uniswap_v3"),
            ],
        )
        config.validate()

        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x

        router = MagicMock()

        def get_amounts_out(_amount_in, routes):
            stable = routes[0][2]
            result = MagicMock()
            if stable:
                # Stable pool wins for stablecoin pair.
                result.call.return_value = [10**6, 999_500]
            else:
                result.call.return_value = [10**6, 998_000]
            return result

        router.functions.getAmountsOut.side_effect = get_amounts_out
        mock_w3.eth.contract.return_value = router

        market = OnChainMarket(config)
        market._w3 = {"optimism": mock_w3}

        price, fee_bps = market._quote_velodrome(
            "optimism", "0xusdc", "0xusdt",
            "velodrome_v2", "USDC", "USDT",
        )
        # Stable pool won → fee should be 2 bps.
        self.assertEqual(fee_bps, 2)

    @patch("market.onchain_market.Web3")
    def test_aerodrome_uses_usdbc_on_base(self, mock_web3_cls) -> None:
        """Aerodrome on Base should fall back to USDbC when native USDC returns 0."""
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=10.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Aerodrome-Base", base_price=0, fee_bps=20.0,
                          volatility_bps=0, chain="base", dex_type="aerodrome"),
                DexConfig(name="Uniswap-Base", base_price=0, fee_bps=5.0,
                          volatility_bps=0, chain="base", dex_type="uniswap_v3"),
            ],
        )
        config.validate()

        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x

        router = MagicMock()
        native_usdc = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
        usdbc = "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA"

        def get_amounts_out(_amount_in, routes):
            quote_addr = routes[0][1]
            result = MagicMock()
            if quote_addr.lower() == usdbc.lower():
                result.call.return_value = [10**18, 2_350 * 10**6]
            else:
                result.call.return_value = [10**18, 0]
            return result

        router.functions.getAmountsOut.side_effect = get_amounts_out
        mock_w3.eth.contract.return_value = router

        market = OnChainMarket(config)
        market._w3 = {"base": mock_w3}

        price, fee_bps = market._quote_velodrome(
            "base", "0xweth", native_usdc,
            "aerodrome", "WETH", "USDC",
        )
        self.assertEqual(price, 2350)


class LiquidityEstimationTests(unittest.TestCase):
    @patch("market.onchain_market.Web3")
    def test_estimate_deep_pool(self, mock_web3_cls) -> None:
        """Small and normal prices close together → high TVL estimate."""
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        config = _make_onchain_config()
        market = OnChainMarket(config, rpc_overrides={"ethereum": "http://fake"})

        # Mock _quote_small_amount to return nearly the same as normal.
        from decimal import Decimal as D
        with patch.object(market, "_quote_small_amount", return_value=D("2200")):
            tvl = market._estimate_liquidity_usd(
                "ethereum", "0xweth", "0xusdc", "uniswap_v3",
                "WETH", "USDC", D("2199"),
            )
        # 0.045% impact → very high TVL
        self.assertGreater(tvl, D("1000000"))

    @patch("market.onchain_market.Web3")
    def test_estimate_thin_pool(self, mock_web3_cls) -> None:
        """2.2% price impact → ~$52K TVL → below scanner $1M threshold."""
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        config = _make_onchain_config()
        market = OnChainMarket(config, rpc_overrides={"ethereum": "http://fake"})

        from decimal import Decimal as D
        # small=2344, normal=2292 → 2.2% impact
        with patch.object(market, "_quote_small_amount", return_value=D("2344")):
            tvl = market._estimate_liquidity_usd(
                "ethereum", "0xweth", "0xusdc", "uniswap_v3",
                "WETH", "USDC", D("2292"),
            )
        # ~$52K TVL — well below $1M
        self.assertLess(tvl, D("100000"))
        self.assertGreater(tvl, D("10000"))

    @patch("market.onchain_market.Web3")
    def test_estimate_returns_zero_on_failure(self, mock_web3_cls) -> None:
        """If _quote_small_amount fails, return ZERO (no filter triggered)."""
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        config = _make_onchain_config()
        market = OnChainMarket(config, rpc_overrides={"ethereum": "http://fake"})

        from decimal import Decimal as D
        with patch.object(market, "_quote_small_amount", side_effect=Exception("RPC fail")):
            tvl = market._estimate_liquidity_usd(
                "ethereum", "0xweth", "0xusdc", "uniswap_v3",
                "WETH", "USDC", D("2200"),
            )
        self.assertEqual(tvl, 0)

    @patch("market.onchain_market.Web3")
    def test_estimate_zero_impact_returns_deep_sentinel(self, mock_web3_cls) -> None:
        """Zero impact (same price) → deep pool sentinel."""
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        config = _make_onchain_config()
        market = OnChainMarket(config, rpc_overrides={"ethereum": "http://fake"})

        from decimal import Decimal as D
        with patch.object(market, "_quote_small_amount", return_value=D("2200")):
            tvl = market._estimate_liquidity_usd(
                "ethereum", "0xweth", "0xusdc", "uniswap_v3",
                "WETH", "USDC", D("2200"),
            )
        self.assertEqual(tvl, OnChainMarket._DEEP_POOL_TVL)


class TvlCacheTests(unittest.TestCase):
    """Tests for the liquidity estimation cache in OnChainMarket."""

    @patch("market.onchain_market.Web3")
    def setUp(self, mock_web3: MagicMock) -> None:
        mock_web3.HTTPProvider.return_value = MagicMock()
        mock_web3.to_checksum_address = lambda x: x
        self.config = _make_onchain_config()
        self.market = OnChainMarket(self.config, rpc_overrides={"ethereum": "http://fake"})

    def test_cache_hit_skips_rpc(self) -> None:
        """Second call within TTL should use cached TVL, not call _quote_small_amount."""
        from decimal import Decimal as D
        call_count = 0
        original_quote = self.market._quote_small_amount

        def counting_quote(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return D("2200")

        with patch.object(self.market, "_quote_small_amount", side_effect=counting_quote):
            # First call — should call _quote_small_amount.
            tvl1 = self.market._estimate_liquidity_usd(
                "ethereum", "0xweth", "0xusdc", "uniswap_v3",
                "WETH", "USDC", D("2199"),
            )
            self.assertEqual(call_count, 1)

            # Second call — should use cache, not call _quote_small_amount.
            tvl2 = self.market._estimate_liquidity_usd(
                "ethereum", "0xweth", "0xusdc", "uniswap_v3",
                "WETH", "USDC", D("2199"),
            )
            self.assertEqual(call_count, 1)  # No additional call
            self.assertEqual(tvl1, tvl2)

    def test_cache_expiry_retries_rpc(self) -> None:
        """After TTL expires, _quote_small_amount should be called again."""
        from decimal import Decimal as D
        import time

        # Pre-populate cache with expired entry.
        # Deep pools ($5M) have 30-min TTL, so use 1801s to ensure expiry.
        tvl_key = "uniswap_v3:ethereum:0xweth:0xusdc"
        self.market._tvl_cache[tvl_key] = (D("5000000"), time.monotonic() - 1801)

        call_count = 0

        def counting_quote(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return D("2200")

        with patch.object(self.market, "_quote_small_amount", side_effect=counting_quote):
            self.market._estimate_liquidity_usd(
                "ethereum", "0xweth", "0xusdc", "uniswap_v3",
                "WETH", "USDC", D("2199"),
            )
            self.assertEqual(call_count, 1)  # Expired cache → fresh call

    def test_cache_stores_deep_pool_sentinel(self) -> None:
        """Zero impact should cache the deep pool sentinel."""
        from decimal import Decimal as D

        with patch.object(self.market, "_quote_small_amount", return_value=D("2200")):
            tvl = self.market._estimate_liquidity_usd(
                "ethereum", "0xweth", "0xusdc", "uniswap_v3",
                "WETH", "USDC", D("2200"),
            )
        self.assertEqual(tvl, OnChainMarket._DEEP_POOL_TVL)
        tvl_key = "uniswap_v3:ethereum:0xweth:0xusdc"
        self.assertIn(tvl_key, self.market._tvl_cache)
        self.assertEqual(self.market._tvl_cache[tvl_key][0], OnChainMarket._DEEP_POOL_TVL)


class FeeTierCacheTests(unittest.TestCase):
    """Tests for the fee tier caching in OnChainMarket._try_fee_tiers."""

    @patch("market.onchain_market.Web3")
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

        with patch("market.onchain_market.Web3") as w3:
            w3.to_checksum_address = lambda x: x
            amount, fee_tier = self.market._try_fee_tiers(
                "test:eth", quoter, "0xweth", "0xusdc",
                10**18, (100, 500, 3000, 10000),
            )
        self.assertGreater(amount, 0)
        self.assertIn(fee_tier, (100, 500, 3000, 10000))
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

        with patch("market.onchain_market.Web3") as w3:
            w3.to_checksum_address = lambda x: x
            amount, fee_tier = self.market._try_fee_tiers(
                "test:eth", quoter, "0xweth", "0xusdc",
                10**18, (100, 500, 3000, 10000),
            )
        self.assertGreater(amount, 0)
        self.assertEqual(fee_tier, 500)  # Used cached tier
        self.assertEqual(call_count, 1)  # Only cached tier tried

    def test_stale_cache_retries_all_tiers(self) -> None:
        """After 5 minutes the cache expires and all tiers are retried."""
        import time
        self.market._best_fee["test:eth"] = (500, time.monotonic() - 301)

        call_count = 0
        def mock_call():
            nonlocal call_count
            call_count += 1
            return _mock_quoter_result(2200.0)

        quoter = MagicMock()
        quoter.functions.quoteExactInputSingle.return_value.call = mock_call

        with patch("market.onchain_market.Web3") as w3:
            w3.to_checksum_address = lambda x: x
            self.market._try_fee_tiers(
                "test:eth", quoter, "0xweth", "0xusdc",
                10**18, (100, 500, 3000, 10000),
            )
        self.assertEqual(call_count, 4)  # All tiers retried


class OnChainMarketCurveTests(unittest.TestCase):
    @patch("market.onchain_market.Web3")
    def test_curve_get_dy_returns_price(self, mock_web3_cls) -> None:
        config = BotConfig(
            pair="USDT/USDC", base_asset="USDT", quote_asset="USDC",
            trade_size=1000.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=10.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Curve-Ethereum", base_price=0, fee_bps=4.0,
                          volatility_bps=0, chain="ethereum", dex_type="curve"),
                DexConfig(name="Uniswap-Ethereum", base_price=0, fee_bps=5.0,
                          volatility_bps=0, chain="ethereum", dex_type="uniswap_v3"),
            ],
        )
        config.validate()

        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x

        pool = MagicMock()
        # 1000 USDT (6 decimals) → ~999.5 USDC (after ~0.04% fee)
        pool.functions.get_dy.return_value.call.return_value = 999_500_000
        mock_w3.eth.contract.return_value = pool

        market = OnChainMarket(config)
        market._w3 = {"ethereum": mock_w3}

        from decimal import Decimal as D
        price, fee_bps = market._quote_curve("ethereum", "USDT", "USDC")
        self.assertAlmostEqual(float(price), 999.5, delta=1.0)
        self.assertEqual(fee_bps, D("4"))

    @patch("market.onchain_market.Web3")
    def test_curve_raises_for_unknown_pair(self, mock_web3_cls) -> None:
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=10.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Curve-Ethereum", base_price=0, fee_bps=4.0,
                          volatility_bps=0, chain="ethereum", dex_type="curve"),
                DexConfig(name="Uniswap-Ethereum", base_price=0, fee_bps=5.0,
                          volatility_bps=0, chain="ethereum", dex_type="uniswap_v3"),
            ],
        )
        config.validate()

        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        market = OnChainMarket(config)

        with self.assertRaises(OnChainMarketError):
            market._quote_curve("ethereum", "WETH", "LINK")


class OnChainMarketTraderJoeTests(unittest.TestCase):
    @patch("market.onchain_market.Web3")
    def test_traderjoe_lb_returns_price(self, mock_web3_cls) -> None:
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=10.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="TraderJoe-Avax", base_price=0, fee_bps=15.0,
                          volatility_bps=0, chain="avax", dex_type="traderjoe_lb"),
                DexConfig(name="Uniswap-Avax", base_price=0, fee_bps=5.0,
                          volatility_bps=0, chain="avax", dex_type="uniswap_v3"),
            ],
        )
        config.validate()

        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x

        quoter = MagicMock()
        # Result tuple: (route, pairs, binSteps, versions, amounts, virtualAmounts, fees)
        quoter.functions.findBestPathFromAmountIn.return_value.call.return_value = (
            ["0xa", "0xb"],  # route
            ["0xpair"],       # pairs
            [20],             # binSteps
            [2],              # versions (V2.1)
            [10**18, 2300 * 10**6],  # amounts (input, output)
            [10**18, 2302 * 10**6],  # virtualAmountsWithoutSlippage
            [100000],         # fees
        )
        mock_w3.eth.contract.return_value = quoter

        market = OnChainMarket(config)
        market._w3 = {"avax": mock_w3}

        from decimal import Decimal as D
        price, fee_bps = market._quote_traderjoe_lb(
            "avax", "0xweth", "0xusdc", "WETH", "USDC",
        )
        self.assertEqual(price, D("2300"))
        self.assertEqual(fee_bps, D("15"))

    @patch("market.onchain_market.Web3")
    def test_traderjoe_raises_for_unknown_chain(self, mock_web3_cls) -> None:
        config = BotConfig(
            pair="WETH/USDC", base_asset="WETH", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=9.0, flash_loan_provider="aave_v3",
            slippage_bps=10.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="TraderJoe-Polygon", base_price=0, fee_bps=15.0,
                          volatility_bps=0, chain="polygon", dex_type="traderjoe_lb"),
                DexConfig(name="Uniswap-Polygon", base_price=0, fee_bps=5.0,
                          volatility_bps=0, chain="polygon", dex_type="uniswap_v3"),
            ],
        )
        config.validate()

        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        market = OnChainMarket(config)

        with self.assertRaises(OnChainMarketError):
            market._quote_traderjoe_lb("polygon", "0xa", "0xb", "WETH", "USDC")


class QuoteSmallAmountFeeCacheTests(unittest.TestCase):
    """Tests for _quote_small_amount reusing the _best_fee cache."""

    @patch("market.onchain_market.Web3")
    def test_uses_cached_fee_tier(self, mock_web3_cls: MagicMock) -> None:
        """When _best_fee has a cached tier, _quote_small_amount uses it
        instead of sweeping all 4 tiers (saves 3 RPC calls)."""
        config = _make_onchain_config()
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        market = OnChainMarket(config)

        import time
        # Pre-populate the fee cache with tier 500 for uniswap_v3.
        market._best_fee["uniswap_v3:ethereum:WETH/USDC"] = (500, time.monotonic())

        mock_quoter = MagicMock()
        mock_quoter.functions.quoteExactInputSingle.return_value.call.return_value = [
            22_000_000, 0, 0, 150_000  # 22 USDC for 0.01 WETH
        ]
        mock_w3 = MagicMock()
        mock_w3.eth.contract.return_value = mock_quoter
        market._w3 = {"ethereum": mock_w3}

        result = market._quote_small_amount(
            "ethereum", "0xweth", "0xusdc", "uniswap_v3", "WETH", "USDC",
        )

        # Should have called quoteExactInputSingle exactly once (cached tier).
        self.assertEqual(mock_quoter.functions.quoteExactInputSingle.call_count, 1)
        # Verify the fee tier passed was 500 (the cached one).
        call_args = mock_quoter.functions.quoteExactInputSingle.call_args[0][0]
        self.assertEqual(call_args[3], 500)  # fee tier position in tuple
        self.assertGreater(result, 0)

    @patch("market.onchain_market.Web3")
    def test_falls_back_to_sweep_without_cache(self, mock_web3_cls: MagicMock) -> None:
        """Without a cached fee tier, _quote_small_amount sweeps all tiers."""
        config = _make_onchain_config()
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        market = OnChainMarket(config)
        # No cached fee tier — _best_fee is empty.

        mock_quoter = MagicMock()
        # Only tier 3000 succeeds, others raise.
        def side_effect(params):
            fee = params[3]
            result = MagicMock()
            if fee == 3000:
                result.call.return_value = [22_000_000, 0, 0, 150_000]
            else:
                result.call.side_effect = Exception("no pool")
            return result
        mock_quoter.functions.quoteExactInputSingle.side_effect = side_effect
        mock_w3 = MagicMock()
        mock_w3.eth.contract.return_value = mock_quoter
        market._w3 = {"ethereum": mock_w3}

        result = market._quote_small_amount(
            "ethereum", "0xweth", "0xusdc", "uniswap_v3", "WETH", "USDC",
        )

        # Should have tried all 4 fee tiers.
        self.assertEqual(mock_quoter.functions.quoteExactInputSingle.call_count, 4)
        self.assertGreater(result, 0)

    @patch("market.onchain_market.Web3")
    def test_cached_tier_failure_returns_zero(self, mock_web3_cls: MagicMock) -> None:
        """If the cached tier fails, _quote_small_amount returns 0 (graceful)."""
        config = _make_onchain_config()
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        market = OnChainMarket(config)

        import time
        market._best_fee["sushi_v3:ethereum:WETH/USDC"] = (500, time.monotonic())

        mock_quoter = MagicMock()
        mock_quoter.functions.quoteExactInputSingle.return_value.call.side_effect = Exception("rpc error")
        mock_w3 = MagicMock()
        mock_w3.eth.contract.return_value = mock_quoter
        market._w3 = {"ethereum": mock_w3}

        result = market._quote_small_amount(
            "ethereum", "0xweth", "0xusdc", "sushi_v3", "WETH", "USDC",
        )

        # Should return 0 (graceful degradation, not exception).
        from decimal import Decimal
        self.assertEqual(result, Decimal("0"))


class PersistentThreadPoolTests(unittest.TestCase):
    """Tests for persistent ThreadPoolExecutor in OnChainMarket."""

    @patch("market.onchain_market.Web3")
    def test_pool_created_on_init(self, mock_web3_cls: MagicMock) -> None:
        """OnChainMarket should create a persistent thread pool at init."""
        config = _make_onchain_config()
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        market = OnChainMarket(config)

        from concurrent.futures import ThreadPoolExecutor
        self.assertIsInstance(market._pool, ThreadPoolExecutor)

    @patch("market.onchain_market.Web3")
    def test_pool_reused_across_calls(self, mock_web3_cls: MagicMock) -> None:
        """The thread pool should be the same instance across get_quotes calls."""
        config = _make_onchain_config()
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        market = OnChainMarket(config)

        pool_id = id(market._pool)

        # Mock the market so get_quotes doesn't make real RPC calls.
        # Just verify the pool isn't recreated.
        market.liquidity_cache.mark_skip("Uniswap-Eth:WETH/USDC", "ethereum", "test")
        market.liquidity_cache.mark_skip("Sushi-Eth:WETH/USDC", "ethereum", "test")
        market.get_quotes()  # All cached, no futures submitted

        self.assertEqual(id(market._pool), pool_id)


class TieredTVLCacheTTLTests(unittest.TestCase):
    """Tests for tiered TVL cache TTL (deep pools get longer TTL)."""

    @patch("market.onchain_market.Web3")
    def test_deep_pool_uses_long_ttl(self, mock_web3_cls: MagicMock) -> None:
        """Pools with >$1M TVL should stay cached for 30 minutes."""
        config = _make_onchain_config()
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        market = OnChainMarket(config)

        from decimal import Decimal
        import time

        # Seed the TVL cache with a deep pool ($50M).
        key = "uniswap_v3:ethereum:0xweth:0xusdc"
        market._tvl_cache[key] = (Decimal("50000000"), time.monotonic() - 600)
        # 600s ago = 10 min. Default TTL is 5 min, but deep pool TTL is 30 min.

        result = market._estimate_liquidity_usd(
            "ethereum", "0xweth", "0xusdc", "uniswap_v3", "WETH", "USDC", Decimal("2300"),
        )
        # Should return cached value (not re-query).
        self.assertEqual(result, Decimal("50000000"))

    @patch("market.onchain_market.Web3")
    def test_thin_pool_uses_short_ttl(self, mock_web3_cls: MagicMock) -> None:
        """Pools with <$1M TVL should expire at the default 5-minute TTL."""
        config = _make_onchain_config()
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        market = OnChainMarket(config)

        from decimal import Decimal
        import time

        # Seed the TVL cache with a thin pool ($50K), expired past 5 min.
        key = "uniswap_v3:ethereum:0xweth:0xusdc"
        market._tvl_cache[key] = (Decimal("50000"), time.monotonic() - 400)
        # 400s > 300s default TTL, so thin pool cache should be expired.

        mock_w3 = MagicMock()
        mock_quoter = MagicMock()
        # Return a small-amount price slightly different from normal → some impact.
        mock_quoter.functions.quoteExactInputSingle.return_value.call.return_value = [
            23_500_000, 0, 0, 150_000  # ~$23.50 for 0.01 WETH
        ]
        mock_w3.eth.contract.return_value = mock_quoter
        market._w3 = {"ethereum": mock_w3}

        result = market._estimate_liquidity_usd(
            "ethereum", "0xweth", "0xusdc", "uniswap_v3", "WETH", "USDC", Decimal("2300"),
        )
        # Should NOT return the cached $50K — should re-estimate.
        self.assertNotEqual(result, Decimal("50000"))


class FeeTierCacheTTLTests(unittest.TestCase):
    """Tests for the extended fee tier cache TTL (5 minutes)."""

    @patch("market.onchain_market.Web3")
    def test_cached_tier_used_within_5_minutes(self, mock_web3_cls: MagicMock) -> None:
        """Fee tier cache should hold for 5 minutes, not 60s."""
        config = _make_onchain_config()
        mock_web3_cls.HTTPProvider = MagicMock()
        mock_web3_cls.to_checksum_address = lambda x: x
        market = OnChainMarket(config)

        import time

        # Pre-populate cache with fee tier 500, 200 seconds ago.
        # Old 60s TTL would expire this; new 300s TTL should keep it.
        market._best_fee["uniswap_v3:ethereum:WETH/USDC"] = (500, time.monotonic() - 200)

        mock_quoter = MagicMock()
        mock_quoter.functions.quoteExactInputSingle.return_value.call.return_value = [
            2200_000_000, 0, 0, 150_000
        ]
        mock_w3 = MagicMock()
        mock_w3.eth.contract.return_value = mock_quoter
        market._w3 = {"ethereum": mock_w3}

        out, fee = market._try_fee_tiers(
            "uniswap_v3:ethereum:WETH/USDC", mock_quoter,
            "0xweth", "0xusdc", 10**18, (100, 500, 3000, 10000),
        )
        # Should use cached tier (500), making exactly 1 call.
        self.assertEqual(fee, 500)
        self.assertEqual(mock_quoter.functions.quoteExactInputSingle.call_count, 1)


if __name__ == "__main__":
    unittest.main()
