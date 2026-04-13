import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arbitrage_bot.config import BotConfig, DexConfig
from arbitrage_bot.live_market import LiveMarket, LiveMarketError


def _make_live_config(**overrides: object) -> BotConfig:
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
            DexConfig(name="Ethereum", base_price=0, fee_bps=30.0, volatility_bps=0, chain="ethereum"),
            DexConfig(name="Base", base_price=0, fee_bps=30.0, volatility_bps=0, chain="base"),
        ],
    )
    defaults.update(overrides)
    config = BotConfig(**defaults)
    config.validate()
    return config


def _mock_defillama_response() -> dict:
    """A realistic DeFi Llama /prices/current response for WETH+USDC on Ethereum and Base."""
    return {
        "coins": {
            "ethereum:0xC02aaA39b223FE8D0A0e5c4F27eAD9083C756Cc2": {
                "decimals": 18,
                "symbol": "WETH",
                "price": 2200.0,
                "timestamp": 1700000000,
                "confidence": 0.99,
            },
            "ethereum:0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48": {
                "decimals": 6,
                "symbol": "USDC",
                "price": 1.0,
                "timestamp": 1700000000,
                "confidence": 0.99,
            },
            "base:0x4200000000000000000000000000000000000006": {
                "decimals": 18,
                "symbol": "WETH",
                "price": 2202.0,
                "timestamp": 1700000000,
                "confidence": 0.99,
            },
            "base:0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913": {
                "decimals": 6,
                "symbol": "USDC",
                "price": 0.9999,
                "timestamp": 1700000000,
                "confidence": 0.99,
            },
        }
    }


class LiveMarketInitTests(unittest.TestCase):
    def test_raises_when_dex_has_no_chain(self) -> None:
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
                DexConfig(name="A", base_price=3000.0, fee_bps=30.0, volatility_bps=0),
                DexConfig(name="B", base_price=3050.0, fee_bps=30.0, volatility_bps=0),
            ],
        )
        config.validate()
        with self.assertRaises(LiveMarketError, msg="no chain configured"):
            LiveMarket(config)

    def test_raises_for_unsupported_chain(self) -> None:
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
                DexConfig(name="A", base_price=0, fee_bps=30.0, volatility_bps=0, chain="ethereum"),
                DexConfig(name="B", base_price=0, fee_bps=30.0, volatility_bps=0, chain="solana"),
            ],
        )
        config.validate()
        with self.assertRaises(LiveMarketError, msg="not in the token registry"):
            LiveMarket(config)

    def test_valid_config_creates_market(self) -> None:
        config = _make_live_config()
        market = LiveMarket(config)
        self.assertIsNotNone(market)


class LiveMarketQuoteTests(unittest.TestCase):
    def _patch_session(self, market: LiveMarket, response_json: dict) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_json
        mock_resp.raise_for_status = MagicMock()
        market._session.get = MagicMock(return_value=mock_resp)

    def test_returns_one_quote_per_venue(self) -> None:
        config = _make_live_config()
        market = LiveMarket(config)
        self._patch_session(market, _mock_defillama_response())

        quotes = market.get_quotes()

        self.assertEqual(len(quotes), 2)
        dex_names = {q.dex for q in quotes}
        self.assertEqual(dex_names, {"Ethereum", "Base"})

    def test_buy_price_above_sell_price(self) -> None:
        config = _make_live_config()
        market = LiveMarket(config)
        self._patch_session(market, _mock_defillama_response())

        quotes = market.get_quotes()
        for q in quotes:
            self.assertGreater(q.buy_price, q.sell_price)

    def test_quotes_reflect_price_differences(self) -> None:
        config = _make_live_config()
        market = LiveMarket(config)
        self._patch_session(market, _mock_defillama_response())

        quotes = market.get_quotes()
        prices = {q.dex: (q.buy_price + q.sell_price) / 2 for q in quotes}

        # Base WETH is $2202 vs Ethereum WETH at $2200,
        # so Base mid-price should be higher.
        self.assertGreater(prices["Base"], prices["Ethereum"])

    def test_pair_field_matches_config(self) -> None:
        config = _make_live_config()
        market = LiveMarket(config)
        self._patch_session(market, _mock_defillama_response())

        quotes = market.get_quotes()
        for q in quotes:
            self.assertEqual(q.pair, "WETH/USDC")

    def test_fee_bps_matches_dex_config(self) -> None:
        config = _make_live_config()
        market = LiveMarket(config)
        self._patch_session(market, _mock_defillama_response())

        quotes = market.get_quotes()
        for q in quotes:
            self.assertEqual(q.fee_bps, 30.0)

    def test_three_chain_venues(self) -> None:
        config = _make_live_config(dexes=[
            DexConfig(name="Ethereum", base_price=0, fee_bps=30.0, volatility_bps=0, chain="ethereum"),
            DexConfig(name="Base", base_price=0, fee_bps=30.0, volatility_bps=0, chain="base"),
            DexConfig(name="Arbitrum", base_price=0, fee_bps=30.0, volatility_bps=0, chain="arbitrum"),
        ])
        market = LiveMarket(config)

        resp = _mock_defillama_response()
        resp["coins"]["arbitrum:0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"] = {
            "decimals": 18, "symbol": "WETH", "price": 2201.5, "timestamp": 1700000000, "confidence": 0.99,
        }
        resp["coins"]["arbitrum:0xaf88d065e77c8cC2239327C5EDb3A432268e5831"] = {
            "decimals": 6, "symbol": "USDC", "price": 1.0001, "timestamp": 1700000000, "confidence": 0.99,
        }
        self._patch_session(market, resp)

        quotes = market.get_quotes()
        self.assertEqual(len(quotes), 3)
        dex_names = {q.dex for q in quotes}
        self.assertEqual(dex_names, {"Ethereum", "Base", "Arbitrum"})


