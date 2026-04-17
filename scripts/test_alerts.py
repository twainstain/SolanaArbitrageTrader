"""Send a test alert to all configured backends (Discord, Gmail, Telegram).

Usage:
    PYTHONPATH=src python scripts/test_alerts.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.env import load_env

load_env()

from alerting.telegram import TelegramAlert
from alerting.discord import DiscordAlert
from alerting.gmail import GmailAlert
from alerting.dispatcher import AlertDispatcher


def main():
    telegram = TelegramAlert()
    discord = DiscordAlert()
    gmail = GmailAlert()

    dispatcher = AlertDispatcher()
    backends_found = 0

    if telegram.configured:
        dispatcher.add_backend(telegram)
        backends_found += 1
        print("[OK] Telegram configured")
    else:
        print("[--] Telegram not configured (skipping)")

    if discord.configured:
        dispatcher.add_backend(discord)
        backends_found += 1
        print("[OK] Discord configured")
    else:
        print("[--] Discord not configured (skipping)")

    if gmail.configured:
        dispatcher.add_backend(gmail)
        backends_found += 1
        print("[OK] Gmail configured")
    else:
        print("[--] Gmail not configured (skipping)")

    if backends_found == 0:
        print("\nNo backends configured. Check your .env file.")
        sys.exit(1)

    print(f"\nSending test alert to {backends_found} backend(s)...")

    count = dispatcher.alert(
        "system_error",
        "Test Alert — SolanaTrader\n"
        "If you see this, alerting is working correctly.\n"
        "This is a test message sent before deployment.",
        {
            "type": "test",
            "status": "success",
            "message": "All systems operational",
        },
    )

    print(f"\nDelivered to {count}/{backends_found} backend(s)")

    if count == backends_found:
        print("All alerts sent successfully!")
    elif count > 0:
        print("Some alerts failed — check the logs above.")
    else:
        print("All alerts failed — check your credentials in .env")
        sys.exit(1)


if __name__ == "__main__":
    main()
