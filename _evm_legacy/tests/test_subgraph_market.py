import sys
from pathlib import Path
from unittest.mock import MagicMock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.config import BotConfig, DexConfig
from market.subgraph_market import SubgraphMarket, SubgraphMarketError


def _make_config() -> BotConfig:
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
            DexConfig(name="Uni", base_price=0, fee_bps=5.0, volatility_bps=0,
                      chain="ethereum", dex_type="uniswap_v3"),
            DexConfig(name="Sushi", base_price=0, fee_bps=30.0, volatility_bps=0,
                      chain="ethereum", dex_type="sushi_v3"),
        ],
    )
    config.validate()
    return config


def _mock_uniswap_pool_response(weth_price_usdc: float = 2200.0) -> dict:
    # In the USDC/WETH pool, token0=USDC token1=WETH
    return {
        "data": {
            "pool": {
                "id": "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
                "token0": {"symbol": "USDC", "id": "0xa0b8", "decimals": "6"},
                "token1": {"symbol": "WETH", "id": "0xc02a", "decimals": "18"},
                "feeTier": "500",
                "token0Price": str(1.0 / weth_price_usdc),  # USDC in WETH
                "token1Price": str(weth_price_usdc),         # WETH in USDC
                "liquidity": "12345678",
                "totalValueLockedUSD": "100000000",
            }
        }
    }


class SubgraphMarketInitTests(unittest.TestCase):
    def test_raises_without_api_key(self) -> None:
        config = _make_config()
        with self.assertRaises(SubgraphMarketError, msg="THEGRAPH_API_KEY"):
            SubgraphMarket(config, api_key="")

    def test_raises_when_dex_has_no_chain(self) -> None:
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
        with self.assertRaises(SubgraphMarketError, msg="requires a 'chain'"):
            SubgraphMarket(config, api_key="test_key")

    def test_creates_with_valid_config(self) -> None:
        config = _make_config()
        market = SubgraphMarket(config, api_key="test_key")
        self.assertIsNotNone(market)


class SubgraphMarketQuoteTests(unittest.TestCase):
    def _build_market(self, uni_price: float = 2200.0, sushi_price: float = 2195.0) -> SubgraphMarket:
        config = _make_config()
        market = SubgraphMarket(config, api_key="test_key")

        call_count = {"n": 0}
        prices = [uni_price, sushi_price]

        def mock_post(url, json=None, timeout=None):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = _mock_uniswap_pool_response(prices[call_count["n"]])
            call_count["n"] += 1
            return resp

        market._session.post = mock_post
        return market

    def test_returns_one_quote_per_dex(self) -> None:
        market = self._build_market()
        quotes = market.get_quotes()
        self.assertEqual(len(quotes), 2)
        names = {q.dex for q in quotes}
        self.assertEqual(names, {"Uni", "Sushi"})

    def test_buy_above_sell(self) -> None:
        market = self._build_market()
        quotes = market.get_quotes()
        for q in quotes:
            self.assertGreater(q.buy_price, q.sell_price)

    def test_price_difference_reflected(self) -> None:
        market = self._build_market(uni_price=2200.0, sushi_price=2190.0)
        quotes = market.get_quotes()
        prices = {q.dex: (q.buy_price + q.sell_price) / 2 for q in quotes}
        self.assertGreater(prices["Uni"], prices["Sushi"])

    def test_pair_correct(self) -> None:
        market = self._build_market()
        quotes = market.get_quotes()
        for q in quotes:
            self.assertEqual(q.pair, "WETH/USDC")

    def test_fee_bps_from_config(self) -> None:
        market = self._build_market()
        quotes = market.get_quotes()
        fees = {q.dex: q.fee_bps for q in quotes}
        self.assertEqual(fees["Uni"], 5.0)
        self.assertEqual(fees["Sushi"], 30.0)


class SubgraphMarketErrorTests(unittest.TestCase):
    def test_raises_on_http_error(self) -> None:
        import requests as req

        config = _make_config()
        market = SubgraphMarket(config, api_key="test_key")

        def mock_post(url, json=None, timeout=None):
            raise req.HTTPError("503 Service Unavailable")

        market._session.post = mock_post

        with self.assertRaises(SubgraphMarketError, msg="request failed"):
            market.get_quotes()

    def test_raises_on_graphql_errors(self) -> None:
        config = _make_config()
        market = SubgraphMarket(config, api_key="test_key")

        def mock_post(url, json=None, timeout=None):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"errors": [{"message": "rate limited"}]}
            return resp

        market._session.post = mock_post

        with self.assertRaises(SubgraphMarketError, msg="errors"):
            market.get_quotes()

    def test_raises_when_pool_not_found(self) -> None:
        config = _make_config()
        market = SubgraphMarket(config, api_key="test_key")

        def mock_post(url, json=None, timeout=None):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"data": {"pool": None}}
            return resp

        market._session.post = mock_post

        with self.assertRaises(SubgraphMarketError, msg="not found"):
            market.get_quotes()


