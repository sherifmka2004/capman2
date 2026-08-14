"""Write path: pragmas, write-behind batching, and single-write session stamping."""
import time

import pytest

from capman.events import Event, EventType
from capman.storage.timeline import TimelineDB


@pytest.fixture
async def db(tmp_path):
    d = TimelineDB(str(tmp_path / "t.db"))
    await d.migrate()
    yield d
    await d.close()


def _ev(i: int, etype=EventType.KEYSTROKE) -> Event:
    e = Event(type=etype, app="Terminal", payload={"text": f"cmd-{i}"})
    e.ts = time.time() + i
    return e


async def _count(db) -> int:
    async with db._db.execute("SELECT COUNT(*) FROM events") as cur:
        return (await cur.fetchone())[0]


async def test_pragmas_applied(db):
    async with db._db.execute("PRAGMA journal_mode") as cur:
        assert (await cur.fetchone())[0].lower() == "wal"
    async with db._db.execute("PRAGMA synchronous") as cur:
        assert (await cur.fetchone())[0] == 1  # NORMAL
    async with db._db.execute("PRAGMA busy_timeout") as cur:
        assert (await cur.fetchone())[0] == 5000


async def test_queued_events_are_buffered_then_flushed(db):
    for i in range(5):
        await db.queue_event(_ev(i))
    assert await _count(db) == 0, "queue_event should not write through immediately"

    await db.flush()
    assert await _count(db) == 5


async def test_threshold_flush_is_automatic(db):
    for i in range(TimelineDB.FLUSH_THRESHOLD):
        await db.queue_event(_ev(i))
    assert await _count(db) == TimelineDB.FLUSH_THRESHOLD


async def test_flush_is_idempotent_and_safe_when_empty(db):
    await db.flush()
    await db.queue_event(_ev(1))
    await db.flush()
    await db.flush()
    assert await _count(db) == 1


async def test_session_id_is_stamped_at_insert(db):
    e = _ev(1)
    await db.queue_event(e, "sess-abc")
    await db.flush()
    async with db._db.execute("SELECT session_id FROM events WHERE id = ?", (e.id,)) as cur:
        assert (await cur.fetchone())[0] == "sess-abc"


async def test_assign_session_bulk_does_not_clobber_existing_membership(db):
    stamped, unstamped = _ev(1), _ev(2)
    await db.queue_event(stamped, "original")
    await db.queue_event(unstamped, None)
    await db.flush()

    await db.assign_session_bulk([stamped.id, unstamped.id], "repair")

    async with db._db.execute(
        "SELECT id, session_id FROM events WHERE id IN (?, ?)", (stamped.id, unstamped.id)
    ) as cur:
        got = {r[0]: r[1] for r in await cur.fetchall()}
    assert got[stamped.id] == "original", "repair path must not overwrite a real assignment"
    assert got[unstamped.id] == "repair"


async def test_reads_flush_pending_writes(db):
    e = _ev(1)
    await db.queue_event(e, "s1")
    # No explicit flush — the accessor must not report stale state.
    assert await db.get_event_count() == 1
    assert len(await db.get_session_events("s1")) == 1


async def test_close_checkpoints_and_persists(tmp_path):
    d = TimelineDB(str(tmp_path / "c.db"))
    await d.migrate()
    for i in range(10):
        await d.queue_event(_ev(i), "s1")
    await d.close()

    wal = tmp_path / "c.db-wal"
    assert not wal.exists() or wal.stat().st_size == 0, "WAL should be checkpointed on close"

    d2 = TimelineDB(str(tmp_path / "c.db"))
    await d2.migrate()
    assert await _count(d2) == 10, "buffered events must survive shutdown"
    await d2.close()
