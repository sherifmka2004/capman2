"""
IdleSensor — emits IDLE_START / IDLE_END based on real input activity.

Heuristic ported from ActivityWatch (`aw-watcher-afk`):
  - Poll `activity_context.time_since_last_input()` every `poll_interval_s`.
  - When the gap crosses `idle_threshold_s` and we are not already idle, emit
    IDLE_START.  When activity resumes, emit IDLE_END with the precise idle
    duration.
  - "Input" means key press / mouse click / scroll — *not* mouse moves
    (the move-heatmap path deliberately does not bump the activity timer
    so passive cursor jitter doesn't keep the user "active").

The emitted IDLE_START is treated as a hard session-break by `SessionDetector`,
so a 20 min lunch doesn't get glued to the morning's debugging session.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import ClassVar

from capman.events import Event, EventType
from capman.sensors.activity_context import last_input_ts, time_since_last_input
from capman.sensors.base import BaseSensor

logger = logging.getLogger(__name__)


class IdleSensor(BaseSensor):
    sensor_id: ClassVar[str] = "idle"
    platform_support: ClassVar[set[str]] = {"*"}

    async def run(self) -> None:
        cfg = self.config.get("sensors", {}).get("idle", {})
        if not cfg.get("enabled", True):
            logger.info("IdleSensor disabled via config")
            return
        # Floors are deliberately small — production configs sit at 180/5;
        # tests dial them down to ~0.2s.
        threshold_s = max(0.05, float(cfg.get("idle_threshold_s", 180)))
        poll_s = max(0.01, float(cfg.get("poll_interval_s", 5)))

        is_idle = False
        idle_started_at = 0.0

        while not self._stop_event.is_set():
            try:
                gap = time_since_last_input()
                # Never seen any input yet (gap == +inf) → don't trip on first launch.
                if gap == float("inf"):
                    await asyncio.sleep(poll_s)
                    continue

                if not is_idle and gap >= threshold_s:
                    # Idle started `gap` seconds ago, not now.
                    idle_started_at = time.time() - gap
                    is_idle = True
                    await self.emit(Event(
                        type=EventType.IDLE_START,
                        payload={
                            "last_input_ts": last_input_ts(),
                        },
                        sensor_id=self.sensor_id,
                    ))
                    logger.debug("IdleSensor: IDLE_START (gap=%.1fs)", gap)
                elif is_idle and gap < threshold_s:
                    # User came back — `last_input_ts` is the resume moment.
                    duration = max(0.0, last_input_ts() - idle_started_at)
                    await self.emit(Event(
                        type=EventType.IDLE_END,
                        payload={
                            "idle_started_at": idle_started_at,
                            "idle_duration_s": round(duration, 1),
                        },
                        sensor_id=self.sensor_id,
                    ))
                    is_idle = False
                    idle_started_at = 0.0
                    logger.debug("IdleSensor: IDLE_END (duration=%.1fs)", duration)
            except Exception as e:
                logger.debug("IdleSensor poll error: %s", e)

            await asyncio.sleep(poll_s)
