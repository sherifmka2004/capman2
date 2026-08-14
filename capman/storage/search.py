"""Hybrid retrieval: BM25 (SQLite FTS5) fused with vector similarity via RRF.

Why hybrid: capman2's corpus is shell commands, file paths, URLs, error strings
and page text. Dense vectors are good at "what was I doing about auth last
week" and structurally bad at "find `ECONNREFUSED`" or "find
`/etc/nginx/nginx.conf`" — those are lexical matches. BM25 is good at exactly
the opposite. Reciprocal Rank Fusion lets us take both without tuning a weight.

RRF: score(d) = sum over rankers of 1 / (k + rank(d)), k = 60. It fuses by
*rank* rather than by score, so it needs no normalisation between a BM25 score
and a cosine distance — which is the whole reason it is used here.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable

logger = logging.getLogger(__name__)

RRF_K = 60
#: How deep to go in each individual ranker before fusing.
CANDIDATE_DEPTH = 60

#: Kinds stored in `documents`.
ALL_KINDS = ("page", "doc", "ocr", "session", "playbook", "node")

_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-./:@]+")

# Words that carry no lexical signal but would otherwise dominate an OR query.
_STOPWORDS = frozenset("""
a an the and or but if then than that this these those of in on at to for with
from by as is are was were be been being do does did doing have has had having
i me my we our you your it its they them their what which who whom how why when
where can could should would will shall may might must not no nor so such about
""".split())


def escape_fts_query(question: str, *, mode: str = "OR") -> str:
    """Turn arbitrary user text into a valid FTS5 MATCH expression.

    FTS5 MATCH takes a query *language*, not a string: a bare question like
    `error: connection refused` raises `fts5: syntax error near ":"`. Every
    token is therefore extracted and double-quoted, which both escapes it and
    makes it a literal phrase.

    Returns "" when nothing searchable survives — callers must treat that as
    "no keyword ranker", not as "match everything".
    """
    tokens = [t for t in _FTS_TOKEN_RE.findall(question or "")
              if t.lower() not in _STOPWORDS]
    kept = [t for t in tokens if len(t) > 1]
    if not kept:
        # Fall back on length only, never on stopword-ness: a query of nothing
        # but stopwords carries no signal and must yield no keyword ranker,
        # whereas a short command like "ls" or "cd" is meaningful.
        kept = tokens
    if not kept:
        return ""
    # Escape embedded double quotes by doubling them, per FTS5 string literals.
    quoted = ['"' + t.replace('"', '""') + '"' for t in kept]
    joiner = f" {mode} "
    return joiner.join(quoted)


def rrf_fuse(rankings: Iterable[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Fuse ranked ID lists into {id: score}. Rank is 1-based."""
    scores: dict[str, float] = {}
    for ranked in rankings:
        for rank, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


