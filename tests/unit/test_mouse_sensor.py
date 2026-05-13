"""
Unit tests for the new MouseSensor — exercise the small helper classes
(``_ScrollCoalescer``, ``_MoveHeatmap``) and the click-element flow with a
mocked platform adapter. We deliberately avoid spawning pynput; the sensor's
two helpers are pure Python and fully testable.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from capman.events import EventType
from capman.sensors.mouse import (
    MouseSensor,
    _MoveHeatmap,
    _ScrollCoalescer,
)


# ── _ScrollCoalescer ──────────────────────────────────────────────────────────

def test_scroll_collapses_burst_into_one_payload():
    sc = _ScrollCoalescer(debounce_s=0.1, min_ticks=3)
    t0 = 1000.0
    for i in range(5):
        sc.add_tick(100, 200, 0, -1, t0 + i * 0.01, "Chrome", "page")
    payload = sc.flush_if_due(t0 + 0.5, "Chrome", "page")
    assert payload is not None
    assert payload["direction"] == "down"
    assert payload["ticks"] == 5
    assert payload["dy"] == -5
    assert payload["delta_total"] == 5
    assert payload["start_x"] == 100 and payload["start_y"] == 200


def test_scroll_drops_short_bursts():
    sc = _ScrollCoalescer(debounce_s=0.05, min_ticks=4)
    sc.add_tick(0, 0, 0, -1, 1000.0, "x", "")
    sc.add_tick(0, 0, 0, -1, 1000.01, "x", "")
    out = sc.flush_if_due(1000.5, "x", "")
    assert out is None  # 2 ticks < 4


def test_scroll_direction_switch_closes_previous_burst():
    sc = _ScrollCoalescer(debounce_s=0.1, min_ticks=2)
    for i in range(3):
        sc.add_tick(0, 0, 0, -1, 1000.0 + i * 0.01, "x", "")  # downward
    closed = sc.add_tick(0, 0, 0, +1, 1000.05, "x", "")        # flip → up
    assert closed is not None
    assert closed["direction"] == "down"
    # Subsequent ticks accumulate into the new (upward) burst.
    sc.add_tick(0, 0, 0, +1, 1000.06, "x", "")
    sc.add_tick(0, 0, 0, +1, 1000.07, "x", "")
    new = sc.flush_if_due(1000.5, "x", "")
    assert new and new["direction"] == "up"


def test_scroll_does_not_emit_while_still_active():
    sc = _ScrollCoalescer(debounce_s=0.5, min_ticks=2)
    sc.add_tick(0, 0, 0, -1, 1000.0, "x", "")
    sc.add_tick(0, 0, 0, -1, 1000.4, "x", "")
    # Only 0.1 s after last tick — debounce hasn't elapsed.
    assert sc.flush_if_due(1000.5, "x", "") is None


# ── _MoveHeatmap ──────────────────────────────────────────────────────────────

def test_heatmap_buckets_by_minute_and_app():
    hm = _MoveHeatmap(grid=10, screen_size=(100, 100))
    t = 60_000.0   # bucket = 1000
    hm.add("Chrome", 5, 5, t)
    hm.add("Chrome", 7, 7, t + 1)
    hm.add("Code", 50, 50, t)
    out = hm.drain_all()
    apps = {p["app"]: p for p in out}
    assert "Chrome" in apps and "Code" in apps
    chrome = apps["Chrome"]
    assert chrome["minute_bucket"] == 1000
    assert chrome["grid_size"] == 10
    # cells: (5,5)→ row=0 col=0, (7,7) → row=0 col=0 too at grid=10/screen=100
    assert sum(chrome["grid"].values()) == 2


def test_heatmap_drain_due_only_emits_finished_buckets():
    hm = _MoveHeatmap(grid=10, screen_size=(100, 100))
    past_minute = 59_940.0   # bucket 999
    current_minute = 60_000.0  # bucket 1000
    hm.add("a", 1, 1, past_minute)
    hm.add("a", 1, 1, current_minute)
    # "now" is in the current bucket → only the past bucket should drain.
    out = hm.drain_due(current_minute + 5)
    assert len(out) == 1
    assert out[0]["minute_bucket"] == 999
    # The current-bucket data is still pending.
    rest = hm.drain_all()
    assert len(rest) == 1 and rest[0]["minute_bucket"] == 1000


def test_heatmap_clamps_out_of_screen_coordinates():
    hm = _MoveHeatmap(grid=4, screen_size=(100, 100))
    hm.add("x", -50, -50, 0.0)        # off top-left
    hm.add("x", 999, 999, 0.0)        # off bottom-right
    out = hm.drain_all()
    assert len(out) == 1
    grid = out[0]["grid"]
    # Two cells: (0,0) and (3,3) — both clamped inside.
    assert "0,0" in grid and "3,3" in grid


# ── MouseSensor click + element-resolution flow ──────────────────────────────

class _FakeAdapter:
    def __init__(self, element):
        self._element = element
        self.calls = 0

    def get_element_at(self, x, y):
        self.calls += 1
        return self._element


def _make_sensor(adapter, *, resolve=True, timeout_ms=200):
    cfg = {
        "sensors": {
            "mouse": {
                "track_clicks": True,
                "resolve_element_at_click": resolve,
                "element_lookup_timeout_ms": timeout_ms,
                "track_scroll": False,
                "track_move_heatmap": False,
                "screen_size": [100, 100],
            }
        }
    }
    queue: asyncio.Queue = asyncio.Queue()
    sensor = MouseSensor(cfg, queue)
    # Bypass the run() bootstrap — we exercise _resolve_element_at directly.
    sensor._resolve_element = resolve
    sensor._element_timeout_s = timeout_ms / 1000.0
    sensor._adapter = adapter
    return sensor, queue


def test_click_resolves_element_via_adapter():
    adapter = _FakeAdapter({"role": "AXButton", "label": "Run all", "value": ""})
    sensor, _ = _make_sensor(adapter)
    out = sensor._resolve_element_at(10, 20, "PyCharm")
    assert out["label"] == "Run all"
    assert out["app"] == "PyCharm"
    assert adapter.calls == 1


def test_click_drops_element_with_empty_label():
    adapter = _FakeAdapter({"role": "AXGroup", "label": "  ", "value": ""})
    sensor, _ = _make_sensor(adapter)
    assert sensor._resolve_element_at(10, 20, "X") is None


def test_click_returns_none_when_adapter_returns_none():
    adapter = _FakeAdapter(None)
    sensor, _ = _make_sensor(adapter)
    assert sensor._resolve_element_at(10, 20, "X") is None


def test_click_resolution_respects_hard_timeout():
    class _SlowAdapter:
        def get_element_at(self, x, y):
            time.sleep(0.5)
            return {"role": "AXButton", "label": "Late"}
    sensor, _ = _make_sensor(_SlowAdapter(), timeout_ms=20)
    t0 = time.time()
    out = sensor._resolve_element_at(0, 0, "X")
    elapsed = time.time() - t0
    assert out is None
    assert elapsed < 0.2  # nowhere near the 0.5s sleep
