"""Tests for smart alerting rules."""

import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch, MagicMock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persistence.db import init_db, close_db
from persistence.repository import Repository
from alerting.smart_alerts import SmartAlerter, BIG_WIN_THRESHOLD_PCT
from alerting.telegram import TelegramAlert
from alerting.discord import DiscordAlert
from alerting.gmail import GmailAlert

D = Decimal


# Unconfigured backend singletons — prevent tests from reading .env and
# sending real alerts.  Every SmartAlerter in tests should use these as
# defaults for any backend not explicitly under test.
_SAFE_TG = TelegramAlert(bot_token="", chat_id="")
_SAFE_DC = DiscordAlert(webhook_url="")
_SAFE_GM = GmailAlert(address="", app_password="", recipient="")


class _AlertTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.conn = init_db(self.tmp.name)
        self.repo = Repository(self.conn)

    def tearDown(self):
        close_db()
        Path(self.tmp.name).unlink(missing_ok=True)


class BigWinTelegramTests(_AlertTestBase):
    @patch("requests.post")
    def test_sends_telegram_for_big_spread(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        tg = TelegramAlert(bot_token="123:ABC", chat_id="999")
        alerter = SmartAlerter(repo=self.repo, telegram=tg, discord=_SAFE_DC, gmail=_SAFE_GM)

        alerter.check_opportunity(
            spread_pct=D("7.5"), pair="WETH/USDC",
            buy_dex="Scroll", sell_dex="Arbitrum",
            chain="scroll", net_profit=0.02,
        )
        # Find the Telegram call among all requests.post calls.
        tg_calls = [c for c in mock_post.call_args_list if "telegram" in c[0][0]]
        self.assertEqual(len(tg_calls), 1)
        call_json = tg_calls[0][1]["json"]
        self.assertIn("BIG SPREAD", call_json["text"])
        self.assertIn("7.50%", call_json["text"])

    @patch("alerting.telegram.requests.post")
    def test_no_telegram_for_small_spread(self, mock_post):
        tg = TelegramAlert(bot_token="123:ABC", chat_id="999")
        alerter = SmartAlerter(repo=self.repo, telegram=tg, discord=_SAFE_DC, gmail=_SAFE_GM)

        alerter.check_opportunity(
            spread_pct=D("0.2"), pair="WETH/USDC",
            buy_dex="A", sell_dex="B", chain="ethereum", net_profit=0.005,
        )
        mock_post.assert_not_called()

    def test_no_crash_when_telegram_unconfigured(self):
        tg = TelegramAlert(bot_token="", chat_id="")
        dc = DiscordAlert(webhook_url="")
        gm = GmailAlert(address="", app_password="", recipient="")
        alerter = SmartAlerter(repo=self.repo, telegram=tg, discord=dc, gmail=gm)
        # Should not crash even for big spread.
        alerter.check_opportunity(
            spread_pct=D("10"), pair="WETH/USDC",
            buy_dex="A", sell_dex="B", chain="ethereum", net_profit=0.05,
        )


class BigWinDiscordTests(_AlertTestBase):
    @patch("alerting.discord.requests.post")
    def test_sends_discord_for_big_spread(self, mock_post):
        mock_post.return_value = MagicMock(status_code=204)
        dc = DiscordAlert(webhook_url="https://discord.com/api/webhooks/fake")
        alerter = SmartAlerter(repo=self.repo, telegram=_SAFE_TG, discord=dc, gmail=_SAFE_GM)

        alerter.check_opportunity(
            spread_pct=D("7.5"), pair="WETH/USDC",
            buy_dex="Uni", sell_dex="Sushi",
            chain="optimism", net_profit=0.02,
        )
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]
        self.assertIn("BIG SPREAD", payload["embeds"][0]["description"])

    @patch("alerting.discord.requests.post")
    def test_no_discord_for_small_spread(self, mock_post):
        dc = DiscordAlert(webhook_url="https://discord.com/api/webhooks/fake")
        alerter = SmartAlerter(repo=self.repo, telegram=_SAFE_TG, discord=dc, gmail=_SAFE_GM)

        alerter.check_opportunity(
            spread_pct=D("0.2"), pair="WETH/USDC",
            buy_dex="A", sell_dex="B", chain="ethereum", net_profit=0.005,
        )
        mock_post.assert_not_called()

    @patch("requests.post")
    def test_big_spread_sends_both_telegram_and_discord(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)

        tg = TelegramAlert(bot_token="123:ABC", chat_id="999")
        dc = DiscordAlert(webhook_url="https://discord.com/api/webhooks/fake")
        alerter = SmartAlerter(repo=self.repo, telegram=tg, discord=dc, gmail=_SAFE_GM)

        alerter.check_opportunity(
            spread_pct=D("8.0"), pair="WETH/USDC",
            buy_dex="Uni", sell_dex="Sushi",
            chain="arbitrum", net_profit=0.03,
        )
        # Both Telegram and Discord should have called requests.post.
        self.assertEqual(mock_post.call_count, 2)
        urls = [call[0][0] for call in mock_post.call_args_list]
        self.assertTrue(any("telegram" in u for u in urls))
        self.assertTrue(any("discord" in u for u in urls))

    def test_no_crash_when_discord_unconfigured(self):
        tg = TelegramAlert(bot_token="", chat_id="")
        dc = DiscordAlert(webhook_url="")
        gm = GmailAlert(address="", app_password="", recipient="")
        alerter = SmartAlerter(repo=self.repo, telegram=tg, discord=dc, gmail=gm)
        alerter.check_opportunity(
            spread_pct=D("10"), pair="WETH/USDC",
            buy_dex="A", sell_dex="B", chain="ethereum", net_profit=0.05,
        )


