"""
BaseSensor ABC — the plugin interface every capture sensor must implement.

To add a new sensor:
1. Create capman/sensors/my_sensor.py
2. Subclass BaseSensor
3. Set sensor_id (unique str), platform_support (set of sys.platform values or {"*"})
4. Implement run() — the main async loop
5. Place file in capman/sensors/ — SensorRegistry auto-discovers it via pkgutil
No other changes needed anywhere.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import ClassVar

from capman.events import Event

logger = logging.getLogger(__name__)


class BaseSensor(ABC):
    sensor_id: ClassVar[str]
    platform_support: ClassVar[set[str]]   # {"darwin","linux","win32"} or {"*"}
    requires_permissions: ClassVar[list[str]] = []

    def __init__(self, config: dict, queue: asyncio.Queue):
        self.config = config
        self._queue = queue
        self._stop_event = asyncio.Event()

    async def emit(self, event: Event) -> None:
        """Non-blocking emit. Drops event (with warning) if queue is full."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full, dropping event type=%s", event.type)

    def emit_sync(self, event: Event) -> None:
        """Thread-safe emit for use from non-async contexts (e.g. pynput callbacks)."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.call_soon_threadsafe(self._queue.put_nowait, event)
            else:
                self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full, dropping event type=%s (sync)", event.type)

    def stop(self) -> None:
        """Signal this sensor to shut down cleanly."""
        self._stop_event.set()

    @abstractmethod
    async def run(self) -> None:
        """
        Main sensor loop. Implementation must:
        - Regularly check self._stop_event.is_set()
        - Call await self.emit(event) for each captured event
        - Handle its own exceptions (log, don't crash)
        - Clean up resources before returning
        """

    async def setup(self) -> None:
        """Optional one-time async init (open handles, request permissions)."""

    async def teardown(self) -> None:
        """Optional cleanup after stop() is called."""
