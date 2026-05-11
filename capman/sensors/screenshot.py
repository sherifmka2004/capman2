"""Periodic + event-triggered screenshot capture via mss."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from capman.events import Event, EventType
from capman.sensors.base import BaseSensor

logger = logging.getLogger(__name__)


class ScreenshotSensor(BaseSensor):
    sensor_id: ClassVar[str] = "screenshot"
    platform_support: ClassVar[set[str]] = {"*"}
    requires_permissions: ClassVar[list[str]] = ["screen_recording"]

    async def run(self) -> None:
        cfg = self.config.get("sensors", {}).get("screenshot", {})
        interval = cfg.get("interval_seconds", 30)
        save_dir = Path(cfg.get("save_dir", "~/.capman/screenshots")).expanduser()
        max_disk_gb = cfg.get("max_disk_gb", 5.0)

        while not self._stop_event.is_set():
            try:
                path = self._capture(save_dir)
                if path:
                    await self.emit(Event(
                        type=EventType.SCREENSHOT,
                        payload={"path": str(path), "trigger": "periodic", "ocr_text": ""},
                        sensor_id=self.sensor_id,
                    ))
                    self._enforce_disk_limit(save_dir, max_disk_gb)
            except Exception as e:
                logger.warning("Screenshot capture failed: %s", e)

            await asyncio.sleep(interval)

    def _capture(self, save_dir: Path) -> Path | None:
        try:
            import mss
            import mss.tools
            now = datetime.now()
            day_dir = save_dir / now.strftime("%Y-%m-%d")
            day_dir.mkdir(parents=True, exist_ok=True)
            filename = now.strftime("%H-%M-%S") + ".png"
            path = day_dir / filename

            with mss.mss() as sct:
                monitor = sct.monitors[0]  # all monitors combined
                img = sct.grab(monitor)
                mss.tools.to_png(img.rgb, img.size, output=str(path))

            return path
        except Exception as e:
            logger.warning("mss capture failed: %s", e)
            return None

    def _enforce_disk_limit(self, save_dir: Path, max_gb: float) -> None:
        max_bytes = max_gb * 1024 ** 3
        files = sorted(save_dir.rglob("*.png"), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        while total > max_bytes and files:
            oldest = files.pop(0)
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)
