"""Query-plan guard.

The storage layer is fast because of a small set of indexes chosen from real
EXPLAIN QUERY PLAN output. These tests fail if a schema change, a dropped index,
or a rewritten query stops using them — which is how storage performance
regresses silently.

Note on reading plans: `SCAN t USING INDEX i` is *good* — it is an ordered
index walk that satisfies ORDER BY without sorting. What we forbid is a bare
table scan and an avoidable `USE TEMP B-TREE`.

Each case mirrors a query production actually runs; see docs/STORAGE_WORKLOAD.md.
"""
import pytest

from capman.storage.timeline import TimelineDB


@pytest.fixture
async def db(tmp_path):
    d = TimelineDB(str(tmp_path / "t.db"))
    await d.migrate()
    yield d
    await d.close()


async def _plan(db, sql: str) -> str:
    async with db._db.execute("EXPLAIN QUERY PLAN " + sql) as cur:
        return " | ".join(r["detail"] for r in await cur.fetchall())


# (label, sql, index that must appear, sort allowed?)
CASES = [
    (
        "routes/sessions.py — session list",
        "SELECT * FROM sessions ORDER BY started_at DESC LIMIT 20",
        "idx_sessions_started", False,
    ),
    (
        "routes/chat.py — recent sessions joined to analyses",
        "SELECT s.id FROM sessions s JOIN session_analyses sa ON sa.session_id = s.id "
        "ORDER BY s.started_at DESC LIMIT 8",
        "idx_sessions_started", False,
    ),
    (
        "routes/chat.py — top triples by confidence",
        "SELECT * FROM knowledge_triples ORDER BY confidence DESC, last_observed DESC LIMIT 30",
        "idx_triples_conf", False,
    ),
    (
        "timeline.py — analysis queue",
        "SELECT id FROM sessions WHERE analyzed = 0 AND ended_at IS NOT NULL ORDER BY started_at",
        "idx_sessions_pending", False,
    ),
    (
        "timeline.py — triple upsert conflict target",
        "SELECT id FROM knowledge_triples WHERE subject='a' AND predicate='b' AND object='c'",
        "idx_triples_spo", False,
    ),
    (
        "routes/chat.py — recent events of one type",
        "SELECT payload, ts FROM events WHERE type = 'url_visit' ORDER BY ts DESC LIMIT 40",
        "idx_events_type", False,
    ),
    (
        "routes/chat.py — 24h activity rollup",
        "SELECT type, COUNT(*) c FROM events WHERE ts > 1 GROUP BY type",
        "idx_events_type", False,
    ),
    # These two sort unavoidably and we accept it:
    #  - an IN-list forces merging several index ranges before ORDER BY ts
    #  - GROUP BY on a computed date() expression cannot be satisfied by an index
    (
        "routes/sessions.py — session detail events (sort accepted)",
        "SELECT * FROM events WHERE session_id = 'x' AND type IN ('file_open','code_diff') ORDER BY ts",
        "idx_events_session_type", True,
    ),
    (
        "routes/chat.py — per-day observed span (sort accepted)",
        "SELECT date(ts,'unixepoch','localtime') d, MIN(ts), MAX(ts) FROM events "
        "WHERE ts > 1 GROUP BY d ORDER BY d DESC LIMIT 7",
        "idx_events_ts", True,
    ),
]


@pytest.mark.parametrize(
    "label,sql,index,sort_ok", CASES, ids=[c[0] for c in CASES]
)
async def test_query_uses_its_index(db, label, sql, index, sort_ok):
    plan = await _plan(db, sql)
    assert index in plan, f"{label}\n  plan: {plan}\n  expected index {index} unused"
    if not sort_ok:
        assert "TEMP B-TREE" not in plan, f"{label}\n  plan: {plan}\n  regressed into a sort"


async def test_no_unused_indexes_on_events(db):
    """events is the hot write path — every index on it must be earning its keep.

    Guards against re-adding the (ts, type) index that measurement showed the
    planner never chooses. See the note in migrations/002_indexes.sql.
    """
    async with db._db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='events' AND name LIKE 'idx_%'"
    ) as cur:
        names = {r[0] for r in await cur.fetchall()}
    assert names == {"idx_events_ts", "idx_events_session", "idx_events_type",
                     "idx_events_session_type"}, f"unexpected index set on events: {names}"
