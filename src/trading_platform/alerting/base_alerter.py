"""Base alerter — generic scheduled report sender.

Products subclass and override build_hourly_report() and
build_daily_report() to generate product-specific content.
The base class handles scheduling, sending, and error logging.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from threading import Thread
from typing import Protocol

logger = logging.getLogger(__name__)


class EmailBackend(Protocol):
    """Protocol for email sending."""
    configured: bool
    def send(self, event_type: str, message: str, details: dict | None = None,
             html_body: str | None = None) -> bool: ...


class BaseAlerter:
    """Generic hourly + daily report sender.

    Hourly: fires every email_interval_seconds (default 3600).
    Daily: fires once per day at daily_hour in the specified timezone.

    Products override:
        build_hourly_report() -> (plain_text, html_body, details_dict)
        build_daily_report()  -> (plain_text, html_body, details_dict)
    """

    def __init__(
        self,
        email: EmailBackend | None = None,
        email_interval_seconds: float = 3600.0,
        daily_hour: int = 9,
        daily_tz_offset_hours: int = -5,  # EST
        startup_delay_seconds: float = 300.0,
    ) -> None:
        self.email = email
        self.email_interval = email_interval_seconds
        self._last_email_at: float = time.time() - email_interval_seconds + startup_delay_seconds
        self._last_daily_date: str = ""
        self._daily_hour = daily_hour
        self._daily_tz = timezone(timedelta(hours=daily_tz_offset_hours))
        self._thread: Thread | None = None
        self._running = False

    # -- Override these in your product --

    def build_hourly_report(self) -> tuple[str, str, dict]:
        """Return (plain_text, html_body, details_dict) for hourly report."""
        raise NotImplementedError

    def build_daily_report(self) -> tuple[str, str, dict]:
        """Return (plain_text, html_body, details_dict) for daily report."""
        raise NotImplementedError

    # -- Sending logic --

    def send_hourly_report(self) -> None:
        """Build and send the hourly report."""
        try:
            plain, html, details = self.build_hourly_report()
        except Exception as exc:
            logger.error("Failed to build hourly report: %s", exc)
            return

        if self.email and self.email.configured:
            ok = self.email.send("hourly_summary", plain, details, html_body=html)
            if ok:
                logger.info("Hourly email report sent")
            else:
                logger.error("Hourly email report FAILED to send")
        else:
            logger.warning("Hourly report skipped — email not configured")

        self._last_email_at = time.time()

    def send_daily_report(self) -> None:
        """Build and send the daily report."""
        try:
            plain, html, details = self.build_daily_report()
        except Exception as exc:
            logger.error("Failed to build daily report: %s", exc)
            return

        if self.email and self.email.configured:
            ok = self.email.send("daily_summary", plain, details, html_body=html)
            if ok:
                logger.info("Daily email report sent")
            else:
                logger.error("Daily email report FAILED to send")
        else:
            logger.warning("Daily report skipped — email not configured")

    def maybe_send_hourly(self) -> None:
        """Check if it's time to send the hourly report."""
        if time.time() - self._last_email_at >= self.email_interval:
            self.send_hourly_report()

    def maybe_send_daily(self) -> None:
        """Check if it's time to send the daily report (at daily_hour in timezone)."""
        now = datetime.now(self._daily_tz)
        today_str = now.strftime("%Y-%m-%d")
        if now.hour >= self._daily_hour and today_str != self._last_daily_date:
            self._last_daily_date = today_str
            self.send_daily_report()

    # -- Background thread --

    def start_background(self) -> None:
        """Start background thread for hourly + daily reports."""
        if self._running:
            return
        self._running = True

        def _loop():
            while self._running:
                time.sleep(60)
                self.maybe_send_hourly()
                self.maybe_send_daily()

        self._thread = Thread(target=_loop, daemon=True)
        self._thread.start()
        logger.info("Report background thread started")

    def stop(self) -> None:
        self._running = False
