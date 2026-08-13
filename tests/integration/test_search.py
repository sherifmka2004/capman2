"""Hybrid search: FTS5 escaping, lexical recall, RRF fusion, backfill."""
import time

import pytest

from capman.events import Event, EventType
from capman.storage.backfill import backfill_documents
from capman.storage.search import SearchIndex, escape_fts_query, rrf_fuse
from capman.storage.timeline import TimelineDB


@pytest.fixture
async def db(tmp_path):
    d = TimelineDB(str(tmp_path / "t.db"))
    await d.migrate()
    yield d
    await d.close()


# --------------------------------------------------------------------------
# Query escaping — a raw question is not a valid FTS5 MATCH expression
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "error: connection refused",
    'what about "quoted" text?',
    "foo AND bar OR NOT baz",
    "a * b ^ c (d)",
    "trailing -",
])
async def test_escaped_queries_are_valid_fts_syntax(db, raw):
    """Unescaped punctuation raises `fts5: syntax error`. Nothing may reach MATCH raw."""
    match = escape_fts_query(raw)
    if not match:
        return
    async with db._db.execute(
        "SELECT count(*) FROM documents_fts WHERE documents_fts MATCH ?", (match,)
    ) as cur:
        await cur.fetchone()  # must not raise


def test_escape_drops_stopwords_but_keeps_signal():
    out = escape_fts_query("how did I fix the ECONNREFUSED error")
    assert '"ECONNREFUSED"' in out
    assert '"the"' not in out
    assert " OR " in out


def test_escape_returns_empty_for_meaningless_input():
    assert escape_fts_query("") == ""
    assert escape_fts_query("   ") == ""
    assert escape_fts_query("the and of") == ""


# --------------------------------------------------------------------------
# RRF
# --------------------------------------------------------------------------

def test_rrf_rewards_appearing_in_both_rankers():
    """A document both rankers found beats one only a single ranker found,
    even when the latter is ranked first by that one ranker."""
    scores = rrf_fuse([["solo", "shared"], ["shared"]])
    assert scores["shared"] > scores["solo"]


def test_rrf_prefers_higher_ranks():
    scores = rrf_fuse([["first", "second", "third"]])
    assert scores["first"] > scores["second"] > scores["third"]


def test_rrf_handles_a_single_empty_ranker():
    scores = rrf_fuse([["a", "b"], []])
    assert list(sorted(scores, key=scores.get, reverse=True)) == ["a", "b"]


# --------------------------------------------------------------------------
# Lexical recall — the queries that pure vector search cannot answer
# --------------------------------------------------------------------------

async def _seed_events(db):
    rows = [
        (EventType.SHELL_COMMAND, {"command": "git bisect start HEAD v1.2.3"}),
        (EventType.SHELL_COMMAND, {"command": "sudo vim /etc/nginx/nginx.conf"}),
        (EventType.SHELL_COMMAND, {"command": "uv run pytest -k storage"}),
        (EventType.URL_VISIT, {"url": "https://github.com/foo/bar/issues/42",
                               "title": "ECONNREFUSED on startup"}),
        (EventType.MOUSE_SCROLL, {"label": "should_not_be_indexed"}),
        (EventType.MOUSE_HEATMAP_TICK, {"label": "should_not_be_indexed"}),
    ]
    for i, (t, payload) in enumerate(rows):
        e = Event(type=t, app="Terminal", payload=payload)
        e.ts = time.time() + i
        await db.queue_event(e, "s1")
    await db.flush()


@pytest.mark.parametrize("query,expect", [
    ("git bisect", "git bisect"),
    ("/etc/nginx/nginx.conf", "nginx.conf"),        # full path kept as one token
    ("nginx.conf", "nginx.conf"),                    # ...and reachable by segment
    ("ECONNREFUSED", "ECONNREFUSED"),
    ("github.com", "github.com"),                    # host reachable inside a URL
    ("pytest", "pytest"),
])
async def test_exact_recall(db, query, expect):
    await _seed_events(db)
    hits = await SearchIndex(db).event_search(query, limit=5)
    assert hits, f"no hit for {query!r} — this is the class of query vector search misses"
    joined = " ".join(h["text"] for h in hits)
    assert expect.lower() in joined.lower().replace("«", "").replace("»", "")


