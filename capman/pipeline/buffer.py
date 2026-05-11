"""
AsyncEventBuffer — thread-safe bridge between sensor threads and asyncio pipeline.
Sensors running in threads use put_sync(); the pipeline uses async get().
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from capman.events import Event

logger = logging.getLogger(__name__)


class AsyncEventBuffer:
    def __init__(self, maxsize: int = 10_000):
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)

    def put_sync(self, event: Event) -> None:
        """Thread-safe, non-blocking put. Drops event if queue is full."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Buffer full, dropping event type=%s", event.type)

    async def put(self, event: Event) -> None:
        await self._queue.put(event)

    async def get(self) -> Event:
        return await self._queue.get()

    def get_nowait(self) -> Event | None:
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()

    async def drain(self) -> list[Event]:
        """Drain all currently queued events (non-blocking)."""
        events = []
        while not self._queue.empty():
            try:
                events.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events

    async def iter_events(self, timeout: float = 0.1) -> AsyncIterator[Event]:
        """Yield events as they arrive."""
        while True:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                yield event
            except asyncio.TimeoutError:
                return
