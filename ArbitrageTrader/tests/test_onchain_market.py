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

        # Each contract call returns a mock with .call() returning the quoter result.
        def make_contract_mock(price: float) -> MagicMock:
            contract = MagicMock()
            call_result = _mock_quoter_result(price)
            contract.functions.quoteExactInputSingle.return_value.call.return_value = call_result
            return contract

        uni_contract = make_contract_mock(uni_price_usdc)
        sushi_contract = make_contract_mock(sushi_price_usdc)

        call_count = {"n": 0}
        contracts = [uni_contract, sushi_contract]

        def fake_contract(address, abi):
            c = contracts[call_count["n"]]
            call_count["n"] += 1
            return c

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
                # Re-setup contracts for get_quotes call.
                call_count["n"] = 0
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
        quotes = self._build_market_with_mocked_contracts(
            uni_price_usdc=2200.0, sushi_price_usdc=2190.0
        )
        prices = {q.dex: (q.buy_price + q.sell_price) / 2 for q in quotes}
        self.assertGreater(prices["Uniswap-Eth"], prices["Sushi-Eth"])

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

        # Uniswap mock
        uni_contract = MagicMock()
        uni_contract.functions.quoteExactInputSingle.return_value.call.return_value = [
            int(2200 * 10**6), 0, 0, 150_000
        ]

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

        # Uniswap mock succeeds, Sushi will fail because we patch SUSHI_V3_QUOTER
        uni_contract = MagicMock()
        uni_contract.functions.quoteExactInputSingle.return_value.call.return_value = [
            int(2200 * 10**6), 0, 0, 150_000
        ]
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


if __name__ == "__main__":
    unittest.main()
