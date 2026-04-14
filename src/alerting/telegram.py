"""Telegram alert backend.

Sends messages via the Telegram Bot API. Requires:
  - TELEGRAM_BOT_TOKEN: Bot token from @BotFather
  - TELEGRAM_CHAT_ID: Chat/group ID to send to

Setup:
  1. Message @BotFather on Telegram, create a bot, get the token
  2. Start a chat with your bot, send any message
  3. GET https://api.telegram.org/bot<TOKEN>/getUpdates to find your chat_id
  4. Add to .env:
     TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
     TELEGRAM_CHAT_ID=987654321
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"

# Emoji prefixes per event type for quick visual scanning.
EVENT_EMOJI = {
    "opportunity_found": "\U0001f50d",   # magnifying glass
    "trade_executed": "\u2705",           # green check
    "trade_reverted": "\u274c",           # red X
    "trade_not_included": "\u23f3",       # hourglass
    "simulation_failed": "\u26a0\ufe0f",  # warning
    "system_error": "\U0001f6a8",         # rotating light
    "daily_summary": "\U0001f4ca",        # bar chart
}


class TelegramAlert:
    """Send alerts to a Telegram chat via Bot API."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.bot_token = bot_token if bot_token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send(self, event_type: str, message: str, details: dict | None = None) -> bool:
        if not self.configured:
            logger.debug("Telegram not configured — skipping alert")
            return False

        emoji = EVENT_EMOJI.get(event_type, "\U0001f514")  # bell default
        text = f"{emoji} *{event_type.replace('_', ' ').title()}*\n\n{message}"

        try:
            resp = requests.post(
                f"{TELEGRAM_API}/bot{self.bot_token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                return True
            logger.warning("Telegram API returned %d: %s", resp.status_code, resp.text)
            return False
        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)
            return False
