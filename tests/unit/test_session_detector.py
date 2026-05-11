"""Tests for the SessionDetector state machine."""
import time
import pytest
from capman.events import Event, EventType
from capman.pipeline.session import SessionDetector


def _make_event(etype: EventType, app: str = "Chrome", url: str = "", ts: float | None = None) -> Event:
    e = Event(type=etype, app=app)
    if ts is not None:
        e.ts = ts
    if url:
        e.payload = {"url": url, "title": "", "referrer": ""}
    if etype == EventType.SEARCH_QUERY:
        e.payload = {"engine": "google", "query": "test query", "url": url or "https://google.com"}
    if etype == EventType.KEYSTROKE:
        e.payload = {"text": "hello world", "is_paste": False, "field_type": "text"}
    return e


def _detector(idle=5, cool=3, hard=60, min_events=1) -> SessionDetector:
    return SessionDetector({"pipeline": {"session": {
        "idle_threshold_s": idle,
        "cool_period_s": cool,
        "hard_break_s": hard,
        "min_session_events": min_events,
    }}})


def test_idle_to_active_on_significant_event():
    d = _detector()
    assert d._state == "IDLE"
    e = _make_event(EventType.SEARCH_QUERY, ts=time.time())
    completed, current = d.ingest(e)
    assert d._state == "ACTIVE"
    assert current is not None
    assert completed is None


def test_window_focus_does_not_activate():
    d = _detector()
    e = _make_event(EventType.WINDOW_FOCUS, ts=time.time())
    completed, current = d.ingest(e)
    assert d._state == "IDLE"
    assert current is None


def test_keystroke_too_short_does_not_activate():
    d = _detector()
    e = Event(type=EventType.KEYSTROKE, app="VSCode")
    e.payload = {"text": "hi", "is_paste": False, "field_type": "text"}  # < min_text_length=3
    e.ts = time.time()
    completed, current = d.ingest(e)
    assert d._state == "IDLE"


def test_session_accumulates_events():
    d = _detector()
    now = time.time()
    events = [
        _make_event(EventType.SEARCH_QUERY, ts=now),
        _make_event(EventType.URL_VISIT, url="https://react.dev", ts=now + 1),
        _make_event(EventType.CLIPBOARD_COPY, ts=now + 2),
    ]
    events[2].payload = {"content": "test", "content_type": "text", "char_count": 4}

    for e in events:
        completed, current = d.ingest(e)
        assert completed is None

    assert d._current is not None
    assert len(d._current.events) == 3


def test_search_queries_collected():
    d = _detector()
    now = time.time()
    e = _make_event(EventType.SEARCH_QUERY, ts=now)
    e.payload["query"] = "react hydration"
    d.ingest(e)
    assert "react hydration" in d._current.search_queries


def test_urls_visited_collected():
    d = _detector()
    now = time.time()
    d.ingest(_make_event(EventType.SEARCH_QUERY, ts=now))
    url_event = _make_event(EventType.URL_VISIT, url="https://react.dev", ts=now + 1)
    d.ingest(url_event)
    assert "https://react.dev" in d._current.urls_visited


def test_check_timeouts_flushes_idle_session():
    d = _detector(idle=1, cool=1)
    now = time.time()
    d.ingest(_make_event(EventType.SEARCH_QUERY, ts=now))
    assert d._state == "ACTIVE"

    # Simulate time passing beyond idle + cool threshold
    d._last_significant_ts = now - 10  # 10s ago
    completed, current = d.check_timeouts()
    assert completed is not None
    assert d._state == "IDLE"


def test_flush_closes_current_session():
    d = _detector()
    d.ingest(_make_event(EventType.SEARCH_QUERY, ts=time.time()))
    assert d._current is not None
    session = d.flush()
    assert session is not None
    assert session.ended_at is not None
    assert d._current is None


def test_hard_break_creates_new_session():
    d = _detector(hard=10)
    now = time.time()
    d.ingest(_make_event(EventType.SEARCH_QUERY, ts=now))
    old_id = d._current.id

    # Event 20s later (past hard_break_s=10)
    late_event = _make_event(EventType.SEARCH_QUERY, ts=now + 20)
    completed, current = d.ingest(late_event)
    assert completed is not None
    assert completed.id == old_id
    assert current.id != old_id
