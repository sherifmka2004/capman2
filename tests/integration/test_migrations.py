"""Migration runner: versioning, idempotency, and upgrade of legacy databases."""
import sqlite3

import pytest

from capman.storage.timeline import TimelineDB


async def _user_version(db: TimelineDB) -> int:
    async with db._db.execute("PRAGMA user_version") as cur:
        return (await cur.fetchone())[0]


async def _tables(db: TimelineDB) -> set[str]:
    async with db._db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
        return {r[0] for r in await cur.fetchall()}


@pytest.fixture
async def db(tmp_path):
    d = TimelineDB(str(tmp_path / "t.db"))
    await d.connect()
    yield d
    await d.close()


def test_migrations_are_discoverable_and_ordered():
    found = TimelineDB._discover_migrations()
    assert found, "no migration files discovered"
    versions = [v for v, _ in found]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions)), f"duplicate migration versions: {versions}"
    assert versions[0] == 1


async def test_migrate_creates_schema_and_sets_version(db):
    await db.migrate()
    tables = await _tables(db)
    for expected in ("events", "sessions", "session_analyses", "knowledge_triples",
                     "playbooks", "knowledge_gaps", "screenshots", "brain_domains"):
        assert expected in tables
    latest = TimelineDB._discover_migrations()[-1][0]
    assert await _user_version(db) == latest


async def test_migrate_is_idempotent(db):
    await db.migrate()
    first = await _user_version(db)
    await db.migrate()  # second run must be a no-op
    assert await _user_version(db) == first


async def test_legacy_database_upgrades(tmp_path):
    """A database created before the runner existed has user_version=0 but real tables."""
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        "CREATE TABLE events (id TEXT PRIMARY KEY, type TEXT NOT NULL, ts REAL NOT NULL,"
        " app TEXT DEFAULT '', window_title TEXT DEFAULT '', payload TEXT NOT NULL DEFAULT '{}',"
        " sensor_id TEXT DEFAULT '', session_id TEXT);"
        "INSERT INTO events (id, type, ts) VALUES ('e1', 'keystroke', 1.0);"
    )
    raw.commit()
    raw.close()

    d = TimelineDB(str(path))
    await d.connect()
    assert await _user_version(d) == 0
    await d.migrate()
    assert await _user_version(d) == TimelineDB._discover_migrations()[-1][0]

    # pre-existing row survived the upgrade
    async with d._db.execute("SELECT COUNT(*) FROM events") as cur:
        assert (await cur.fetchone())[0] == 1
    await d.close()