async def test_contentless_event_types_are_not_indexed(db):
    await _seed_events(db)
    assert await SearchIndex(db).event_search("should_not_be_indexed") == []


async def test_deleting_an_event_removes_it_from_the_index(db):
    await _seed_events(db)
    assert await SearchIndex(db).event_search("ECONNREFUSED")
    await db._db.execute("DELETE FROM events WHERE type = 'url_visit'")
    await db._db.commit()
    assert await SearchIndex(db).event_search("ECONNREFUSED") == []


# --------------------------------------------------------------------------
# Documents + fusion
# --------------------------------------------------------------------------

async def test_documents_are_searchable_and_kind_filtered(db):
    await db.upsert_document("page:1", "page", "Postgres connection pooling with pgbouncer",
                             ts=time.time(), title="Pooling", uri="https://example.com/pg")
    await db.upsert_document("node:1", "node", "Nginx reverse proxy configuration notes",
                             ts=time.time(), title="Nginx notes")

    idx = SearchIndex(db)
    assert [h["id"] for h in await idx.keyword_search("pgbouncer")] == ["page:1"]
    assert await idx.keyword_search("pgbouncer", kinds=["node"]) == []
    assert [h["id"] for h in await idx.keyword_search("nginx", kinds=["node"])] == ["node:1"]


async def test_upsert_document_updates_the_index(db):
    await db.upsert_document("page:1", "page", "original body", ts=time.time())
    idx = SearchIndex(db)
    assert await idx.keyword_search("original")

    await db.upsert_document("page:1", "page", "replacement body", ts=time.time())
    assert await idx.keyword_search("original") == []
    assert await idx.keyword_search("replacement")


async def test_hybrid_search_works_without_any_embeddings(db):
    """Keyword-only must be a clean degradation, not an error."""
    await db.upsert_document("page:1", "page", "kubernetes ingress troubleshooting",
                             ts=time.time(), title="k8s")
    hits = await SearchIndex(db).hybrid_search("kubernetes ingress")
    assert [h["id"] for h in hits] == ["page:1"]
    assert hits[0]["matched_by"] == "keyword"


class FakeVectorIndex:
    """Stands in for VectorIndex so fusion can be tested without the model."""

    def __init__(self, hits):
        self._hits = hits

    async def count(self):
        return len(self._hits)

    async def search(self, query, kinds=None, limit=60):
        if kinds:
            return [h for h in self._hits if h["type"] in kinds]
        return list(self._hits)


async def test_hybrid_search_fuses_both_rankers(db):
    await db.upsert_document("page:1", "page", "alpha document", ts=time.time())
    await db.upsert_document("page:2", "page", "beta document", ts=time.time())

    vec = FakeVectorIndex([{"id": "page:2", "type": "page", "title": "beta",
                            "url": "", "score": 0.9, "text": "beta document"}])
    hits = await SearchIndex(db, vec).hybrid_search("document")
    by_id = {h["id"]: h for h in hits}
    assert by_id["page:2"]["matched_by"] == "both"
    assert by_id["page:1"]["matched_by"] == "keyword"
    # Agreement between rankers must outrank a single-ranker hit.
    assert by_id["page:2"]["score"] > by_id["page:1"]["score"]


# --------------------------------------------------------------------------
# Backfill
# --------------------------------------------------------------------------

async def test_backfill_indexes_pre_existing_events_and_is_idempotent(db):
    # Insert events directly, bypassing triggers, to simulate pre-migration data.
    await db._db.execute("DROP TRIGGER events_ai")
    await _seed_events(db)
    assert await SearchIndex(db).event_search("bisect") == []

    first = await backfill_documents(db)
    assert first["events_fts"] > 0
    assert await SearchIndex(db).event_search("bisect")

    second = await backfill_documents(db)
    assert second["events_fts"] == 0, "backfill must not double-index"


async def test_backfill_recovers_session_analyses(db):
    await db._db.execute(
        "INSERT INTO sessions (id, started_at, event_count) VALUES ('s9', 1.0, 3)")
    await db._db.execute(
        "INSERT INTO session_analyses (session_id, problem_statement, approach_description,"
        " methodology_tags, analyzed_at) VALUES ('s9', 'Debugged a flaky hydration error',"
        " 'Bisected the render path', '[\"bisect\"]', 2.0)")
    await db._db.commit()
