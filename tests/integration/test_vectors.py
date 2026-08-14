"""In-database vector index: durability, fallback, and agreement between paths."""
import time

import pytest

from capman.storage.timeline import TimelineDB
from capman.storage.vectors import VEC_TABLE, VectorIndex

pytest.importorskip("model2vec")

DOCS = [
    ("page:1", "page", "Terraform state locking with a DynamoDB backend", "TF locking"),
    ("page:2", "page", "Kubernetes ingress controller TLS termination", "k8s ingress"),
    ("node:1", "node", "Debugging a React hydration mismatch in SSR", "React SSR"),
    ("node:2", "node", "Postgres connection pooling with pgbouncer", "PG pooling"),
]


@pytest.fixture
async def db(tmp_path):
    d = TimelineDB(str(tmp_path / "t.db"))
    await d.migrate()
    for i, (doc_id, kind, body, title) in enumerate(DOCS):
        await d.upsert_document(doc_id, kind, body, ts=time.time() + i, title=title)
    yield d
    await d.close()


async def test_embeddings_are_persisted_as_durable_blobs(db):
    """documents.embedding is the source of truth — the ANN table is derived."""
    index = VectorIndex(db)
    assert await index.index_documents() == len(DOCS)
    assert await index.count() == len(DOCS)

    async with db._db.execute(
        "SELECT embedding FROM documents WHERE id = 'page:1'") as cur:
        blob = (await cur.fetchone())[0]
    from capman.storage.embedding import EMBEDDING_DIM, from_blob
    assert blob is not None
    assert len(from_blob(blob)) == EMBEDDING_DIM


async def test_indexing_is_incremental(db):
    index = VectorIndex(db)
    assert await index.index_documents() == len(DOCS)
    assert await index.index_documents() == 0, "already-embedded rows must be skipped"

    await db.upsert_document("page:3", "page", "Redis eviction policies", ts=time.time())
    assert await index.index_documents() == 1


async def test_search_ranks_the_relevant_document_first(db):
    index = VectorIndex(db)
    await index.index_documents()
    hits = await index.search("how do I lock terraform state", limit=2)
    assert hits and hits[0]["id"] == "page:1"


async def test_kind_filter(db):
    index = VectorIndex(db)
    await index.index_documents()
    hits = await index.search("terraform", kinds=["node"], limit=5)
    assert all(h["type"] == "node" for h in hits)


async def test_bruteforce_fallback_matches_the_indexed_ranking(db):
    """If sqlite-vec is unavailable the answer must still be right."""
    index = VectorIndex(db)
    await index.index_documents()
    query = "how do I lock terraform state"

    indexed = await index.search(query, limit=4)
    index._extension_ok = False               # simulate a missing extension
    brute = await index.search(query, limit=4)

    assert [h["id"] for h in indexed] == [h["id"] for h in brute]


async def test_scores_are_on_the_same_scale_in_both_paths(db):
    """vec0 defaults to L2; we force cosine so scores don't jump on fallback."""
    index = VectorIndex(db)
    await index.index_documents()
    query = "kubernetes ingress"

    indexed = await index.search(query, limit=1)
    index._extension_ok = False
    brute = await index.search(query, limit=1)

    assert indexed[0]["id"] == brute[0]["id"]
    assert abs(indexed[0]["score"] - brute[0]["score"]) < 0.05


async def test_rebuild_restores_the_index_without_re_embedding(db):
    """The recovery path if sqlite-vec's storage format ever changes."""
    index = VectorIndex(db)
    await index.index_documents()
    if not await index._ensure_extension():
        pytest.skip("sqlite-vec not available on this platform")

    await db._db.execute(f"DELETE FROM {VEC_TABLE}")
    await db._db.commit()
    async with db._db.execute(f"SELECT COUNT(*) FROM {VEC_TABLE}") as cur:
        assert (await cur.fetchone())[0] == 0

    assert await VectorIndex(db).rebuild() == len(DOCS)
    hits = await VectorIndex(db).search("terraform state", limit=1)
    assert hits[0]["id"] == "page:1"


async def test_hybrid_search_prefers_the_in_database_index(db):
    """Once documents carry embeddings, the legacy external store is not consulted."""
    from capman.storage.search import SearchIndex
    await VectorIndex(db).index_documents()

    class ExplodingLegacyStore:
        def search(self, *a, **k):
            raise AssertionError("legacy vector store should not be used")

    hits = await SearchIndex(db, ExplodingLegacyStore()).hybrid_search("terraform state locking")
    assert hits and hits[0]["id"] == "page:1"
