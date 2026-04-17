"""Tests for the Jito bundle submitter stub (Phase 2d)."""

import os
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "trading_platform" / "src"))

from execution.jito_bundle import JitoBundleSubmitter


class JitoStubTests(unittest.TestCase):
    def test_constructor_refuses(self):
        with self.assertRaises(NotImplementedError) as cm:
            JitoBundleSubmitter()
        self.assertIn("Phase 3b", str(cm.exception))

    def test_constructor_refuses_even_with_args(self):
        with self.assertRaises(NotImplementedError):
            JitoBundleSubmitter(
                block_engine_url="https://mainnet.block-engine.jito.wtf",
                auth_keypair_path="/tmp/fake.json",
            )

    def test_is_configured_reports_env_presence(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JITO_BLOCK_ENGINE_URL", None)
            os.environ.pop("JITO_AUTH_KEYPAIR_PATH", None)
            self.assertFalse(JitoBundleSubmitter.is_configured())
        with patch.dict(os.environ, {
            "JITO_BLOCK_ENGINE_URL": "https://mainnet.block-engine.jito.wtf",
            "JITO_AUTH_KEYPAIR_PATH": "/tmp/fake.json",
        }):
            self.assertTrue(JitoBundleSubmitter.is_configured())

    def test_submit_method_is_defined_but_unreachable(self):
        """submit() on a real instance would raise, but we never reach there."""
        # The class body references `submit` so Phase 3b importers get a clear
        # method signature to see when planning the integration.
        self.assertTrue(hasattr(JitoBundleSubmitter, "submit"))


if __name__ == "__main__":
    unittest.main()
