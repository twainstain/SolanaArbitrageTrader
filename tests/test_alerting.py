"""Tests for the alerting module — dispatcher + Discord/Gmail/Telegram backends.

Adapted from the EVM repo for Solana: signatures replace tx hashes, Solscan
replaces Etherscan/Arbiscan/Basescan, `chain` is still accepted on dispatcher
helpers for API symmetry but is ignored by the explorer URL helper.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "trading_platform" / "src"))

from alerting.dispatcher import AlertDispatcher, tx_explorer_url, opp_dashboard_url
from alerting.discord import DiscordAlert
from alerting.gmail import GmailAlert
from alerting.telegram import TelegramAlert


# Sample Solana signature (base58, 88 chars typical; using a short legible one).
SAMPLE_SIG = "5j7s4k3n2mEXAMPLE1111111111111111111111111111111111111111111111111111111111111111111"
SAMPLE_SIG_2 = "3Ks9tZpEXAMPLE22222222222222222222222222222222222222222222222222222222222222222222"


# ---------------------------------------------------------------
# Dispatcher tests
# ---------------------------------------------------------------

class _FakeBackend:
    def __init__(self, name: str = "fake", should_fail: bool = False):
        self._name = name
        self._should_fail = should_fail
        self.received: list[tuple] = []

    @property
    def name(self) -> str:
        return self._name

    def send(self, event_type, message, details=None):
        if self._should_fail:
            raise RuntimeError("boom")
        self.received.append((event_type, message, details))
        return True


class DispatcherTests(unittest.TestCase):
    def test_no_backends_returns_zero(self):
        d = AlertDispatcher()
        self.assertEqual(d.alert("test", "hello"), 0)

    def test_routes_to_all_backends(self):
        b1 = _FakeBackend("b1")
        b2 = _FakeBackend("b2")
        d = AlertDispatcher([b1, b2])
        count = d.alert("trade_executed", "msg")
        self.assertEqual(count, 2)
        self.assertEqual(len(b1.received), 1)
        self.assertEqual(len(b2.received), 1)

    def test_failing_backend_doesnt_crash(self):
        good = _FakeBackend("good")
        bad = _FakeBackend("bad", should_fail=True)
        d = AlertDispatcher([bad, good])
        count = d.alert("system_error", "oops")
        self.assertEqual(count, 1)
        self.assertEqual(len(good.received), 1)

    def test_add_backend(self):
        d = AlertDispatcher()
        d.add_backend(_FakeBackend("x"))
        self.assertEqual(d.backend_count, 1)

    def test_opportunity_found_helper(self):
        b = _FakeBackend()
        d = AlertDispatcher([b])
        d.opportunity_found("SOL/USDC", "Orca-SOL/USDC", "Jupiter-Best", 0.12, 0.0009)
        self.assertEqual(len(b.received), 1)
        self.assertEqual(b.received[0][0], "opportunity_found")
        self.assertIn("SOL/USDC", b.received[0][1])

    def test_opportunity_found_includes_dashboard_link(self):
        b = _FakeBackend()
        d = AlertDispatcher([b])
        d.opportunity_found(
            "SOL/USDC", "Orca-SOL/USDC", "Jupiter-Best", 0.15, 0.0012,
            opp_id="opp_abc123",
        )
        msg = b.received[0][1]
        details = b.received[0][2]
        self.assertIn("https://arb-trader-solana.yeda-ai.com/opportunity/opp_abc123", msg)
        self.assertEqual(details["opp_id"], "opp_abc123")
        self.assertIn("dashboard_link", details)

    def test_trade_executed_helper(self):
        b = _FakeBackend()
        d = AlertDispatcher([b])
        d.trade_executed("SOL/USDC", SAMPLE_SIG, 0.001)
        self.assertEqual(b.received[0][0], "trade_executed")

    def test_trade_executed_includes_links(self):
        b = _FakeBackend()
        d = AlertDispatcher([b])
        d.trade_executed(
            "SOL/USDC", SAMPLE_SIG, 0.0008,
            opp_id="opp_def456", chain="solana",
        )
        msg = b.received[0][1]
        details = b.received[0][2]
        self.assertIn(f"solscan.io/tx/{SAMPLE_SIG}", msg)
        self.assertIn("arb-trader-solana.yeda-ai.com/opportunity/opp_def456", msg)
        self.assertEqual(details["tx_link"], f"https://solscan.io/tx/{SAMPLE_SIG}")
        self.assertEqual(
            details["dashboard_link"],
            "https://arb-trader-solana.yeda-ai.com/opportunity/opp_def456",
        )

    def test_trade_reverted_helper(self):
        b = _FakeBackend()
        d = AlertDispatcher([b])
        d.trade_reverted("SOL/USDC", SAMPLE_SIG_2, "slippage_exceeded")
        self.assertEqual(b.received[0][0], "trade_reverted")

    def test_trade_reverted_includes_links(self):
        b = _FakeBackend()
        d = AlertDispatcher([b])
        d.trade_reverted(
            "SOL/USDC", SAMPLE_SIG_2, "slippage_exceeded",
            opp_id="opp_ghi012", chain="solana",
        )
        msg = b.received[0][1]
        self.assertIn(f"solscan.io/tx/{SAMPLE_SIG_2}", msg)
        self.assertIn("arb-trader-solana.yeda-ai.com/opportunity/opp_ghi012", msg)

    def test_system_error_helper(self):
        b = _FakeBackend()
        d = AlertDispatcher([b])
        d.system_error("scanner", "RPC timeout")
        self.assertEqual(b.received[0][0], "system_error")
        self.assertIn("scanner", b.received[0][1])

    def test_daily_summary_helper(self):
        b = _FakeBackend()
        d = AlertDispatcher([b])
        d.daily_summary(250, 4, 0, 0.0, 0)
        self.assertEqual(b.received[0][0], "daily_summary")
        self.assertIn("250", b.received[0][1])


# ---------------------------------------------------------------
# Discord backend tests
# ---------------------------------------------------------------

class DiscordTests(unittest.TestCase):
    def test_not_configured_returns_false(self):
        d = DiscordAlert(webhook_url="")
        self.assertFalse(d.configured)
        self.assertFalse(d.send("trade_executed", "msg"))

    def test_name(self):
        self.assertEqual(DiscordAlert(webhook_url="").name, "discord")

    @patch("alerting.discord.requests.post")
    def test_send_success(self, mock_post):
        mock_post.return_value = MagicMock(status_code=204)
        d = DiscordAlert(webhook_url="https://discord.com/api/webhooks/fake")
        self.assertTrue(d.send(
            "trade_executed", "profit!",
            {"pair": "SOL/USDC", "profit": "0.001"},
        ))
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["embeds"][0]["title"], "Trade Executed")
        self.assertGreater(len(payload["embeds"][0]["fields"]), 0)

    @patch("alerting.discord.requests.post")
    def test_send_webhook_error(self, mock_post):
        mock_post.return_value = MagicMock(status_code=429, text="Rate limited")
        d = DiscordAlert(webhook_url="https://discord.com/api/webhooks/fake")
        self.assertFalse(d.send("trade_executed", "msg"))

    @patch("alerting.discord.requests.post", side_effect=Exception("network"))
    def test_send_network_error(self, mock_post):
        d = DiscordAlert(webhook_url="https://discord.com/api/webhooks/fake")
        self.assertFalse(d.send("trade_executed", "msg"))

    def test_filtered_event_returns_true_without_post(self):
        """Events not in ALLOWED_EVENTS are silently skipped (returns True)."""
        d = DiscordAlert(webhook_url="https://discord.com/api/webhooks/fake")
        self.assertTrue(d.send("opportunity_found", "SOL/USDC spread"))
        self.assertTrue(d.send("simulation_failed", "sim failed"))

    @patch("alerting.discord.requests.post")
    def test_embed_includes_tx_and_dashboard_field(self, mock_post):
        mock_post.return_value = MagicMock(status_code=204)
        d = DiscordAlert(webhook_url="https://discord.com/api/webhooks/fake")
        d.send("trade_executed", "executed", {
            "pair": "SOL/USDC",
            "tx_link": "https://solscan.io/tx/abc",
            "dashboard_link": "https://arb-trader-solana.yeda-ai.com/opportunity/opp_1",
        })
        fields = mock_post.call_args[1]["json"]["embeds"][0]["fields"]
        names = {f["name"] for f in fields}
        self.assertIn("Transaction", names)
        self.assertIn("Dashboard", names)


# ---------------------------------------------------------------
# Gmail backend tests
# ---------------------------------------------------------------

class GmailTests(unittest.TestCase):
    def test_not_configured_returns_false(self):
        g = GmailAlert(address="", app_password="", recipient="")
        self.assertFalse(g.configured)
        self.assertFalse(g.send("trade_executed", "msg"))

    def test_configured_check(self):
        g = GmailAlert(address="a@g.com", app_password="pw", recipient="b@g.com")
        self.assertTrue(g.configured)

    def test_name(self):
        self.assertEqual(GmailAlert(address="", app_password="", recipient="").name, "gmail")

    @patch("alerting.gmail.smtplib.SMTP")
    def test_send_success(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        g = GmailAlert(address="a@g.com", app_password="pw", recipient="b@g.com")
        self.assertTrue(g.send("daily_summary", "report", {"scans": 100}))
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("a@g.com", "pw")
        mock_server.sendmail.assert_called_once()

    @patch("alerting.gmail.smtplib.SMTP", side_effect=Exception("connection refused"))
    def test_send_smtp_error(self, _mock_smtp_cls):
        g = GmailAlert(address="a@g.com", app_password="pw", recipient="b@g.com")
        self.assertFalse(g.send("trade_executed", "msg"))

    @patch("alerting.gmail.smtplib.SMTP")
    def test_html_links_rendered(self, mock_smtp_cls):
        captured = {}
        def _sendmail(from_addr, to_addrs, msg_str):
            captured["msg"] = msg_str
        mock_server = MagicMock()
        mock_server.sendmail.side_effect = _sendmail
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        g = GmailAlert(address="a@g.com", app_password="pw", recipient="b@g.com")
        g.send("trade_executed", "ok", {
            "tx_link": "https://solscan.io/tx/abc",
            "dashboard_link": "https://arb-trader-solana.yeda-ai.com/opportunity/opp_1",
        })
        # Default HTML body should include both links as anchor tags.
        self.assertIn('href="https://solscan.io/tx/abc"', captured["msg"])
        self.assertIn('href="https://arb-trader-solana.yeda-ai.com/opportunity/opp_1"', captured["msg"])


# ---------------------------------------------------------------
# Telegram backend tests
# ---------------------------------------------------------------

class TelegramTests(unittest.TestCase):
    def test_not_configured_returns_false(self):
        t = TelegramAlert(bot_token="", chat_id="")
        self.assertFalse(t.configured)
        self.assertFalse(t.send("trade_executed", "msg"))

    def test_configured_check(self):
        t = TelegramAlert(bot_token="123:ABC", chat_id="999")
        self.assertTrue(t.configured)

    def test_name(self):
        self.assertEqual(TelegramAlert(bot_token="", chat_id="").name, "telegram")

    @patch("alerting.telegram.requests.post")
    def test_send_success(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        t = TelegramAlert(bot_token="123:ABC", chat_id="999")
        self.assertTrue(t.send("trade_executed", "profit!"))
        mock_post.assert_called_once()
        body = mock_post.call_args[1]["json"]
        self.assertEqual(body["chat_id"], "999")
        self.assertIn("Trade Executed", body["text"])

    @patch("alerting.telegram.requests.post")
    def test_send_api_error(self, mock_post):
        mock_post.return_value = MagicMock(status_code=400, text="Bad Request")
        t = TelegramAlert(bot_token="123:ABC", chat_id="999")
        self.assertFalse(t.send("trade_executed", "msg"))

    @patch("alerting.telegram.requests.post", side_effect=Exception("timeout"))
    def test_send_network_error(self, _mock_post):
        t = TelegramAlert(bot_token="123:ABC", chat_id="999")
        self.assertFalse(t.send("trade_executed", "msg"))

    @patch("alerting.telegram.requests.post")
    def test_links_appended(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        t = TelegramAlert(bot_token="123:ABC", chat_id="999")
        t.send("trade_executed", "exec", {
            "tx_link": f"https://solscan.io/tx/{SAMPLE_SIG}",
            "dashboard_link": "https://arb-trader-solana.yeda-ai.com/opportunity/opp_1",
        })
        text = mock_post.call_args[1]["json"]["text"]
        self.assertIn("[View Transaction]", text)
        self.assertIn("[View on Dashboard]", text)


# ---------------------------------------------------------------
# URL helper tests
# ---------------------------------------------------------------

class URLHelperTests(unittest.TestCase):
    def test_tx_explorer_url_always_solscan(self):
        # chain is accepted but ignored — every tx on SolanaTrader is Solscan.
        self.assertEqual(
            tx_explorer_url("solana", SAMPLE_SIG),
            f"https://solscan.io/tx/{SAMPLE_SIG}",
        )
        self.assertEqual(
            tx_explorer_url(None, SAMPLE_SIG),
            f"https://solscan.io/tx/{SAMPLE_SIG}",
        )
        self.assertEqual(
            tx_explorer_url("arbitrum", SAMPLE_SIG),
            f"https://solscan.io/tx/{SAMPLE_SIG}",
        )

    def test_opp_dashboard_url_default(self):
        self.assertEqual(
            opp_dashboard_url("opp_abc123"),
            "https://arb-trader-solana.yeda-ai.com/opportunity/opp_abc123",
        )

    def test_opp_dashboard_url_custom(self):
        self.assertEqual(
            opp_dashboard_url("opp_xyz", "http://localhost:8000"),
            "http://localhost:8000/opportunity/opp_xyz",
        )


# ---------------------------------------------------------------
# Integration: dispatcher + real backends (unconfigured)
# ---------------------------------------------------------------

class IntegrationTests(unittest.TestCase):
    def test_unconfigured_backends_gracefully_skip(self):
        d = AlertDispatcher([
            TelegramAlert(bot_token="", chat_id=""),
            DiscordAlert(webhook_url=""),
            GmailAlert(address="", app_password="", recipient=""),
        ])
        self.assertEqual(d.alert("trade_executed", "test msg"), 0)

    @patch("alerting.telegram.requests.post")
    @patch("alerting.discord.requests.post")
    def test_mixed_configured(self, mock_discord, mock_telegram):
        mock_telegram.return_value = MagicMock(status_code=200)
        mock_discord.return_value = MagicMock(status_code=204)
        d = AlertDispatcher([
            TelegramAlert(bot_token="123:ABC", chat_id="999"),
            DiscordAlert(webhook_url="https://discord.com/api/webhooks/fake"),
            GmailAlert(address="", app_password="", recipient=""),
        ])
        self.assertEqual(d.alert("trade_executed", "profit!"), 2)


if __name__ == "__main__":
    unittest.main()
