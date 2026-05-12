"""Active window + app focus tracker."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import ClassVar

from capman.events import Event, EventType
from capman.sensors.activity_context import set_foreground
from capman.sensors.base import BaseSensor

logger = logging.getLogger(__name__)


class WindowSensor(BaseSensor):
    sensor_id: ClassVar[str] = "window"
    platform_support: ClassVar[set[str]] = {"*"}

    async def run(self) -> None:
        from capman.platform.base import get_platform_adapter
        adapter = get_platform_adapter(self.config)
        poll_interval = self.config.get("sensors", {}).get("window", {}).get("poll_interval_s", 1.0)

        current_app = ""
        current_title = ""
        focus_start = time.time()

        while not self._stop_event.is_set():
            try:
                app, title = adapter.get_active_window()
                now = time.time()

                if app != current_app or title != current_title:
                    if current_app:
                        await self.emit(Event(
                            type=EventType.WINDOW_BLUR,
                            app=current_app,
                            window_title=current_title,
                            payload={"duration_s": round(now - focus_start, 2)},
                            sensor_id=self.sensor_id,
                        ))
                    current_app = app
                    current_title = title
                    focus_start = now
                    # Publish for file-event attribution (FilesystemSensor reads this)
                    set_foreground(app, title)
                    if app:
                        await self.emit(Event(
                            type=EventType.WINDOW_FOCUS,
                            app=app,
                            window_title=title,
                            payload={},
                            sensor_id=self.sensor_id,
                        ))
            except Exception as e:
                logger.debug("WindowSensor error: %s", e)

            await asyncio.sleep(poll_interval)
