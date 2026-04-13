import sys
from pathlib import Path
from unittest.mock import MagicMock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arbitrage_bot.price_downloader import (
    DownloadError,
    _parse_date,
    _resolve_subgraph_and_pool,
    download_hourly_data,
)


class ResolveSubgraphTests(unittest.TestCase):
    def test_uniswap_v3_ethereum(self) -> None:
        sg_id, pool = _resolve_subgraph_and_pool("uniswap_v3", "ethereum")
        self.assertTrue(sg_id)  # non-empty
        self.assertTrue(pool.startswith("0x"))

    def test_uniswap_v3_arbitrum(self) -> None:
        sg_id, pool = _resolve_subgraph_and_pool("uniswap_v3", "arbitrum")
        self.assertTrue(sg_id)
        self.assertTrue(pool.startswith("0x"))

    def test_sushi_v3_ethereum(self) -> None:
        sg_id, pool = _resolve_subgraph_and_pool("sushi_v3", "ethereum")
        self.assertTrue(sg_id)
        self.assertTrue(pool.startswith("0x"))

    def test_unknown_dex_raises(self) -> None:
        with self.assertRaises(DownloadError, msg="Unsupported dex"):
            _resolve_subgraph_and_pool("pancake_v3", "ethereum")

    def test_unknown_chain_raises(self) -> None:
        with self.assertRaises(DownloadError, msg="No subgraph"):
            _resolve_subgraph_and_pool("sushi_v3", "base")


class DownloadHourlyDataTests(unittest.TestCase):
    def test_download_returns_correct_structure(self) -> None:
        import requests

        original_session = requests.Session

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "data": {
                "poolHourDatas": [
                    {
                        "periodStartUnix": 1700000000,
                        "open": "2200.0",
                        "high": "2210.0",
                        "low": "2190.0",
                        "close": "2205.0",
                        "token0Price": "0.000454",
                        "token1Price": "2205.0",
                        "liquidity": "12345678",
                        "volumeUSD": "1000000",
                    },
                    {
                        "periodStartUnix": 1700003600,
                        "open": "2205.0",
                        "high": "2215.0",
                        "low": "2195.0",
                        "close": "2210.0",
                        "token0Price": "0.000452",
                        "token1Price": "2210.0",
                        "liquidity": "12345678",
                        "volumeUSD": "900000",
                    },
                ]
            }
        }

        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp
        requests.Session = lambda: mock_session

        try:
            result = download_hourly_data("uniswap_v3", "ethereum", days=1, api_key="test_key")
        finally:
            requests.Session = original_session

        self.assertEqual(result["dex"], "uniswap_v3")
        self.assertEqual(result["chain"], "ethereum")
        self.assertEqual(result["pair"], "WETH/USDC")
        self.assertEqual(result["snapshot_count"], 2)
        self.assertEqual(len(result["snapshots"]), 2)

        snap = result["snapshots"][0]
        self.assertEqual(snap["timestamp"], 1700000000)
        self.assertAlmostEqual(snap["close"], 2205.0)
        self.assertIn("downloaded_at", result)

    def test_download_handles_pagination(self) -> None:
        import requests

        original_session = requests.Session
        call_count = {"n": 0}

        def make_response():
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if call_count["n"] == 0:
                # First page: return 2 items (simulating partial page = last page)
                resp.json.return_value = {
                    "data": {
                        "poolHourDatas": [
                            {"periodStartUnix": 1700000000, "open": "2200", "high": "2210",
                             "low": "2190", "close": "2205", "token0Price": "0.0004",
                             "token1Price": "2205", "liquidity": "1", "volumeUSD": "1"},
                        ]
                    }
                }
            else:
                resp.json.return_value = {"data": {"poolHourDatas": []}}
            call_count["n"] += 1
            return resp

        mock_session = MagicMock()
        mock_session.post.side_effect = lambda *a, **kw: make_response()
        requests.Session = lambda: mock_session

        try:
            result = download_hourly_data("uniswap_v3", "ethereum", days=1, api_key="test_key")
        finally:
            requests.Session = original_session

        self.assertEqual(result["snapshot_count"], 1)


class ParseDateTests(unittest.TestCase):
    def test_date_only(self) -> None:
        ts = _parse_date("2026-01-15")
        # 2026-01-15 00:00 UTC
        self.assertEqual(ts, 1768435200)

    def test_date_and_time(self) -> None:
        ts = _parse_date("2026-01-15 12:30")
        # 2026-01-15 12:30 UTC
        self.assertEqual(ts, 1768480200)

    def test_invalid_format_raises(self) -> None:
        with self.assertRaises(DownloadError, msg="Cannot parse"):
            _parse_date("15/01/2026")

    def test_empty_string_raises(self) -> None:
        with self.assertRaises(DownloadError):
            _parse_date("")