class SearchIndex:
    """Hybrid search over `documents`: BM25 from FTS5, semantics from vectors.

    Both rankers read the same table, so they agree on ids by construction and
    RRF can actually fuse them. `vector_index` is injectable for tests.
    """

    def __init__(self, db, vector_index=None):
        self._db = db                     # TimelineDB
        self._vector_index = vector_index

    # -- individual rankers -------------------------------------------------

    async def keyword_search(
        self, query: str, kinds: Iterable[str] | None = None, limit: int = CANDIDATE_DEPTH
    ) -> list[dict[str, Any]]:
        """BM25 over documents_fts. Lower bm25() is a better match."""
        match = escape_fts_query(query)
        if not match:
            return []

        sql = [
            "SELECT d.id, d.kind, d.title, d.uri, d.ts, d.session_id, d.ref_id,",
            "       snippet(documents_fts, 2, '«', '»', '…', 24) AS snippet,",
            "       bm25(documents_fts) AS score",
            "FROM documents_fts JOIN documents d ON d.rowid = documents_fts.rowid",
            "WHERE documents_fts MATCH ?",
        ]
        params: list[Any] = [match]
        kinds = list(kinds) if kinds else []
        if kinds:
            sql.append(f"AND d.kind IN ({','.join('?' * len(kinds))})")
            params.extend(kinds)
        sql.append("ORDER BY bm25(documents_fts) LIMIT ?")
        params.append(limit)

        try:
            async with self._db._db.execute(" ".join(sql), params) as cur:
                rows = await cur.fetchall()
        except Exception as e:
            logger.warning("Keyword search failed for %r: %s", match, e)
            return []

        return [
            {
                "id": r["id"], "type": r["kind"], "title": r["title"], "url": r["uri"],
                "ts": r["ts"], "session_id": r["session_id"], "ref_id": r["ref_id"],
                "text": r["snippet"], "score": r["score"],
            }
            for r in rows
        ]

    async def event_search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """BM25 over raw event text — exact commands, paths, URLs, form labels."""
        match = escape_fts_query(query)
        if not match:
            return []
        try:
            async with self._db._db.execute(
                "SELECT e.id, e.type, e.ts, e.app, e.session_id,"
                "       snippet(events_fts, 0, '«', '»', '…', 24) AS snippet,"
                "       bm25(events_fts) AS score "
                "FROM events_fts JOIN events e ON e.rowid = events_fts.rowid "
                "WHERE events_fts MATCH ? ORDER BY bm25(events_fts) LIMIT ?",
                (match, limit),
            ) as cur:
                rows = await cur.fetchall()
        except Exception as e:
            logger.warning("Event search failed for %r: %s", match, e)
            return []
        return [
            {"id": r["id"], "type": r["type"], "ts": r["ts"], "app": r["app"],
             "session_id": r["session_id"], "text": r["snippet"], "score": r["score"]}
            for r in rows
        ]

    async def semantic_search(
        self, query: str, kinds: Iterable[str] | None = None, limit: int = CANDIDATE_DEPTH
    ) -> list[dict[str, Any]]:
        """Vector ranker over `documents.embedding`."""
        kinds = list(kinds) if kinds else None
        try:
            index = self._vector_index
            if index is None:
                from capman.storage.vectors import VectorIndex
                index = VectorIndex(self._db)
            if await index.count() == 0:
                return []
            return await index.search(query, kinds, limit=limit)
        except Exception as e:
            logger.warning("Semantic search failed: %s", e)
            return []

    # -- fusion -------------------------------------------------------------

    async def hybrid_search(
        self,
        query: str,
        kinds: Iterable[str] | None = None,
        top_k: int = 10,
        depth: int = CANDIDATE_DEPTH,
    ) -> list[dict[str, Any]]:
        """Rank by RRF over the keyword and vector rankers.

        Degrades cleanly: if either ranker returns nothing (no FTS content yet,
        or no vector store configured) the other still produces results.
        """
        kinds = list(kinds) if kinds else None

        kw = await self.keyword_search(query, kinds, limit=depth)
        vec = await self.semantic_search(query, kinds, limit=depth)

        by_id: dict[str, dict[str, Any]] = {}
        for hit in vec:            # vector first so keyword snippets win on merge
            by_id[hit["id"]] = dict(hit)
        for hit in kw:
            if hit["id"] in by_id:
                by_id[hit["id"]].update({k: v for k, v in hit.items() if v not in (None, "")})
            else:
                by_id[hit["id"]] = dict(hit)

        fused = rrf_fuse([[h["id"] for h in kw], [h["id"] for h in vec]])

        out: list[dict[str, Any]] = []
        for doc_id, score in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]:
            hit = by_id.get(doc_id, {"id": doc_id})
            hit["score"] = round(score, 6)
            hit["matched_by"] = (
                "both" if any(h["id"] == doc_id for h in kw) and any(h["id"] == doc_id for h in vec)
                else "keyword" if any(h["id"] == doc_id for h in kw)
                else "vector"
            )
            out.append(hit)
        return out
