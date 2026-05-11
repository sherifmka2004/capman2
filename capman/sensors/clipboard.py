"""Clipboard monitor — detects copy/paste via polling."""
from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from capman.events import Event, EventType
from capman.sensors.base import BaseSensor

logger = logging.getLogger(__name__)


class ClipboardSensor(BaseSensor):
    sensor_id: ClassVar[str] = "clipboard"
    platform_support: ClassVar[set[str]] = {"*"}

    async def run(self) -> None:
        try:
            import pyperclip  # type: ignore
        except ImportError:
            logger.warning("pyperclip not available, clipboard sensor disabled")
            return

        cfg = self.config.get("sensors", {}).get("clipboard", {})
        poll_interval_s = cfg.get("poll_interval_ms", 500) / 1000.0

        last_content = ""
        try:
            last_content = pyperclip.paste() or ""
        except Exception:
            pass

        while not self._stop_event.is_set():
            try:
                content = pyperclip.paste() or ""
                if content and content != last_content:
                    from capman.platform.base import get_platform_adapter
                    app, title = get_platform_adapter(self.config).get_active_window()
                    await self.emit(Event(
                        type=EventType.CLIPBOARD_COPY,
                        app=app,
                        window_title=title,
                        payload={
                            "content": content,
                            "content_type": "text",
                            "char_count": len(content),
                        },
                        sensor_id=self.sensor_id,
                    ))
                    last_content = content
            except Exception as e:
                logger.debug("ClipboardSensor error: %s", e)

            await asyncio.sleep(poll_interval_s)
