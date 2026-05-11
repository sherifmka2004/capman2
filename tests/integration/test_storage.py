"""Integration tests for SQLite storage layer."""
import asyncio
import time
import pytest
from capman.events import Event, EventType, Session
from capman.storage.timeline import TimelineDB


@pytest.fixture
async def db(tmp_path):
    db = TimelineDB(str(tmp_path / "test.db"))
    await db.migrate()
    yield db
    await db.close()


async def test_migrate_creates_tables(tmp_path):
    db = TimelineDB(str(tmp_path / "m.db"))
    await db.migrate()
    async with db._db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
        tables = {r[0] for r in await cur.fetchall()}
    assert "events" in tables
    assert "sessions" in tables
    assert "session_analyses" in tables
    assert "knowledge_triples" in tables
    await db.close()


async def test_insert_and_retrieve_event(db):
    e = Event(
        type=EventType.SEARCH_QUERY,
        app="Chrome",
        window_title="Google",
        payload={"engine": "google", "query": "test", "url": "https://google.com", "result_count": 10},
    )
    await db.insert_event(e)
    events = await db.get_events_since(0)
    assert len(events) == 1
    assert events[0].id == e.id
    assert events[0].type == EventType.SEARCH_QUERY
    assert events[0].payload["query"] == "test"


async def test_bulk_insert(db):
    events = [
        Event(type=EventType.WINDOW_FOCUS, app=f"App{i}")
        for i in range(10)
    ]
    await db.insert_events_bulk(events)
    count = await db.get_event_count()
    assert count == 10


async def test_assign_session(db):
    e = Event(type=EventType.URL_VISIT, app="Chrome",
              payload={"url": "https://example.com", "title": "Example", "referrer": ""})
    await db.insert_event(e)
    await db.assign_session(e.id, "sess-123")

    async with db._db.execute("SELECT session_id FROM events WHERE id = ?", (e.id,)) as cur:
        row = await cur.fetchone()
    assert row[0] == "sess-123"


async def test_upsert_session(db):
    session = Session(
        dominant_app="Chrome",
        primary_domain="stackoverflow.com",
        search_queries=["python async"],
        urls_visited=["https://stackoverflow.com/q/1"],
        commands_run=[],
    )
    session.ended_at = time.time()
    session.events = [Event(type=EventType.URL_VISIT)]
    await db.upsert_session(session)

    async with db._db.execute("SELECT id, dominant_app FROM sessions WHERE id = ?", (session.id,)) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[1] == "Chrome"


async def test_get_unanalyzed_sessions(db):
    session = Session(dominant_app="Terminal", ended_at=time.time())
    session.events = [Event(type=EventType.SHELL_COMMAND)]
    await db.upsert_session(session)

    unanalyzed = await db.get_unanalyzed_sessions()
    assert any(s["id"] == session.id for s in unanalyzed)