class LiveMarketErrorTests(unittest.TestCase):
    def test_raises_on_http_error(self) -> None:
        import requests as req

        config = _make_live_config()
        market = LiveMarket(config)

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req.HTTPError("503 Service Unavailable")
        market._session.get = MagicMock(return_value=mock_resp)

        with self.assertRaises(LiveMarketError, msg="request failed"):
            market.get_quotes()

    def test_missing_price_data_returns_no_quotes(self) -> None:
        """When DeFi Llama returns no coin data, no quotes are produced."""
        config = _make_live_config()
        market = LiveMarket(config)

        # Return empty coins dict.
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"coins": {}}
        mock_resp.raise_for_status = MagicMock()
        market._session.get = MagicMock(return_value=mock_resp)

        quotes = market.get_quotes()
        self.assertEqual(len(quotes), 0)


class LiveMarketUnsupportedAssetTests(unittest.TestCase):
    def test_unsupported_base_asset_returns_no_quotes(self) -> None:
        """DAI is not in the asset map, so no quotes are produced."""
        config = BotConfig(
            pair="DAI/USDC", base_asset="DAI", quote_asset="USDC",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=0.0, flash_loan_provider="aave_v3",
            slippage_bps=0.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Ethereum", base_price=0, fee_bps=30.0, volatility_bps=0, chain="ethereum"),
                DexConfig(name="Base", base_price=0, fee_bps=30.0, volatility_bps=0, chain="base"),
            ],
        )
        config.validate()
        market = LiveMarket(config)

        mock_resp = MagicMock()
        mock_resp.json.return_value = _mock_defillama_response()
        mock_resp.raise_for_status = MagicMock()
        market._session.get = MagicMock(return_value=mock_resp)

        quotes = market.get_quotes()
        self.assertEqual(len(quotes), 0)

    def test_unsupported_quote_asset_returns_no_quotes(self) -> None:
        config = BotConfig(
            pair="WETH/DAI", base_asset="WETH", quote_asset="DAI",
            trade_size=1.0, min_profit_base=0.0, estimated_gas_cost_base=0.0,
            flash_loan_fee_bps=0.0, flash_loan_provider="aave_v3",
            slippage_bps=0.0, poll_interval_seconds=0.0,
            dexes=[
                DexConfig(name="Ethereum", base_price=0, fee_bps=30.0, volatility_bps=0, chain="ethereum"),
                DexConfig(name="Base", base_price=0, fee_bps=30.0, volatility_bps=0, chain="base"),
            ],
        )
        config.validate()
        market = LiveMarket(config)

        mock_resp = MagicMock()
        mock_resp.json.return_value = _mock_defillama_response()
        mock_resp.raise_for_status = MagicMock()
        market._session.get = MagicMock(return_value=mock_resp)

        quotes = market.get_quotes()
        self.assertEqual(len(quotes), 0)

    def test_usdc_depeg_handled(self) -> None:
        """USDC at $0.98 should still produce valid quotes (price adjusted)."""
        config = _make_live_config()
        market = LiveMarket(config)

        resp = _mock_defillama_response()
        # Simulate USDC de-peg to $0.98
        resp["coins"]["ethereum:0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"]["price"] = 0.98
        resp["coins"]["base:0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"]["price"] = 0.98

        mock_resp = MagicMock()
        mock_resp.json.return_value = resp
        mock_resp.raise_for_status = MagicMock()
        market._session.get = MagicMock(return_value=mock_resp)

        quotes = market.get_quotes()
        # WETH=$2200, USDC=$0.98 → mid_price = 2200/0.98 ≈ 2244.9
        for q in quotes:
            mid = (q.buy_price + q.sell_price) / 2
            self.assertGreater(mid, 2200.0)  # higher than if USDC=$1


if __name__ == "__main__":
    unittest.main()
