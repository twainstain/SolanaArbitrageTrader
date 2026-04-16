"""Alert dispatcher — fan-out to multiple notification backends.

Generic pattern: register backends (email, Telegram, Discord, etc.),
then call alert() to send to all configured backends.
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class AlertBackend(Protocol):
    """Protocol for a notification backend."""
    name: str
    configured: bool
    def send(self, event_type: str, message: str, details: dict | None = None) -> bool: ...


class AlertDispatcher:
    """Fan-out alerts to multiple backends."""

    def __init__(self) -> None:
        self._backends: list[AlertBackend] = []

    def add_backend(self, backend: AlertBackend) -> None:
        if backend.configured:
            self._backends.append(backend)

    @property
    def backend_count(self) -> int:
        return len(self._backends)

    def alert(self, event_type: str, message: str, details: dict | None = None) -> None:
        """Send an alert to all configured backends."""
        for backend in self._backends:
            try:
                backend.send(event_type, message, details)
            except Exception as exc:
                logger.error("Alert backend '%s' failed: %s", backend.name, exc)