class DateRangeTests(unittest.TestCase):
    """Test that start/end parameters flow through to download_hourly_data."""

    def _mock_download(self, **kwargs):
        import requests

        original_session = requests.Session
        captured = {}

        def mock_post(url, json=None, timeout=None):
            # Capture the variables sent to the subgraph.
            captured.update(json.get("variables", {}))
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"data": {"poolHourDatas": []}}
            return resp

        mock_session = MagicMock()
        mock_session.post.side_effect = mock_post
        requests.Session = lambda: mock_session

        try:
            download_hourly_data(
                dex="uniswap_v3", chain="ethereum", api_key="test_key", **kwargs
            )
        finally:
            requests.Session = original_session

        return captured

    def test_start_and_end(self) -> None:
        captured = self._mock_download(start="2026-01-01", end="2026-01-31")
        self.assertEqual(captured["startTime"], _parse_date("2026-01-01"))
        self.assertEqual(captured["endTime"], _parse_date("2026-01-31"))

    def test_start_only_uses_now_as_end(self) -> None:
        import time
        before = int(time.time())
        captured = self._mock_download(start="2026-01-01")
        after = int(time.time())
        self.assertEqual(captured["startTime"], _parse_date("2026-01-01"))
        self.assertGreaterEqual(captured["endTime"], before)
        self.assertLessEqual(captured["endTime"], after)

    def test_days_only(self) -> None:
        import time
        before = int(time.time())
        captured = self._mock_download(days=30)
        expected_start = before - (30 * 86400)
        # Allow 2 seconds of drift.
        self.assertAlmostEqual(captured["startTime"], expected_start, delta=2)

    def test_start_after_end_raises(self) -> None:
        with self.assertRaises(DownloadError, msg="must be before"):
            self._mock_download(start="2026-03-01", end="2026-01-01")


class MessariPriceDerivationTests(unittest.TestCase):
    """Test _derive_messari_price with different data scenarios."""

    def test_usd_values_populated(self) -> None:
        from arbitrage_bot.price_downloader import _derive_messari_price

        snapshot = {
            "timestamp": "1700000000",
            "inputTokenBalances": ["248178686711653888", "495448415"],
            "inputTokenBalancesUSD": ["570.59", "495.45"],
            "pool": {
                "inputTokens": [
                    {"symbol": "WETH", "decimals": 18},
                    {"symbol": "USDC", "decimals": 6},
                ]
            },
            "tick": None,
        }
        price = _derive_messari_price(snapshot)
        # 570.59 / (248178686711653888 / 1e18) ≈ 570.59 / 0.2482 ≈ 2299
        self.assertGreater(price, 2000)
        self.assertLess(price, 3000)

    def test_tick_fallback_when_usd_is_zero(self) -> None:
        from arbitrage_bot.price_downloader import _derive_messari_price

        # token0=WETH(18), token1=USDC(6) → tick is negative for WETH>$1.
        # raw_price = 1.0001^(-199357) ≈ 2.2e-9
        # adjusted = 2.2e-9 * 10^(18-6) = 2200  (WETH is token0 so returned directly)
        snapshot = {
            "timestamp": "1700000000",
            "inputTokenBalances": ["15404456874935752589572", "21116847567644"],
            "inputTokenBalancesUSD": ["0", "0"],
            "pool": {
                "inputTokens": [
                    {"symbol": "WETH", "decimals": 18},
                    {"symbol": "USDC", "decimals": 6},
                ]
            },
            "tick": "-199357",
        }
        price = _derive_messari_price(snapshot)
        self.assertGreater(price, 1500)
        self.assertLess(price, 3000)

    def test_tick_zero(self) -> None:
        from arbitrage_bot.price_downloader import _derive_messari_price

        snapshot = {
            "timestamp": "1700000000",
            "inputTokenBalances": ["1000000000000000000", "1000000"],
            "inputTokenBalancesUSD": ["0", "0"],
            "pool": {
                "inputTokens": [
                    {"symbol": "WETH", "decimals": 18},
                    {"symbol": "USDC", "decimals": 6},
                ]
            },
            "tick": "0",
        }
        price = _derive_messari_price(snapshot)
        # 1.0001^0 = 1, adjusted by 10^(18-6) = 1e12
        self.assertGreater(price, 0)

    def test_usdc_as_token0_positive_tick(self) -> None:
        from arbitrage_bot.price_downloader import _derive_messari_price

        # token0=USDC(6), token1=WETH(18) → tick is positive for WETH>$1.
        # raw_price = 1.0001^199357 ≈ 4.545e8
        # adjusted = 4.545e8 * 10^(6-18) = 4.545e-4
        # USDC is token0 (not WETH), so we invert: 1/4.545e-4 ≈ 2200
        snapshot = {
            "timestamp": "1700000000",
            "inputTokenBalances": ["21116847567644", "15404456874935752589572"],
            "inputTokenBalancesUSD": ["0", "0"],
            "pool": {
                "inputTokens": [
                    {"symbol": "USDC", "decimals": 6},
                    {"symbol": "WETH", "decimals": 18},
                ]
            },
            "tick": "199357",
        }
        price = _derive_messari_price(snapshot)
        self.assertGreater(price, 1500)
        self.assertLess(price, 3000)

    def test_balance_ratio_fallback(self) -> None:
        from arbitrage_bot.price_downloader import _derive_messari_price

        # No USD values, no tick — falls back to balance ratio
        snapshot = {
            "timestamp": "1700000000",
            "inputTokenBalances": ["1000000000000000000", "2200000000"],
            "inputTokenBalancesUSD": ["0", "0"],
            "pool": {
                "inputTokens": [
                    {"symbol": "WETH", "decimals": 18},
                    {"symbol": "USDC", "decimals": 6},
                ]
            },
            "tick": None,
        }
        price = _derive_messari_price(snapshot)
        # 2200000000/1e6 / (1000000000000000000/1e18) = 2200/1 = 2200
        self.assertAlmostEqual(price, 2200.0)


if __name__ == "__main__":
    unittest.main()
