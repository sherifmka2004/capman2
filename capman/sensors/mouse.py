"""Mouse click sensor — tracks clicks and app focus changes via mouse."""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import ClassVar

from capman.events import Event, EventType
from capman.sensors.base import BaseSensor

logger = logging.getLogger(__name__)


class MouseSensor(BaseSensor):
    sensor_id: ClassVar[str] = "mouse"
    platform_support: ClassVar[set[str]] = {"*"}
    requires_permissions: ClassVar[list[str]] = ["accessibility"]

    async def run(self) -> None:
        cfg = self.config.get("sensors", {}).get("mouse", {})
        self._track_clicks = cfg.get("track_clicks", True)
        self._loop = asyncio.get_event_loop()

        listener_thread = threading.Thread(target=self._start_listener, daemon=True)
        listener_thread.start()

        while not self._stop_event.is_set():
            await asyncio.sleep(1.0)

        if hasattr(self, "_listener"):
            self._listener.stop()

    def _start_listener(self) -> None:
        try:
            from pynput import mouse  # type: ignore
            self._listener = mouse.Listener(on_click=self._on_click)
            self._listener.start()
            self._listener.join()
        except Exception as e:
            logger.warning("Mouse listener failed: %s", e)

    def _on_click(self, x, y, button, pressed) -> None:
        if not pressed or not self._track_clicks:
            return
        try:
            from capman.platform.base import get_platform_adapter
            app, title = get_platform_adapter(self.config).get_active_window()
            event = Event(
                type=EventType.MOUSE_CLICK,
                app=app,
                window_title=title,
                payload={"button": str(button), "x": x, "y": y},
                sensor_id=self.sensor_id,
            )
            self.emit_sync(event)
        except Exception as e:
            logger.debug("MouseSensor._on_click error: %s", e)
