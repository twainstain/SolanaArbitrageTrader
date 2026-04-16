"""Discord alert backend.

Sends messages via Discord Webhook. Requires:
  - DISCORD_WEBHOOK_URL: Webhook URL from Discord channel settings

Setup:
  1. In Discord, go to Channel Settings → Integrations → Webhooks
  2. Create a webhook, copy the URL
  3. Add to .env:
     DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

# Color codes for Discord embeds per event type.
EVENT_COLORS = {
    "opportunity_found": 0x3498DB,   # blue
    "trade_executed": 0x2ECC71,      # green
    "trade_reverted": 0xE74C3C,      # red
    "trade_not_included": 0xF39C12,  # orange
    "simulation_failed": 0xE67E22,   # dark orange
    "system_error": 0xE74C3C,        # red
    "daily_summary": 0x9B59B6,       # purple
}


class DiscordAlert:
    """Send alerts to a Discord channel via webhook."""

    def __init__(
        self,
        webhook_url: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.webhook_url = webhook_url if webhook_url is not None else os.environ.get("DISCORD_WEBHOOK_URL", "")
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "discord"

    @property
    def configured(self) -> bool:
        return bool(self.webhook_url)

    # Only send these event types to Discord. Other events (opportunity_found,
    # simulation_failed, etc.) create too much noise. Only successful executions
    # and critical errors warrant a Discord notification.
    ALLOWED_EVENTS = frozenset({"trade_executed", "trade_reverted", "system_error", "daily_summary"})

    def send(self, event_type: str, message: str, details: dict | None = None) -> bool:
        if not self.configured:
            logger.debug("Discord not configured — skipping alert")
            return False
        if event_type not in self.ALLOWED_EVENTS:
            logger.debug("Discord: skipping event_type=%s (not in allowed list)", event_type)
            return True  # Return True to avoid "failure" warnings in dispatcher

        color = EVENT_COLORS.get(event_type, 0x95A5A6)
        title = event_type.replace("_", " ").title()

        # Build embed fields from details dict.
        fields = []
        if details:
            for key, val in details.items():
                fields.append({
                    "name": key.replace("_", " ").title(),
                    "value": str(val),
                    "inline": True,
                })

        payload = {
            "embeds": [
                {
                    "title": title,
                    "description": message,
                    "color": color,
                    "fields": fields[:25],  # Discord max 25 fields
                }
            ],
        }

        try:
            resp = requests.post(
                self.webhook_url,
                json=payload,
                timeout=self.timeout,
            )
            # Discord returns 204 on success.
            if resp.status_code in (200, 204):
                return True
            logger.warning("Discord webhook returned %d: %s", resp.status_code, resp.text)
            return False
        except Exception as exc:
            logger.error("Discord send failed: %s", exc)
            return False