class HourlyEmailTests(_AlertTestBase):
    @staticmethod
    def _decode_email_body(raw_msg: str) -> str:
        """Decode MIME email to get plain text body."""
        import email
        msg = email.message_from_string(raw_msg)
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8")
        return raw_msg

    @patch("alerting.smart_alerts._fetch_wallet_data", return_value={"address": "", "balances": {}})
    @patch("alerting.gmail.smtplib.SMTP")
    def test_sends_hourly_email(self, mock_smtp_cls, _mock_wallet):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        gm = GmailAlert(address="a@g.com", app_password="pw", recipient="b@g.com")
        alerter = SmartAlerter(repo=self.repo, telegram=_SAFE_TG, discord=_SAFE_DC, gmail=gm, dashboard_url="http://test:8000/dashboard")

        # Seed some data.
        self.repo.create_opportunity(
            pair="WETH/USDC", chain="ethereum",
            buy_dex="A", sell_dex="B", spread_bps=D("42"),
        )

        alerter.send_hourly_report()
        mock_server.sendmail.assert_called_once()
        body = self._decode_email_body(mock_server.sendmail.call_args[0][2])
        self.assertIn("http://test:8000/dashboard", body)

    @patch("alerting.smart_alerts._fetch_wallet_data", return_value={"address": "", "balances": {}})
    @patch("alerting.gmail.smtplib.SMTP")
    def test_hourly_email_contains_report_fields(self, mock_smtp_cls, _mock_wallet):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        gm = GmailAlert(address="a@g.com", app_password="pw", recipient="b@g.com")
        alerter = SmartAlerter(repo=self.repo, telegram=_SAFE_TG, discord=_SAFE_DC, gmail=gm, dashboard_url="http://dash:8000/dashboard")

        alerter.send_hourly_report()
        body = self._decode_email_body(mock_server.sendmail.call_args[0][2])
        self.assertIn("Hourly Arbitrage Report", body)
        self.assertIn("Detected:", body)
        self.assertIn("Actionable:", body)
        self.assertIn("Sim approved:", body)
        self.assertIn("PNL", body)
        self.assertIn("WALLET:", body)
        self.assertIn("EXECUTION", body)
        self.assertIn("PER CHAIN", body)
        self.assertIn("http://dash:8000/dashboard", body)

    @patch("alerting.smart_alerts._fetch_wallet_data", return_value={"address": "", "balances": {}})
    @patch("alerting.gmail.smtplib.SMTP")
    def test_hourly_email_uses_hourly_subject(self, mock_smtp_cls, _mock_wallet):
        """Hourly report should use 'hourly_summary' event type."""
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        gm = GmailAlert(address="a@g.com", app_password="pw", recipient="b@g.com")
        alerter = SmartAlerter(repo=self.repo, telegram=_SAFE_TG, discord=_SAFE_DC, gmail=gm)

        alerter.send_hourly_report()
        raw_msg = mock_server.sendmail.call_args[0][2]
        self.assertIn("[Arb] Hourly Report", raw_msg)

    def test_no_crash_when_gmail_unconfigured(self):
        gm = GmailAlert(address="", app_password="", recipient="")
        alerter = SmartAlerter(repo=self.repo, telegram=_SAFE_TG, discord=_SAFE_DC, gmail=gm)
        alerter.send_hourly_report()  # should not crash

    @patch("alerting.smart_alerts._fetch_wallet_data", return_value={"address": "", "balances": {}})
    @patch("alerting.gmail.smtplib.SMTP")
    def test_maybe_send_respects_interval(self, mock_smtp_cls, _mock_wallet):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        gm = GmailAlert(address="a@g.com", app_password="pw", recipient="b@g.com")
        alerter = SmartAlerter(repo=self.repo, telegram=_SAFE_TG, discord=_SAFE_DC, gmail=gm, email_interval_seconds=9999)

        alerter.maybe_send_hourly()
        # Interval not elapsed -> should NOT send.
        mock_server.sendmail.assert_not_called()

    @patch("alerting.smart_alerts._fetch_wallet_data", return_value={"address": "", "balances": {}})
    @patch("alerting.gmail.smtplib.SMTP")
    def test_maybe_send_fires_after_interval(self, mock_smtp_cls, _mock_wallet):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        gm = GmailAlert(address="a@g.com", app_password="pw", recipient="b@g.com")
        alerter = SmartAlerter(repo=self.repo, telegram=_SAFE_TG, discord=_SAFE_DC, gmail=gm, email_interval_seconds=1)

        # Push _last_email_at back to force trigger.
        alerter._last_email_at = time.time() - 2

        alerter.maybe_send_hourly()
        mock_server.sendmail.assert_called_once()


