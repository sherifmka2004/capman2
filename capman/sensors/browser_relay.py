"""
Browser relay sensor — receives events POSTed by the browser extension
via the local FastAPI server. Events are injected into the pipeline queue.
This sensor doesn't run its own loop; it registers a queue reference that
the API server uses to inject events.
"""
from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from capman.events import Event, EventType
from capman.sensors.base import BaseSensor

logger = logging.getLogger(__name__)

# Module-level queue reference — set by the orchestrator, read by the API route
_relay_queue: asyncio.Queue | None = None


def set_relay_queue(queue: asyncio.Queue) -> None:
    global _relay_queue
    _relay_queue = queue


def get_relay_queue() -> asyncio.Queue | None:
    return _relay_queue


class BrowserRelaySensor(BaseSensor):
    sensor_id: ClassVar[str] = "browser_relay"
    platform_support: ClassVar[set[str]] = {"*"}

    async def run(self) -> None:
        set_relay_queue(self._queue)
        logger.info("Browser relay sensor active — waiting for extension events on port %s",
                    self.config.get("api", {}).get("port", 7331))
        while not self._stop_event.is_set():
            await asyncio.sleep(5.0)

    async def teardown(self) -> None:
        set_relay_queue(None)
