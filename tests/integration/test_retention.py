"""Retention: what expires, what is protected, and what survives a prune."""
import time

import pytest

from capman.events import Event, EventType
from capman.storage.retention import (
    DEFAULT_TTL_DAYS, PROTECTED_TYPES, estimate_growth, prune_events, resolve_ttls,
)
from capman.storage.timeline import TimelineDB

DAY = 86400.0


@pytest.fixture
async def db(tmp_path):
    d = TimelineDB(str(tmp_path / "t.db"))
    await d.migrate()
    yield d
    await d.close()


async def _seed(db, now, spec):
    """spec: [(EventType, age_days, count, session_id)]"""
    for etype, age, count, sid in spec:
        for i in range(count):
            e = Event(type=etype, app="X", payload={"command": f"c{i}", "url": f"u{i}"})
            e.ts = now - age * DAY + i
            await db.queue_event(e, sid)
    await db.flush()


async def _count(db, etype=None):
    if etype:
        async with db._db.execute("SELECT COUNT(*) FROM events WHERE type = ?", (etype,)) as c:
            return (await c.fetchone())[0]
    async with db._db.execute("SELECT COUNT(*) FROM events") as c:
        return (await c.fetchone())[0]


# --------------------------------------------------------------------------
# Policy resolution
# --------------------------------------------------------------------------

def test_protected_types_cannot_be_pruned_by_config():
    """A user (or a bad default) must not be able to expire the analysable record."""
    cfg = {"storage": {"retention": {"ttl_days": {"shell_command": 1, "code_diff": 1}}}}
    resolved = resolve_ttls(cfg)
    assert "shell_command" not in resolved
    assert "code_diff" not in resolved


def test_zero_ttl_means_keep_forever():
    cfg = {"storage": {"retention": {"ttl_days": {"mouse_click": 0}}}}
    assert "mouse_click" not in resolve_ttls(cfg)


def test_config_overrides_defaults():
    cfg = {"storage": {"retention": {"ttl_days": {"mouse_click": 3}}}}
    assert resolve_ttls(cfg)["mouse_click"] == 3


def test_defaults_and_protected_sets_do_not_overlap():
    assert not (set(DEFAULT_TTL_DAYS) & PROTECTED_TYPES)


# --------------------------------------------------------------------------
# Pruning
# --------------------------------------------------------------------------

async def test_expired_low_signal_events_are_pruned(db):
    now = time.time()
    await _seed(db, now, [(EventType.MOUSE_SCROLL, 30, 10, "s1")])
    cfg = {"storage": {"retention": {"ttl_days": {"mouse_scroll": 14}}}}

    deleted = await prune_events(db, cfg, now=now)
    assert deleted == {"mouse_scroll": 10}
    assert await _count(db, "mouse_scroll") == 0


async def test_recent_events_are_kept(db):
    now = time.time()
    await _seed(db, now, [(EventType.MOUSE_SCROLL, 2, 10, "s1")])
    cfg = {"storage": {"retention": {"ttl_days": {"mouse_scroll": 14}}}}

    assert await prune_events(db, cfg, now=now) == {}
    assert await _count(db, "mouse_scroll") == 10


async def test_high_signal_events_survive_even_when_ancient(db):
    now = time.time()
    await _seed(db, now, [
        (EventType.SHELL_COMMAND, 3650, 5, "s1"),
        (EventType.URL_VISIT, 3650, 5, "s1"),
        (EventType.CODE_DIFF, 3650, 5, "s1"),
        (EventType.MOUSE_SCROLL, 3650, 5, "s1"),
    ])
    await prune_events(db, {}, now=now)

    assert await _count(db, "shell_command") == 5
    assert await _count(db, "url_visit") == 5
    assert await _count(db, "code_diff") == 5
    assert await _count(db, "mouse_scroll") == 0


async def test_sessions_with_playbooks_are_protected(db):
    now = time.time()
    await db._db.execute("INSERT INTO sessions (id, started_at) VALUES ('keep', 1.0)")
    await db._db.execute(
        "INSERT INTO playbooks (id, session_id, title) VALUES ('p1', 'keep', 'Fix it')")
    await db._db.commit()
    await _seed(db, now, [
        (EventType.MOUSE_SCROLL, 30, 5, "keep"),
        (EventType.MOUSE_SCROLL, 30, 5, "other"),
    ])
    cfg = {"storage": {"retention": {"ttl_days": {"mouse_scroll": 14},
                                     "protect_analyzed_sessions": True}}}

    deleted = await prune_events(db, cfg, now=now)
    assert deleted == {"mouse_scroll": 5}
    async with db._db.execute(
        "SELECT COUNT(*) FROM events WHERE session_id = 'keep'") as c:
        assert (await c.fetchone())[0] == 5


