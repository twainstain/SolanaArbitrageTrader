"""Tests for main.py — CLI argument parsing and mode resolution."""

import sys
from pathlib import Path
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from main import build_parser, _resolve_mode


class BuildParserTests(unittest.TestCase):
    """Verify the CLI parser accepts all expected arguments."""

    @patch("main.load_env")
    def test_parser_has_all_flags(self, _mock_env) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        # All optional args should have defaults
        self.assertIsNone(args.config)
        self.assertIsNone(args.iterations)
        self.assertFalse(args.execute)
        self.assertFalse(args.discover)
        self.assertFalse(args.live)
        self.assertFalse(args.onchain)
        self.assertFalse(args.subgraph)
        self.assertIsNone(args.historical)

    @patch("main.load_env")
    def test_config_flag(self, _mock_env) -> None:
        parser = build_parser()
        args = parser.parse_args(["--config", "my_config.json"])
        self.assertEqual(args.config, "my_config.json")

    @patch("main.load_env")
    def test_iterations_flag(self, _mock_env) -> None:
        parser = build_parser()
        args = parser.parse_args(["--iterations", "42"])
        self.assertEqual(args.iterations, 42)

    @patch("main.load_env")
    def test_dry_run_flag(self, _mock_env) -> None:
        parser = build_parser()
        args = parser.parse_args(["--dry-run"])
        self.assertTrue(args.dry_run)

    @patch("main.load_env")
    def test_execute_flag(self, _mock_env) -> None:
        parser = build_parser()
        args = parser.parse_args(["--execute"])
        self.assertTrue(args.execute)

    @patch("main.load_env")
    def test_discover_flags(self, _mock_env) -> None:
        parser = build_parser()
        args = parser.parse_args(["--discover", "--discover-chain", "arbitrum", "--discover-min-volume", "100000"])
        self.assertTrue(args.discover)
        self.assertEqual(args.discover_chain, "arbitrum")
        self.assertEqual(args.discover_min_volume, 100000.0)

    @patch("main.load_env")
    def test_discover_min_volume_default(self, _mock_env) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        self.assertEqual(args.discover_min_volume, 50_000)

    @patch("main.load_env")
    def test_historical_accepts_multiple_files(self, _mock_env) -> None:
        parser = build_parser()
        args = parser.parse_args(["--historical", "file1.json", "file2.json"])
        self.assertEqual(args.historical, ["file1.json", "file2.json"])

    @patch("main.load_env")
    def test_market_modes_mutually_exclusive(self, _mock_env) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--live", "--onchain"])


class ResolveModeTests(unittest.TestCase):
    """Verify _resolve_mode priority: CLI flag > env var > default."""

    def _make_args(self, **kwargs):
        """Build a minimal Namespace mimicking parsed CLI args."""
        import argparse
        defaults = {"live": False, "onchain": False, "subgraph": False, "historical": None}
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_live_flag_returns_live(self) -> None:
        args = self._make_args(live=True)
        self.assertEqual(_resolve_mode(args), "live")

    def test_onchain_flag_returns_onchain(self) -> None:
        args = self._make_args(onchain=True)
        self.assertEqual(_resolve_mode(args), "onchain")

    def test_subgraph_flag_returns_subgraph(self) -> None:
        args = self._make_args(subgraph=True)
        self.assertEqual(_resolve_mode(args), "subgraph")

    def test_historical_flag_returns_historical(self) -> None:
        args = self._make_args(historical=["data.json"])
        self.assertEqual(_resolve_mode(args), "historical")

    @patch("main.get_bot_mode", return_value="live")
    def test_no_flag_falls_back_to_env(self, _mock_mode) -> None:
        args = self._make_args()
        self.assertEqual(_resolve_mode(args), "live")

    @patch("main.get_bot_mode", return_value="simulated")
    def test_no_flag_no_env_defaults_simulated(self, _mock_mode) -> None:
        args = self._make_args()
        self.assertEqual(_resolve_mode(args), "simulated")

    @patch("main.get_bot_mode", return_value="onchain")
    def test_cli_flag_overrides_env(self, _mock_mode) -> None:
        """CLI --live should override BOT_MODE=onchain in env."""
        args = self._make_args(live=True)
        self.assertEqual(_resolve_mode(args), "live")

    def test_priority_order_live_first(self) -> None:
        """If somehow multiple flags are set, first match wins."""
        args = self._make_args(live=True, onchain=True)
        self.assertEqual(_resolve_mode(args), "live")


if __name__ == "__main__":
    unittest.main()
