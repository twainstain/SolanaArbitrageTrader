import json
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.config import BotConfig, DexConfig, FLASH_LOAN_PROVIDERS


def _base_kwargs() -> dict:
    return dict(
        pair="WETH/USDC",
        base_asset="WETH",
        quote_asset="USDC",
        trade_size=1.0,
        min_profit_base=0.01,
        estimated_gas_cost_base=0.003,
        flash_loan_fee_bps=9.0,
        flash_loan_provider="aave_v3",
        slippage_bps=10.0,
        poll_interval_seconds=0.5,
        dexes=[
            DexConfig(name="A", base_price=3000.0, fee_bps=30.0, volatility_bps=10.0),
            DexConfig(name="B", base_price=3050.0, fee_bps=30.0, volatility_bps=10.0),
        ],
    )


def _make_config(**overrides: object) -> BotConfig:
    kwargs = _base_kwargs()
    kwargs.update(overrides)
    return BotConfig(**kwargs)


class ConfigValidationTests(unittest.TestCase):
    def test_valid_config_passes(self) -> None:
        config = _make_config()
        config.validate()  # should not raise

    def test_fewer_than_two_dexes_raises(self) -> None:
        config = _make_config(dexes=[
            DexConfig(name="A", base_price=3000.0, fee_bps=30.0, volatility_bps=10.0),
        ])
        with self.assertRaises(ValueError, msg="At least two DEX"):
            config.validate()

    def test_zero_trade_size_raises(self) -> None:
        config = _make_config(trade_size=0.0)
        with self.assertRaises(ValueError, msg="trade_size must be positive"):
            config.validate()

    def test_negative_trade_size_raises(self) -> None:
        config = _make_config(trade_size=-1.0)
        with self.assertRaises(ValueError):
            config.validate()

    def test_negative_poll_interval_raises(self) -> None:
        config = _make_config(poll_interval_seconds=-1.0)
        with self.assertRaises(ValueError):
            config.validate()

    def test_negative_min_profit_raises(self) -> None:
        config = _make_config(min_profit_base=-0.01)
        with self.assertRaises(ValueError):
            config.validate()

    def test_negative_gas_cost_raises(self) -> None:
        config = _make_config(estimated_gas_cost_base=-0.001)
        with self.assertRaises(ValueError):
            config.validate()

    def test_negative_flash_fee_raises(self) -> None:
        config = _make_config(flash_loan_fee_bps=-1.0)
        with self.assertRaises(ValueError):
            config.validate()

    def test_negative_slippage_raises(self) -> None:
        config = _make_config(slippage_bps=-1.0)
        with self.assertRaises(ValueError):
            config.validate()

    def test_invalid_flash_loan_provider_raises(self) -> None:
        config = _make_config(flash_loan_provider="unknown")
        with self.assertRaises(ValueError, msg="flash_loan_provider"):
            config.validate()

    def test_balancer_provider_accepted(self) -> None:
        config = _make_config(flash_loan_provider="balancer")
        config.validate()  # should not raise

    def test_dex_zero_base_price_raises(self) -> None:
        config = _make_config(dexes=[
            DexConfig(name="A", base_price=0.0, fee_bps=30.0, volatility_bps=10.0),
            DexConfig(name="B", base_price=3050.0, fee_bps=30.0, volatility_bps=10.0),
        ])
        with self.assertRaises(ValueError, msg="base_price must be positive"):
            config.validate()

    def test_dex_fee_bps_out_of_range_raises(self) -> None:
        config = _make_config(dexes=[
            DexConfig(name="A", base_price=3000.0, fee_bps=10000.0, volatility_bps=10.0),
            DexConfig(name="B", base_price=3050.0, fee_bps=30.0, volatility_bps=10.0),
        ])
        with self.assertRaises(ValueError, msg="fee_bps"):
            config.validate()

    def test_dex_negative_volatility_raises(self) -> None:
        config = _make_config(dexes=[
            DexConfig(name="A", base_price=3000.0, fee_bps=30.0, volatility_bps=-1.0),
            DexConfig(name="B", base_price=3050.0, fee_bps=30.0, volatility_bps=10.0),
        ])
        with self.assertRaises(ValueError, msg="volatility_bps"):
            config.validate()