async def test_rollup_preserves_counts_after_prune(db):
    """The aggregate record must survive even though the rows do not."""
    now = time.time()
    await _seed(db, now, [(EventType.MOUSE_SCROLL, 30, 12, "s1")])
    cfg = {"storage": {"retention": {"ttl_days": {"mouse_scroll": 14}}}}

    await prune_events(db, cfg, now=now)

    async with db._db.execute(
        "SELECT count FROM session_event_stats WHERE session_id='s1' AND type='mouse_scroll'"
    ) as c:
        row = await c.fetchone()
    assert row is not None, "counts were lost with the rows"
    assert row[0] == 12


async def test_dry_run_changes_nothing(db):
    now = time.time()
    await _seed(db, now, [(EventType.MOUSE_SCROLL, 30, 7, "s1")])
    cfg = {"storage": {"retention": {"ttl_days": {"mouse_scroll": 14}}}}

    preview = await prune_events(db, cfg, now=now, dry_run=True)
    assert preview == {"mouse_scroll": 7}
    assert await _count(db, "mouse_scroll") == 7, "dry run must not delete"
    async with db._db.execute("SELECT COUNT(*) FROM retention_runs") as c:
        assert (await c.fetchone())[0] == 0


async def test_prune_is_audited(db):
    now = time.time()
    await _seed(db, now, [(EventType.MOUSE_SCROLL, 30, 4, "s1")])
    await prune_events(db, {"storage": {"retention": {"ttl_days": {"mouse_scroll": 14}}}}, now=now)

    async with db._db.execute(
        "SELECT type, deleted FROM retention_runs ORDER BY ran_at DESC LIMIT 1") as c:
        row = await c.fetchone()
    assert row["type"] == "mouse_scroll"
    assert row["deleted"] == 4


async def test_prune_larger_than_one_chunk(db):
    """Deletes are chunked; make sure the loop terminates and clears everything."""
    from capman.storage import retention
    now = time.time()
    await _seed(db, now, [(EventType.MOUSE_SCROLL, 30, 250, "s1")])

    original = retention.DELETE_CHUNK
    retention.DELETE_CHUNK = 100
    try:
        deleted = await prune_events(
            db, {"storage": {"retention": {"ttl_days": {"mouse_scroll": 14}}}}, now=now)
    finally:
        retention.DELETE_CHUNK = original

    assert deleted == {"mouse_scroll": 250}
    assert await _count(db, "mouse_scroll") == 0


async def test_pruned_events_leave_the_search_index(db):
    """A deleted event must not linger in FTS as a phantom hit."""
    from capman.storage.search import SearchIndex
    now = time.time()
    e = Event(type=EventType.KEYSTROKE, app="X", payload={"text": "supersecretphrase"})
    e.ts = now - 400 * DAY
    await db.queue_event(e, "s1")
    await db.flush()
    assert await SearchIndex(db).event_search("supersecretphrase")

    await prune_events(db, {"storage": {"retention": {"ttl_days": {"keystroke": 30}}}}, now=now)
    assert await SearchIndex(db).event_search("supersecretphrase") == []


async def test_growth_estimate(db):
    now = time.time()
    await _seed(db, now, [(EventType.URL_VISIT, 10, 50, "s1"),
                          (EventType.URL_VISIT, 0, 50, "s1")])
    stats = await estimate_growth(db)
    assert stats["events"] == 100
    assert stats["span_days"] > 9
    assert stats["events_per_day"] > 0
    assert stats["projected_bytes_per_year"] > 0


async def test_pruning_actually_shrinks_the_database(db, tmp_path):
    """Deleting rows only moves pages to the freelist; the file must really shrink.

    Guards the incremental_vacuum drain: a single fixed-size slice reclaimed
    ~4 MB regardless of how much was pruned, which made retention look
    effective in row counts while the disk barely moved.
    """
    import os
    from capman.storage.retention import reclaim_free_pages

    now = time.time()
    # Enough rows that the freelist far exceeds one vacuum slice.
    for i in range(12_000):
        e = Event(type=EventType.MOUSE_SCROLL, app="X", payload={"text": "x" * 200})
        e.ts = now - 100 * DAY + i
        await db.queue_event(e, "s1")
    await db.flush()
    await db._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    await db._db.commit()

    path = db._path
    before = os.path.getsize(path)

    await prune_events(db, {"storage": {"retention": {"ttl_days": {"mouse_scroll": 14}}}}, now=now)
    after = os.path.getsize(path)

    assert await _count(db, "mouse_scroll") == 0
    assert after < before * 0.6, (
        f"database did not shrink meaningfully: {before} -> {after} bytes"
    )


async def test_reclaim_is_a_noop_with_an_empty_freelist(db):
    from capman.storage.retention import reclaim_free_pages
    assert await reclaim_free_pages(db) == 0
