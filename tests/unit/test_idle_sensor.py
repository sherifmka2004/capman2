"""
Unit tests for IdleSensor — drives it with a controlled
``activity_context`` clock to exercise the threshold/return logic without
real-time waits.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from capman.events import EventType
from capman.sensors import activity_context as ac
from capman.sensors.idle import IdleSensor


@pytest.fixture(autouse=True)
def _reset_activity_context():
    """Each test starts with a fresh activity_context state so tests don't
    contaminate each other."""
    ac._last_input_ts = 0.0  # type: ignore[attr-defined]
    yield
    ac._last_input_ts = 0.0  # type: ignore[attr-defined]


def _make_sensor(threshold_s=0.3, poll_s=0.05):
    cfg = {"sensors": {"idle": {
        "enabled": True,
        "idle_threshold_s": threshold_s,
        "poll_interval_s": poll_s,
    }}}
    queue: asyncio.Queue = asyncio.Queue()
    return IdleSensor(cfg, queue), queue


@pytest.mark.asyncio
async def test_no_idle_event_when_user_keeps_typing():
    sensor, queue = _make_sensor(threshold_s=0.3, poll_s=0.05)
    task = asyncio.create_task(sensor.run())
    # Bump the activity timer faster than the threshold for ~0.4s
    end = time.time() + 0.4
    while time.time() < end:
        ac.record_input_activity()
        await asyncio.sleep(0.05)
    sensor.stop()
    await asyncio.wait_for(task, timeout=1.0)
    assert queue.empty()


@pytest.mark.asyncio
async def test_idle_start_then_idle_end():
    sensor, queue = _make_sensor(threshold_s=0.2, poll_s=0.05)
    # Seed an "old" input timestamp so the very first poll trips IDLE_START.
    ac.record_input_activity(time.time() - 1.0)
    task = asyncio.create_task(sensor.run())
    # Let the sensor poll once and emit IDLE_START
    await asyncio.sleep(0.15)
    # Now "user comes back"
    ac.record_input_activity()
    await asyncio.sleep(0.15)
    sensor.stop()
    await asyncio.wait_for(task, timeout=1.0)
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    types = [e.type for e in events]
    assert EventType.IDLE_START in types
    assert EventType.IDLE_END in types
    assert types.index(EventType.IDLE_START) < types.index(EventType.IDLE_END)
    end = next(e for e in events if e.type == EventType.IDLE_END)
    assert end.payload["idle_duration_s"] >= 0.0


@pytest.mark.asyncio
async def test_no_double_idle_start():
    sensor, queue = _make_sensor(threshold_s=0.2, poll_s=0.05)
    ac.record_input_activity(time.time() - 1.0)
    task = asyncio.create_task(sensor.run())
    # No further input — should still emit only one IDLE_START.
    await asyncio.sleep(0.4)
    sensor.stop()
    await asyncio.wait_for(task, timeout=1.0)
    starts = 0
    while not queue.empty():
        e = queue.get_nowait()
        if e.type == EventType.IDLE_START:
            starts += 1
    assert starts == 1


@pytest.mark.asyncio
async def test_first_launch_with_no_input_yet_does_not_emit():
    sensor, queue = _make_sensor(threshold_s=0.2, poll_s=0.05)
    # Don't seed anything → time_since_last_input returns +inf, sensor stays quiet.
    task = asyncio.create_task(sensor.run())
    await asyncio.sleep(0.3)
    sensor.stop()
    await asyncio.wait_for(task, timeout=1.0)
    assert queue.empty()


@pytest.mark.asyncio
async def test_disabled_sensor_returns_immediately():
    cfg = {"sensors": {"idle": {"enabled": False}}}
    queue: asyncio.Queue = asyncio.Queue()
    sensor = IdleSensor(cfg, queue)
    await asyncio.wait_for(sensor.run(), timeout=0.5)
    assert queue.empty()