class ExtractPriceTests(unittest.TestCase):
    def test_weth_as_token1(self) -> None:
        pool = {
            "token0": {"symbol": "USDC", "id": "0x", "decimals": "6"},
            "token1": {"symbol": "WETH", "id": "0x", "decimals": "18"},
            "token0Price": "0.000454",
            "token1Price": "2200.0",
        }
        price = SubgraphMarket._extract_weth_usdc_price(pool)
        self.assertAlmostEqual(price, 2200.0)

    def test_weth_as_token0(self) -> None:
        pool = {
            "token0": {"symbol": "WETH", "id": "0x", "decimals": "18"},
            "token1": {"symbol": "USDC", "id": "0x", "decimals": "6"},
            "token0Price": "2200.0",
            "token1Price": "0.000454",
        }
        price = SubgraphMarket._extract_weth_usdc_price(pool)
        self.assertAlmostEqual(price, 2200.0)


    def test_no_weth_in_pool_raises(self) -> None:
        pool = {
            "token0": {"symbol": "USDC", "id": "0x", "decimals": "6"},
            "token1": {"symbol": "DAI", "id": "0x", "decimals": "18"},
            "token0Price": "1.0",
            "token1Price": "1.0",
        }
        with self.assertRaises(SubgraphMarketError, msg="Cannot identify WETH"):
            SubgraphMarket._extract_weth_usdc_price(pool)


class BalancerSpotPriceTests(unittest.TestCase):
    def test_50_50_pool_price(self) -> None:
        pool = {
            "tokens": [
                {"symbol": "WETH", "address": "0x", "balance": "100.0", "decimals": 18, "weight": "0.5"},
                {"symbol": "USDC", "address": "0x", "balance": "220000.0", "decimals": 6, "weight": "0.5"},
            ]
        }
        price = SubgraphMarket._balancer_spot_price(pool)
        self.assertAlmostEqual(price, 2200.0)

    def test_zero_weth_weight_raises(self) -> None:
        pool = {
            "tokens": [
                {"symbol": "WETH", "address": "0x", "balance": "100.0", "decimals": 18, "weight": "0.0"},
                {"symbol": "USDC", "address": "0x", "balance": "220000.0", "decimals": 6, "weight": "0.5"},
            ]
        }
        with self.assertRaises(SubgraphMarketError, msg="zero WETH"):
            SubgraphMarket._balancer_spot_price(pool)

    def test_zero_usdc_weight_raises(self) -> None:
        pool = {
            "tokens": [
                {"symbol": "WETH", "address": "0x", "balance": "100.0", "decimals": 18, "weight": "0.5"},
                {"symbol": "USDC", "address": "0x", "balance": "220000.0", "decimals": 6, "weight": "0.0"},
            ]
        }
        with self.assertRaises(SubgraphMarketError, msg="zero USDC"):
            SubgraphMarket._balancer_spot_price(pool)

    def test_missing_weth_raises(self) -> None:
        pool = {
            "tokens": [
                {"symbol": "DAI", "address": "0x", "balance": "100.0", "decimals": 18, "weight": "0.5"},
                {"symbol": "USDC", "address": "0x", "balance": "220000.0", "decimals": 6, "weight": "0.5"},
            ]
        }
        with self.assertRaises(SubgraphMarketError, msg="Cannot find WETH"):
            SubgraphMarket._balancer_spot_price(pool)

    def test_80_20_pool_price(self) -> None:
        """An 80/20 pool should give a different price than 50/50."""
        pool = {
            "tokens": [
                {"symbol": "WETH", "address": "0x", "balance": "100.0", "decimals": 18, "weight": "0.8"},
                {"symbol": "USDC", "address": "0x", "balance": "220000.0", "decimals": 6, "weight": "0.2"},
            ]
        }
        price = SubgraphMarket._balancer_spot_price(pool)
        # (220000/0.2) / (100/0.8) = 1100000 / 125 = 8800
        self.assertAlmostEqual(price, 8800.0)


if __name__ == "__main__":
    unittest.main()
