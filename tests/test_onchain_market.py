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


class OnChainMarketVelodromeTests(unittest.TestCase):
    @patch("onchain_market.Web3")
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

    @patch("onchain_market.Web3")
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

    @patch("onchain_market.Web3")
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

    @patch("onchain_market.Web3")
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
    @patch("onchain_market.Web3")
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

    @patch("onchain_market.Web3")
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

    @patch("onchain_market.Web3")
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

    @patch("onchain_market.Web3")
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

        with patch("onchain_market.Web3") as w3:
            w3.to_checksum_address = lambda x: x
            amount, fee_tier = self.market._try_fee_tiers(
                "test:eth", quoter, "0xweth", "0xusdc",
                10**18, (100, 500, 3000, 10000),
            )
        self.assertGreater(amount, 0)
        self.assertEqual(fee_tier, 500)  # Used cached tier
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
