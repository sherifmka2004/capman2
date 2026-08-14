"""Periodic + event-triggered screenshot capture via mss."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
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

        loop = asyncio.get_event_loop()

        while not self._stop_event.is_set():
            try:
                # Grab + encode is 100-500ms of blocking work, and the disk-limit
                # sweep stats every file under save_dir. Both would otherwise stall
                # the event loop that also serves the API and the pipeline.
                path = await loop.run_in_executor(None, self._capture, save_dir, cfg)
                if path:
                    await self.emit(Event(
                        type=EventType.SCREENSHOT,
                        payload={"path": str(path), "trigger": "periodic", "ocr_text": ""},
                        sensor_id=self.sensor_id,
                    ))
                    await loop.run_in_executor(
                        None, self._enforce_disk_limit, save_dir, max_disk_gb, cfg
                    )
            except Exception as e:
                logger.warning("Screenshot capture failed: %s", e)

            await asyncio.sleep(interval)

    def _capture(self, save_dir: Path, cfg: dict) -> Path | None:
        try:
            import mss
            from PIL import Image
            import io

            fmt = cfg.get("format", "png").lower()
            quality = int(cfg.get("quality", 80))
            scale = float(cfg.get("scale", 1.0))

            ext_map = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}
            ext = ext_map.get(fmt, ".png")

            now = datetime.now()
            day_dir = save_dir / now.strftime("%Y-%m-%d")
            day_dir.mkdir(parents=True, exist_ok=True)
            filename = now.strftime("%H-%M-%S") + ext
            path = day_dir / filename

            with mss.mss() as sct:
                monitor = sct.monitors[0]
                img = sct.grab(monitor)
                pil_img = Image.frombytes("RGB", img.size, img.rgb)

            if scale != 1.0:
                new_size = (int(pil_img.width * scale), int(pil_img.height * scale))
                pil_img = pil_img.resize(new_size, Image.LANCZOS)

            save_kwargs: dict = {}
            if fmt == "jpeg":
                save_kwargs = {"quality": quality, "optimize": True}
            elif fmt == "webp":
                save_kwargs = {"quality": quality, "method": 4}
            else:  # png
                save_kwargs = {"optimize": True, "compress_level": 6}

            pil_img.save(str(path), format=fmt.upper() if fmt != "jpeg" else "JPEG", **save_kwargs)
            return path
        except Exception as e:
            logger.warning("Screenshot capture failed: %s", e)
            return None

    def _enforce_disk_limit(self, save_dir: Path, max_gb: float, cfg: dict) -> None:
        fmt = cfg.get("format", "png").lower()
        ext = {"png": "*.png", "jpeg": "*.jpg", "webp": "*.webp"}.get(fmt, "*.png")

        retain_days = int(cfg.get("retain_days", 0))
        if retain_days > 0:
            cutoff = datetime.now() - timedelta(days=retain_days)
            for p in save_dir.rglob(ext):
                try:
                    if datetime.fromtimestamp(p.stat().st_mtime) < cutoff:
                        p.unlink(missing_ok=True)
                except Exception:
                    pass

        max_bytes = max_gb * 1024 ** 3
        files = sorted(save_dir.rglob(ext), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        while total > max_bytes and files:
            oldest = files.pop(0)
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)
