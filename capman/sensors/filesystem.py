"""Filesystem sensor using watchdog — detects file open/save/close events."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import ClassVar

from capman.events import Event, EventType
from capman.sensors.base import BaseSensor

logger = logging.getLogger(__name__)


class FilesystemSensor(BaseSensor):
    sensor_id: ClassVar[str] = "filesystem"
    platform_support: ClassVar[set[str]] = {"*"}

    async def run(self) -> None:
        try:
            from watchdog.observers import Observer  # type: ignore
            from watchdog.events import FileSystemEventHandler  # type: ignore
        except ImportError:
            logger.warning("watchdog not available, filesystem sensor disabled")
            return

        cfg = self.config.get("sensors", {}).get("filesystem", {})
        watch_paths = cfg.get("watch_paths", [])
        allowed_extensions = set(cfg.get("extensions", []))

        sensor = self
        loop = asyncio.get_event_loop()

        class Handler(FileSystemEventHandler):
            def on_modified(self, ev):
                if ev.is_directory:
                    return
                self._emit(ev.src_path, EventType.FILE_SAVE)

            def on_created(self, ev):
                if ev.is_directory:
                    return
                self._emit(ev.src_path, EventType.FILE_OPEN)

            def _emit(self, path: str, etype: EventType):
                p = Path(path)
                if allowed_extensions and p.suffix not in allowed_extensions:
                    return
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                event = Event(
                    type=etype,
                    payload={"path": path, "extension": p.suffix, "size_bytes": size},
                    sensor_id=sensor.sensor_id,
                )
                loop.call_soon_threadsafe(
                    asyncio.ensure_future,
                    sensor.emit(event),
                )

        observer = Observer()
        handler = Handler()
        for watch_path in watch_paths:
            expanded = str(Path(watch_path).expanduser())
            if Path(expanded).exists():
                observer.schedule(handler, expanded, recursive=True)

        observer.start()
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(1.0)
        finally:
            observer.stop()
            observer.join()
