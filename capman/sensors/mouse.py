"""
MouseSensor — captures meaningful mouse activity:

  • MOUSE_CLICK         → one event per click, enriched with the UI element
                          under the cursor (best-effort via the platform
                          adapter's accessibility hook). Falls back to raw
                          {x, y} when no element can be resolved.
  • MOUSE_SCROLL        → one event per *coalesced burst* of scroll ticks
                          (debounced ``scroll_debounce_ms``); avoids thousands
                          of micro-events for one wheel spin.
  • MOUSE_HEATMAP_TICK  → one event per active app per minute, payload is a
                          100×100 grid of move counts. Mouse-move data without
                          ballooning storage; per-event move tracking is *not*
                          stored.

All input also bumps ``activity_context.record_input_activity`` so the
``IdleSensor`` can detect AFK windows.

Threading: pynput callbacks fire on a background thread; everything that
touches asyncio is marshalled through ``loop.call_soon_threadsafe``. The
element-resolution call is wrapped in a hard timeout so a slow accessibility
call can't stall the listener thread.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import defaultdict
from typing import ClassVar

from capman.events import Event, EventType
from capman.sensors.activity_context import record_input_activity
from capman.sensors.base import BaseSensor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers — kept as small standalone classes (Single-Responsibility).
# Each is fed events from the pynput thread and decides what to emit.
# ---------------------------------------------------------------------------


class _ScrollCoalescer:
    """Collapse a burst of scroll ticks (same direction) into one MOUSE_SCROLL.

    A burst ends when no scroll happens for ``debounce_s`` seconds. Bursts with
    fewer than ``min_ticks`` ticks are silently dropped (mouse-wheel jitter).
    """

    def __init__(self, *, debounce_s: float, min_ticks: int):
        self._debounce_s = float(debounce_s)
        self._min_ticks = max(1, int(min_ticks))
        self._lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        self._direction: str = ""
        self._dx_total = 0
        self._dy_total = 0
        self._ticks = 0
        self._started_at = 0.0
        self._last_tick_at = 0.0
        self._start_xy: tuple[int, int] = (0, 0)
        self._last_xy: tuple[int, int] = (0, 0)

    def add_tick(self, x: int, y: int, dx: int, dy: int, ts: float,
                 app: str, title: str) -> dict | None:
        """Record one scroll tick. Returns a payload dict to emit if this tick
        starts a NEW burst that supersedes a closed one; otherwise None.
        Bursts close via a timer in the sensor (see ``flush_if_due``)."""
        # Direction encoded by sign of the dominant axis. Switching direction
        # closes the previous burst and starts a new one — return the closed
        # one so the caller can emit it.
        new_dir = self._direction_for(dx, dy)
        with self._lock:
            closed_payload: dict | None = None
            if self._direction and new_dir != self._direction:
                closed_payload = self._build_payload_locked(app, title)
                self._reset()
            if not self._direction:
                self._direction = new_dir
                self._started_at = ts
                self._start_xy = (int(x), int(y))
            self._dx_total += int(dx)
            self._dy_total += int(dy)
            self._ticks += 1
            self._last_tick_at = ts
            self._last_xy = (int(x), int(y))
        return closed_payload

    def flush_if_due(self, now: float, app: str, title: str) -> dict | None:
        """Called periodically. Returns a payload to emit if the burst has
        gone quiet for >= debounce_s and reaches the min-ticks bar."""
        with self._lock:
            if not self._direction:
                return None
            if (now - self._last_tick_at) < self._debounce_s:
                return None
            payload = self._build_payload_locked(app, title)
            self._reset()
            return payload

    def _build_payload_locked(self, app: str, title: str) -> dict | None:
        if self._ticks < self._min_ticks:
            return None
        return {
            "direction": self._direction,
            "delta_total": int(abs(self._dy_total) + abs(self._dx_total)),
            "duration_s": round(self._last_tick_at - self._started_at, 2),
            "ticks": int(self._ticks),
            "dx": int(self._dx_total),
            "dy": int(self._dy_total),
            "start_x": int(self._start_xy[0]),
            "start_y": int(self._start_xy[1]),
            "end_x": int(self._last_xy[0]),
            "end_y": int(self._last_xy[1]),
        }

    @staticmethod
    def _direction_for(dx: int, dy: int) -> str:
        if abs(dy) >= abs(dx):
            return "down" if dy < 0 else "up"
        return "right" if dx > 0 else "left"


class _MoveHeatmap:
    """Aggregates raw mouse-move events into a per-app, per-minute coarse grid
    (default 100 × 100 cells). One ``MOUSE_HEATMAP_TICK`` per app per minute
    is emitted on flush — total data volume is tiny."""

    def __init__(self, *, grid: int, screen_size: tuple[int, int]):
        self._grid = max(2, int(grid))
        self._screen_w, self._screen_h = max(1, int(screen_size[0])), max(1, int(screen_size[1]))
        # bucket → app → cell-key → count
        self._buckets: dict[int, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
        self._lock = threading.Lock()

    def add(self, app: str, x: int, y: int, ts: float) -> None:
        if not app:
            return
        col = min(self._grid - 1, max(0, int(int(x) * self._grid / self._screen_w)))
        row = min(self._grid - 1, max(0, int(int(y) * self._grid / self._screen_h)))
        bucket = int(ts // 60)
        key = f"{row},{col}"
        with self._lock:
            self._buckets[bucket][app][key] += 1

    def drain_due(self, now: float) -> list[dict]:
        """Return payload list for buckets that are now in the past."""
        cutoff = int(now // 60)
        out: list[dict] = []
        with self._lock:
            done_buckets = [b for b in list(self._buckets.keys()) if b < cutoff]
            for b in done_buckets:
                per_app = self._buckets.pop(b)
                for app, grid in per_app.items():
                    out.append({
                        "app": app,
                        "minute_bucket": int(b),
                        "grid": dict(grid),
                        "grid_size": self._grid,
                        "screen_size": [self._screen_w, self._screen_h],
                    })
        return out

    def drain_all(self) -> list[dict]:
        """Flush everything, regardless of bucket age (used at shutdown)."""
        out: list[dict] = []
        with self._lock:
            for b, per_app in self._buckets.items():
                for app, grid in per_app.items():
                    out.append({
                        "app": app,
                        "minute_bucket": int(b),
                        "grid": dict(grid),
                        "grid_size": self._grid,
                        "screen_size": [self._screen_w, self._screen_h],
                    })
            self._buckets.clear()
        return out


# ---------------------------------------------------------------------------
# MouseSensor (orchestrator)
# ---------------------------------------------------------------------------


class MouseSensor(BaseSensor):
    sensor_id: ClassVar[str] = "mouse"
    platform_support: ClassVar[set[str]] = {"*"}
    requires_permissions: ClassVar[list[str]] = ["accessibility"]

    async def run(self) -> None:
        cfg = self.config.get("sensors", {}).get("mouse", {})
        self._track_clicks = bool(cfg.get("track_clicks", True))
        self._resolve_element = bool(cfg.get("resolve_element_at_click", True))
        self._element_timeout_s = max(0.005, float(cfg.get("element_lookup_timeout_ms", 50)) / 1000.0)
        self._track_scroll = bool(cfg.get("track_scroll", True))
        self._track_heatmap = bool(cfg.get("track_move_heatmap", True))
        flush_interval_s = max(5, int(cfg.get("heatmap_flush_interval_s", 60)))
        self._scroll = _ScrollCoalescer(
            debounce_s=cfg.get("scroll_debounce_ms", 800) / 1000.0,
            min_ticks=int(cfg.get("scroll_min_burst_ticks", 3)),
        )
        self._heatmap = _MoveHeatmap(
            grid=int(cfg.get("heatmap_grid", 100)),
            screen_size=tuple(cfg.get("screen_size", self._guess_screen_size())),
        )
        self._loop = asyncio.get_event_loop()
        self._adapter = None  # lazy

        listener_thread = threading.Thread(target=self._start_listener, daemon=True)
        listener_thread.start()

        try:
            tick = 0
            while not self._stop_event.is_set():
                await asyncio.sleep(1.0)
                tick += 1
                # Once a second: drain any scroll burst that's gone quiet.
                if self._track_scroll:
                    payload = self._scroll.flush_if_due(time.time(), *self._current_app())
                    if payload:
                        await self._emit_scroll(payload)
                # Every flush_interval_s seconds: drain finished heatmap buckets.
                if self._track_heatmap and (tick % flush_interval_s == 0):
                    for hp in self._heatmap.drain_due(time.time()):
                        await self._emit_heatmap(hp)
        finally:
            if self._track_heatmap:
                for hp in self._heatmap.drain_all():
                    try:
                        await self._emit_heatmap(hp)
                    except Exception:
                        pass
            if hasattr(self, "_listener"):
                try:
                    self._listener.stop()
                except Exception:
                    pass

    # -- pynput plumbing -----------------------------------------------------

    def _start_listener(self) -> None:
        try:
            from pynput import mouse  # type: ignore
            self._listener = mouse.Listener(
                on_click=self._on_click,
                on_scroll=self._on_scroll if self._track_scroll else None,
                on_move=self._on_move if self._track_heatmap else None,
            )
            self._listener.start()
            self._listener.join()
        except Exception as e:
            logger.warning("Mouse listener failed: %s", e)

    def _on_click(self, x, y, button, pressed) -> None:
        if not pressed:
            return
        record_input_activity()
        if not self._track_clicks:
            return
        try:
            app, title = self._current_app()
            element = self._resolve_element_at(int(x), int(y), app) if self._resolve_element else None
            payload: dict = {"button": str(button), "x": int(x), "y": int(y)}
            if element:
                payload["element"] = element
            event = Event(
                type=EventType.MOUSE_CLICK,
                app=app,
                window_title=title,
                payload=payload,
                sensor_id=self.sensor_id,
            )
            self.emit_sync(event)
        except Exception as e:
            logger.debug("MouseSensor._on_click error: %s", e)

    def _on_scroll(self, x, y, dx, dy) -> None:
        record_input_activity()
        try:
            app, title = self._current_app()
            closed = self._scroll.add_tick(int(x), int(y), int(dx), int(dy), time.time(), app, title)
            if closed:
                # A direction switch closed the previous burst — emit it now.
                self._loop.call_soon_threadsafe(self._spawn_emit_scroll, closed)
        except Exception as e:
            logger.debug("MouseSensor._on_scroll error: %s", e)

    def _on_move(self, x, y) -> None:
        # Cheap path; we DON'T record_input_activity for moves (would defeat
        # AFK detection — moves can happen passively from cats, vibrations,
        # etc.). Clicks/scroll/keys are the canonical "user is here" signals.
        try:
            app, _title = self._current_app()
            self._heatmap.add(app, int(x), int(y), time.time())
        except Exception:
            pass  # never raise from a hot mouse-move callback

    # -- element resolution --------------------------------------------------

    def _resolve_element_at(self, x: int, y: int, app: str) -> dict | None:
        """Hard-bounded call into the platform adapter. Returns None on
        timeout / exception — never blocks the listener thread for long."""
        adapter = self._get_adapter()
        if adapter is None:
            return None
        result: dict | None = None
        finished = threading.Event()

        def _runner() -> None:
            nonlocal result
            try:
                result = adapter.get_element_at(x, y)
            except Exception as e:
                logger.debug("get_element_at raised: %s", e)
                result = None
            finally:
                finished.set()

        worker = threading.Thread(target=_runner, daemon=True)
        worker.start()
        # Hard ceiling per click — if the adapter is slow, we bail.
        if not finished.wait(timeout=self._element_timeout_s):
            return None
        if not result:
            return None
        # Stamp the active app onto the element if the adapter didn't.
        if app and not result.get("app"):
            result = {**result, "app": app}
        # Drop entries with no useful label — nothing to show downstream.
        if not (result.get("label") or "").strip():
            return None
        return result

    # -- emit helpers --------------------------------------------------------

    async def _emit_scroll(self, payload: dict) -> None:
        app, title = self._current_app()
        await self.emit(Event(
            type=EventType.MOUSE_SCROLL,
            app=app,
            window_title=title,
            payload=payload,
            sensor_id=self.sensor_id,
        ))

    def _spawn_emit_scroll(self, payload: dict) -> None:
        # Called from pynput thread via call_soon_threadsafe — schedule on loop.
        try:
            self._loop.create_task(self._emit_scroll(payload))
        except Exception:
            pass

    async def _emit_heatmap(self, payload: dict) -> None:
        app = payload.get("app", "")
        await self.emit(Event(
            type=EventType.MOUSE_HEATMAP_TICK,
            app=app,
            window_title="",
            payload=payload,
            sensor_id=self.sensor_id,
        ))

    # -- misc ---------------------------------------------------------------

    def _current_app(self) -> tuple[str, str]:
        """Cheap read of the current foreground (set by WindowSensor). Falls
        back to (\"\", \"\") if nothing has been recorded yet."""
        try:
            from capman.sensors.activity_context import get_foreground
            app, title, _ = get_foreground()
            return app, title
        except Exception:
            return "", ""

    def _get_adapter(self):
        if self._adapter is not None:
            return self._adapter
        try:
            from capman.platform.base import get_platform_adapter
            self._adapter = get_platform_adapter(self.config)
        except Exception as e:
            logger.debug("MouseSensor adapter init failed: %s", e)
            self._adapter = None
        return self._adapter

    @staticmethod
    def _guess_screen_size() -> tuple[int, int]:
        try:
            import mss  # type: ignore
            with mss.mss() as sct:
                m = sct.monitors[0]   # virtual bounding box across all screens
                return int(m.get("width", 1920)), int(m.get("height", 1080))
        except Exception:
            return 1920, 1080