class ConfigFromFileTests(unittest.TestCase):
    def test_from_file_loads_correctly(self) -> None:
        data = {
            "pair": "WETH/USDC",
            "base_asset": "WETH",
            "quote_asset": "USDC",
            "trade_size": 1.5,
            "min_profit_base": 0.008,
            "estimated_gas_cost_base": 0.002,
            "flash_loan_fee_bps": 9.0,
            "flash_loan_provider": "aave_v3",
            "slippage_bps": 15.0,
            "poll_interval_seconds": 0.5,
            "dexes": [
                {"name": "UniSim", "base_price": 3008.0, "fee_bps": 30.0, "volatility_bps": 18.0},
                {"name": "SushiSim", "base_price": 3092.0, "fee_bps": 30.0, "volatility_bps": 22.0},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            config = BotConfig.from_file(f.name)

        self.assertEqual(config.pair, "WETH/USDC")
        self.assertEqual(config.trade_size, 1.5)
        self.assertEqual(config.flash_loan_provider, "aave_v3")
        self.assertEqual(len(config.dexes), 2)

    def test_from_file_defaults_flash_loan_provider(self) -> None:
        data = {
            "pair": "WETH/USDC",
            "base_asset": "WETH",
            "quote_asset": "USDC",
            "trade_size": 1.0,
            "min_profit_base": 0.01,
            "estimated_gas_cost_base": 0.003,
            "flash_loan_fee_bps": 9.0,
            "slippage_bps": 10.0,
            "poll_interval_seconds": 0.5,
            "dexes": [
                {"name": "A", "base_price": 3000.0, "fee_bps": 30.0, "volatility_bps": 10.0},
                {"name": "B", "base_price": 3050.0, "fee_bps": 30.0, "volatility_bps": 10.0},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            config = BotConfig.from_file(f.name)

        self.assertEqual(config.flash_loan_provider, "aave_v3")

    def test_from_file_legacy_eth_fields(self) -> None:
        data = {
            "pair": "WETH/USDC",
            "base_asset": "WETH",
            "quote_asset": "USDC",
            "trade_size": 1.0,
            "min_profit_eth": 0.01,
            "estimated_gas_cost_eth": 0.003,
            "flash_loan_fee_bps": 9.0,
            "slippage_bps": 10.0,
            "poll_interval_seconds": 0.5,
            "dexes": [
                {"name": "A", "base_price": 3000.0, "fee_bps": 30.0, "volatility_bps": 10.0},
                {"name": "B", "base_price": 3050.0, "fee_bps": 30.0, "volatility_bps": 10.0},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            config = BotConfig.from_file(f.name)

        from decimal import Decimal
        self.assertEqual(config.min_profit_base, Decimal("0.01"))
        self.assertEqual(config.estimated_gas_cost_base, Decimal("0.003"))

    def test_from_file_preserves_extra_pair_metadata(self) -> None:
        data = {
            "pair": "WETH/USDC",
            "base_asset": "WETH",
            "quote_asset": "USDC",
            "trade_size": 1.0,
            "min_profit_base": 0.01,
            "estimated_gas_cost_base": 0.003,
            "flash_loan_fee_bps": 9.0,
            "slippage_bps": 10.0,
            "poll_interval_seconds": 0.5,
            "extra_pairs": [
                {
                    "pair": "OP/USDC",
                    "base_asset": "OP",
                    "quote_asset": "USDC",
                    "trade_size": 1500,
                    "base_address": "0x4200000000000000000000000000000000000042",
                    "quote_address": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
                    "chain": "optimism",
                }
            ],
            "dexes": [
                {"name": "A", "base_price": 3000.0, "fee_bps": 30.0, "volatility_bps": 10.0},
                {"name": "B", "base_price": 3050.0, "fee_bps": 30.0, "volatility_bps": 10.0},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            config = BotConfig.from_file(f.name)

        self.assertIsNotNone(config.extra_pairs)
        pair = config.extra_pairs[0]
        self.assertEqual(pair.base_address, "0x4200000000000000000000000000000000000042")
        self.assertEqual(pair.quote_address, "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85")
        self.assertEqual(pair.chain, "optimism")


class FlashLoanProvidersConstTests(unittest.TestCase):
    def test_expected_providers(self) -> None:
        self.assertIn("aave_v3", FLASH_LOAN_PROVIDERS)
        self.assertIn("balancer", FLASH_LOAN_PROVIDERS)


class PairConfigMaxExposureTests(unittest.TestCase):
    """Tests for PairConfig.max_exposure field."""

    def test_max_exposure_none_by_default(self) -> None:
        from core.config import PairConfig
        pc = PairConfig(pair="WETH/USDC", base_asset="WETH",
                        quote_asset="USDC", trade_size=1.0)
        self.assertIsNone(pc.max_exposure)

    def test_max_exposure_set_from_float(self) -> None:
        from core.config import PairConfig
        from decimal import Decimal
        pc = PairConfig(pair="OP/USDC", base_asset="OP",
                        quote_asset="USDC", trade_size=20000.0,
                        max_exposure=25000.0)
        self.assertEqual(pc.max_exposure, Decimal("25000.0"))

    def test_max_exposure_parsed_from_json(self) -> None:
        from decimal import Decimal
        data = {
            "pair": "WETH/USDC",
            "base_asset": "WETH",
            "quote_asset": "USDC",
            "trade_size": 1.0,
            "min_profit_base": 0.01,
            "estimated_gas_cost_base": 0.003,
            "flash_loan_fee_bps": 9.0,
            "slippage_bps": 10.0,
            "poll_interval_seconds": 0.5,
            "extra_pairs": [
                {
                    "pair": "OP/USDC",
                    "base_asset": "OP",
                    "quote_asset": "USDC",
                    "trade_size": 20000,
                    "chain": "optimism",
                    "max_exposure": 25000,
                }
            ],
            "dexes": [
                {"name": "A", "base_price": 3000.0, "fee_bps": 30.0, "volatility_bps": 10.0},
                {"name": "B", "base_price": 3050.0, "fee_bps": 30.0, "volatility_bps": 10.0},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            config = BotConfig.from_file(f.name)

        pair = config.extra_pairs[0]
        self.assertEqual(pair.max_exposure, Decimal("25000"))

    def test_max_exposure_absent_in_json_is_none(self) -> None:
        data = {
            "pair": "WETH/USDC",
            "base_asset": "WETH",
            "quote_asset": "USDC",
            "trade_size": 1.0,
            "min_profit_base": 0.01,
            "estimated_gas_cost_base": 0.003,
            "flash_loan_fee_bps": 9.0,
            "slippage_bps": 10.0,
            "poll_interval_seconds": 0.5,
            "extra_pairs": [
                {
                    "pair": "WETH/USDT",
                    "base_asset": "WETH",
                    "quote_asset": "USDT",
                    "trade_size": 1.0,
                }
            ],
            "dexes": [
                {"name": "A", "base_price": 3000.0, "fee_bps": 30.0, "volatility_bps": 10.0},
                {"name": "B", "base_price": 3050.0, "fee_bps": 30.0, "volatility_bps": 10.0},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            config = BotConfig.from_file(f.name)

        pair = config.extra_pairs[0]
        self.assertIsNone(pair.max_exposure)


class MinLiquidityForChainTests(unittest.TestCase):
    """Tests for BotConfig.min_liquidity_for_chain() static method."""

    def test_ethereum_returns_1m(self) -> None:
        from decimal import Decimal
        self.assertEqual(BotConfig.min_liquidity_for_chain("ethereum"), Decimal("1000000"))

    def test_arbitrum_returns_50k(self) -> None:
        from decimal import Decimal
        self.assertEqual(BotConfig.min_liquidity_for_chain("arbitrum"), Decimal("50000"))

    def test_base_returns_100k(self) -> None:
        from decimal import Decimal
        self.assertEqual(BotConfig.min_liquidity_for_chain("base"), Decimal("100000"))

    def test_optimism_returns_100k(self) -> None:
        from decimal import Decimal
        self.assertEqual(BotConfig.min_liquidity_for_chain("optimism"), Decimal("100000"))

    def test_unknown_chain_returns_1m(self) -> None:
        from decimal import Decimal
        self.assertEqual(BotConfig.min_liquidity_for_chain("zksync"), Decimal("1000000"))

    def test_case_insensitive(self) -> None:
        from decimal import Decimal
        self.assertEqual(BotConfig.min_liquidity_for_chain("Arbitrum"), Decimal("50000"))


if __name__ == "__main__":
    unittest.main()