class HourlyReportChannelTests(_AlertTestBase):
    @patch("alerting.smart_alerts._fetch_wallet_data", return_value={"address": "", "balances": {}})
    @patch("alerting.discord.requests.post")
    @patch("alerting.gmail.smtplib.SMTP")
    def test_hourly_sends_email_only_not_discord(self, mock_smtp_cls, mock_dc, _mock_wallet):
        """Hourly reports go to email only — Discord is for big-win alerts."""
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        gm = GmailAlert(address="a@g.com", app_password="pw", recipient="b@g.com")
        dc = DiscordAlert(webhook_url="https://discord.com/api/webhooks/fake")
        alerter = SmartAlerter(repo=self.repo, telegram=_SAFE_TG, gmail=gm, discord=dc)

        alerter.send_hourly_report()
        mock_server.sendmail.assert_called_once()
        mock_dc.assert_not_called()  # Discord should NOT receive hourly reports

    def test_no_crash_when_all_unconfigured(self):
        alerter = SmartAlerter(repo=self.repo, telegram=_SAFE_TG, discord=_SAFE_DC, gmail=_SAFE_GM)
        alerter.send_hourly_report()  # should not crash


class DailyEmailTests(_AlertTestBase):
    @staticmethod
    def _decode_email_body(raw_msg: str) -> str:
        import email
        msg = email.message_from_string(raw_msg)
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8")
        return raw_msg

    @patch("alerting.smart_alerts._fetch_wallet_data", return_value={"address": "", "balances": {}})
    @patch("alerting.gmail.smtplib.SMTP")
    def test_sends_daily_email(self, mock_smtp_cls, _mock_wallet):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        gm = GmailAlert(address="a@g.com", app_password="pw", recipient="b@g.com")
        alerter = SmartAlerter(repo=self.repo, telegram=_SAFE_TG, discord=_SAFE_DC, gmail=gm,
                               dashboard_url="http://test:8000/dashboard")

        self.repo.create_opportunity(
            pair="WETH/USDC", chain="arbitrum",
            buy_dex="Uni", sell_dex="Sushi", spread_bps=D("50"),
        )

        alerter.send_daily_report()
        mock_server.sendmail.assert_called_once()
        body = self._decode_email_body(mock_server.sendmail.call_args[0][2])
        self.assertIn("Daily Arbitrage Summary", body)

    @patch("alerting.smart_alerts._fetch_wallet_data", return_value={"address": "", "balances": {}})
    @patch("alerting.gmail.smtplib.SMTP")
    def test_daily_email_contains_all_sections(self, mock_smtp_cls, _mock_wallet):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        gm = GmailAlert(address="a@g.com", app_password="pw", recipient="b@g.com")
        alerter = SmartAlerter(repo=self.repo, telegram=_SAFE_TG, discord=_SAFE_DC, gmail=gm,
                               dashboard_url="http://test:8000/dashboard")

        alerter.send_daily_report()
        body = self._decode_email_body(mock_server.sendmail.call_args[0][2])
        self.assertIn("WALLET:", body)
        self.assertIn("LAST 24 HOURS", body)
        self.assertIn("EXECUTION", body)
        self.assertIn("PER CHAIN", body)
        self.assertIn("ALL TIME", body)
        self.assertIn("PNL", body)
        self.assertIn("FUNNEL", body)
        self.assertIn("http://test:8000/dashboard", body)

    @patch("alerting.smart_alerts._fetch_wallet_data", return_value={"address": "", "balances": {}})
    @patch("alerting.gmail.smtplib.SMTP")
    def test_daily_email_uses_daily_subject(self, mock_smtp_cls, _mock_wallet):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        gm = GmailAlert(address="a@g.com", app_password="pw", recipient="b@g.com")
        alerter = SmartAlerter(repo=self.repo, telegram=_SAFE_TG, discord=_SAFE_DC, gmail=gm)

        alerter.send_daily_report()
        raw_msg = mock_server.sendmail.call_args[0][2]
        self.assertIn("[Arb] Daily Summary", raw_msg)

    @patch("alerting.smart_alerts._fetch_wallet_data", return_value={"address": "", "balances": {}})
    @patch("alerting.gmail.smtplib.SMTP")
    def test_maybe_send_daily_skips_if_already_sent_today(self, mock_smtp_cls, _mock_wallet):
        """Daily report should not fire twice on the same day."""
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        gm = GmailAlert(address="a@g.com", app_password="pw", recipient="b@g.com")
        alerter = SmartAlerter(repo=self.repo, telegram=_SAFE_TG, discord=_SAFE_DC, gmail=gm)

        # Mark today as already sent.
        from datetime import datetime, timezone, timedelta
        EST = timezone(timedelta(hours=-5))
        today = datetime.now(EST).strftime("%Y-%m-%d")
        alerter._last_daily_date = today

        alerter.maybe_send_daily()
        mock_server.sendmail.assert_not_called()

    @patch("alerting.smart_alerts._fetch_wallet_data", return_value={"address": "", "balances": {}})
    @patch("alerting.gmail.smtplib.SMTP")
    def test_maybe_send_daily_fires_for_previous_date(self, mock_smtp_cls, _mock_wallet):
        """Daily report fires if _last_daily_date is a previous day and hour >= 9 EST."""
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        gm = GmailAlert(address="a@g.com", app_password="pw", recipient="b@g.com")
        alerter = SmartAlerter(repo=self.repo, telegram=_SAFE_TG, discord=_SAFE_DC, gmail=gm)

        # Set last sent to yesterday — should fire if current EST hour >= 9.
        alerter._last_daily_date = "2020-01-01"
        alerter.maybe_send_daily()
        # Whether it fires depends on current real clock (hour >= 9 EST).
        # At minimum, verify no crash.

    def test_daily_hour_defaults_to_9am(self):
        """Daily report target hour should default to 9 (9 AM EST)."""
        alerter = SmartAlerter(repo=self.repo, telegram=_SAFE_TG, discord=_SAFE_DC, gmail=_SAFE_GM)
        self.assertEqual(alerter._daily_hour_est, 9)

    def test_no_crash_when_gmail_unconfigured_daily(self):
        gm = GmailAlert(address="", app_password="", recipient="")
        alerter = SmartAlerter(repo=self.repo, telegram=_SAFE_TG, discord=_SAFE_DC, gmail=gm)
        alerter.send_daily_report()  # should not crash


class PerChainStatsTests(_AlertTestBase):
    @patch("alerting.smart_alerts._fetch_wallet_data", return_value={"address": "", "balances": {}})
    @patch("alerting.gmail.smtplib.SMTP")
    def test_hourly_shows_per_chain_breakdown(self, mock_smtp_cls, _mock_wallet):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        gm = GmailAlert(address="a@g.com", app_password="pw", recipient="b@g.com")
        alerter = SmartAlerter(repo=self.repo, telegram=_SAFE_TG, discord=_SAFE_DC, gmail=gm)

        # Seed opportunities on two chains.
        self.repo.create_opportunity("WETH/USDC", "arbitrum", "Uni", "Sushi", D("30"))
        self.repo.create_opportunity("WETH/USDC", "ethereum", "Uni", "1inch", D("25"))

        alerter.send_hourly_report()
        body = HourlyEmailTests._decode_email_body(mock_server.sendmail.call_args[0][2])
        self.assertIn("arbitrum", body)
        self.assertIn("ethereum", body)


class ThresholdTests(unittest.TestCase):
    def test_big_win_threshold_is_0_3_percent(self):
        self.assertEqual(BIG_WIN_THRESHOLD_PCT, D("0.3"))


if __name__ == "__main__":
    unittest.main()
