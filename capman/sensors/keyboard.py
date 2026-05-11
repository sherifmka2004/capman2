"""
Keystroke sensor using pynput.
Aggregates individual keystrokes into text blocks (within aggregate_window_ms).
Runs pynput listener in a background thread and bridges events into asyncio queue.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import ClassVar

from capman.events import Event, EventType
from capman.sensors.base import BaseSensor

logger = logging.getLogger(__name__)

_PASSWORD_HINTS = frozenset(["password", "passwd", "secret", "pin", "passphrase"])


class KeyboardSensor(BaseSensor):
    sensor_id: ClassVar[str] = "keyboard"
    platform_support: ClassVar[set[str]] = {"*"}
    requires_permissions: ClassVar[list[str]] = ["accessibility"]

    async def run(self) -> None:
        cfg = self.config.get("sensors", {}).get("keyboard", {})
        self._aggregate_window_s = cfg.get("aggregate_window_ms", 500) / 1000.0
        self._exclude_apps = set(cfg.get("exclude_apps", []))
        self._min_text_length = cfg.get("min_text_length", 3)

        self._buffer: list[str] = []
        self._last_key_ts: float = 0.0
        self._current_app: str = ""
        self._current_title: str = ""
        self._lock = threading.Lock()
        self._loop = asyncio.get_event_loop()

        listener_thread = threading.Thread(target=self._start_listener, daemon=True)
        listener_thread.start()

        # Flush loop: emit aggregated text blocks
        while not self._stop_event.is_set():
            await asyncio.sleep(self._aggregate_window_s)
            self._flush_buffer()

        if hasattr(self, "_listener"):
            self._listener.stop()

    def _start_listener(self) -> None:
        try:
            from pynput import keyboard  # type: ignore
            self._listener = keyboard.Listener(
                on_press=self._on_press,
                suppress=False,
            )
            self._listener.start()
            self._listener.join()
        except Exception as e:
            logger.warning("Keyboard listener failed to start: %s", e)

    def _on_press(self, key) -> None:
        try:
            from pynput import keyboard  # type: ignore
            # Update app context
            from capman.platform.base import get_platform_adapter
            app, title = get_platform_adapter(self.config).get_active_window()
            with self._lock:
                self._current_app = app
                self._current_title = title

            if app in self._exclude_apps:
                return
            if self._is_password_context(title):
                return

            char = None
            if hasattr(key, "char") and key.char is not None:
                char = key.char
            elif key == keyboard.Key.space:
                char = " "
            elif key == keyboard.Key.enter:
                char = "\n"
            elif key == keyboard.Key.backspace:
                with self._lock:
                    if self._buffer:
                        self._buffer.pop()
                return

            if char:
                with self._lock:
                    self._buffer.append(char)
                    self._last_key_ts = time.time()
        except Exception as e:
            logger.debug("KeyboardSensor._on_press error: %s", e)

    def _flush_buffer(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            if time.time() - self._last_key_ts < self._aggregate_window_s:
                return  # Still typing
            text = "".join(self._buffer)
            self._buffer.clear()
            app = self._current_app
            title = self._current_title

        if len(text.strip()) < self._min_text_length:
            return

        event = Event(
            type=EventType.KEYSTROKE,
            app=app,
            window_title=title,
            payload={"text": text, "is_paste": False, "field_type": "text"},
            sensor_id=self.sensor_id,
        )
        self.emit_sync(event)

    @staticmethod
    def _is_password_context(window_title: str) -> bool:
        title_lower = window_title.lower()
        return any(hint in title_lower for hint in _PASSWORD_HINTS)
