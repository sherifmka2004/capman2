"""Vector index over `documents`, backed by sqlite-vec with a numpy fallback.

Design rule: **`documents.embedding` is the source of truth; the vec0 virtual
table is a rebuildable index.** sqlite-vec is still pre-v1 (0.1.9 as of writing)
and its storage format may change, so nothing irreplaceable lives inside it. If
the extension fails to load — an unsupported platform, a broken build, a
breaking format change — search degrades to brute force over the durable BLOBs
rather than failing.

That fallback is not a token gesture. At this scale it is genuinely fast: a few
hundred thousand 256-dim float32 vectors is a ~100 MB matmul, single-digit
milliseconds. The ANN index is a convenience, not a requirement.

The vec0 table is created lazily at runtime rather than in a migration, so a
platform without the extension still starts, migrates, and serves keyword
search normally.
"""
from __future__ import annotations

import logging

from capman.storage.embedding import EMBEDDING_DIM, embed, embed_one, from_blob, to_blob

logger = logging.getLogger(__name__)

VEC_TABLE = "vec_documents"


class VectorIndex:
    """Semantic search over `documents`, and the writer that keeps it current."""

    def __init__(self, db):
        self._db = db
        self._extension_ok: bool | None = None

    # -- extension plumbing -------------------------------------------------

    async def _ensure_extension(self) -> bool:
        """Load sqlite-vec and create the index table. Cached per instance."""
        if self._extension_ok is not None:
            return self._extension_ok
        try:
            import sqlite_vec

            await self._db._db.enable_load_extension(True)
            await self._db._db.load_extension(sqlite_vec.loadable_path())
            await self._db._db.enable_load_extension(False)
            await self._db._db.execute(
                # cosine, not the vec0 default of L2: embeddings are
                # normalised, so the ranking is the same either way, but the
                # scores must be on the same scale as the brute-force fallback
                # or callers see them jump when the extension is unavailable.
                f"""CREATE VIRTUAL TABLE IF NOT EXISTS {VEC_TABLE} USING vec0(
                        doc_rowid INTEGER PRIMARY KEY,
                        kind      TEXT PARTITION KEY,
                        embedding float[{EMBEDDING_DIM}] distance_metric=cosine
                    )"""
            )
            await self._db._db.commit()
            self._extension_ok = True
        except Exception as e:
            logger.warning(
                "sqlite-vec unavailable (%s) — falling back to brute-force search "
                "over documents.embedding", e
            )
            self._extension_ok = False
        return self._extension_ok

    # -- writing ------------------------------------------------------------

    async def index_documents(self, limit: int | None = None, batch_size: int = 256) -> int:
        """Embed documents that have no embedding yet. Returns the count."""
        sql = "SELECT rowid, id, kind, title, body FROM documents WHERE embedding IS NULL"
        if limit:
            sql += f" LIMIT {int(limit)}"
        async with self._db._db.execute(sql) as cur:
            rows = await cur.fetchall()
        if not rows:
            return 0

        has_vec = await self._ensure_extension()
        total = 0
        for start in range(0, len(rows), batch_size):
            batch = rows[start:start + batch_size]
            texts = [f"{r['title']}\n\n{r['body']}"[:8000] for r in batch]
            try:
                vectors = embed(texts)
            except Exception as e:
                logger.error("Embedding batch failed: %s", e)
                break

            await self._db._db.executemany(
                "UPDATE documents SET embedding = ? WHERE rowid = ?",
                [(to_blob(v), r["rowid"]) for v, r in zip(vectors, batch)],
            )
            if has_vec:
                # vec0 has no UPSERT ("UPSERT not implemented for virtual
                # table"), so replace explicitly.
                await self._db._db.executemany(
                    f"DELETE FROM {VEC_TABLE} WHERE doc_rowid = ?",
                    [(r["rowid"],) for r in batch],
                )
                await self._db._db.executemany(
                    f"INSERT INTO {VEC_TABLE}(doc_rowid, kind, embedding) VALUES (?, ?, ?)",
                    [(r["rowid"], r["kind"], to_blob(v)) for v, r in zip(vectors, batch)],
                )
            await self._db._db.commit()
            total += len(batch)

        logger.info("Embedded %d documents", total)
        return total

    async def rebuild(self) -> int:
        """Rebuild the ANN index from the durable embeddings.

        This is the recovery path if sqlite-vec's storage format changes: drop
        the virtual table and repopulate from `documents.embedding` without
        re-running the model.
        """
        if not await self._ensure_extension():
            return 0
        await self._db._db.execute(f"DELETE FROM {VEC_TABLE}")
        async with self._db._db.execute(
            "SELECT rowid, kind, embedding FROM documents WHERE embedding IS NOT NULL"
        ) as cur:
            rows = await cur.fetchall()
        if rows:
            await self._db._db.executemany(
                f"INSERT INTO {VEC_TABLE}(doc_rowid, kind, embedding) VALUES (?, ?, ?)",
                [(r["rowid"], r["kind"], r["embedding"]) for r in rows],
            )
        await self._db._db.commit()
        logger.info("Rebuilt vector index from %d durable embeddings", len(rows))
        return len(rows)

    # -- reading ------------------------------------------------------------

    async def search(self, query: str, kinds: list[str] | None = None,
                     limit: int = 60) -> list[dict]:
        try:
            qvec = embed_one(query)
        except Exception as e:
            logger.warning("Query embedding failed: %s", e)
            return []

        if await self._ensure_extension():
            hits = await self._search_vec0(qvec, kinds, limit)
            if hits is not None:
                return hits
        return await self._search_bruteforce(qvec, kinds, limit)

    async def _search_vec0(self, qvec, kinds, limit) -> list[dict] | None:
        params: list = [to_blob(qvec), limit]
        kind_filter = ""
        if kinds:
            kind_filter = f" AND v.kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        try:
            async with self._db._db.execute(
                f"""SELECT d.id, d.kind, d.title, d.uri, d.ts, d.session_id, d.ref_id,
                           substr(d.body, 1, 400) AS text, v.distance
                    FROM {VEC_TABLE} v JOIN documents d ON d.rowid = v.doc_rowid
                    WHERE v.embedding MATCH ? AND k = ?{kind_filter}
                    ORDER BY v.distance""",
                params,
            ) as cur:
                rows = await cur.fetchall()
        except Exception as e:
            logger.warning("vec0 search failed, falling back to brute force: %s", e)
            return None
        return [
            {"id": r["id"], "type": r["kind"], "title": r["title"], "url": r["uri"],
             "ts": r["ts"], "session_id": r["session_id"], "ref_id": r["ref_id"],
             "text": r["text"], "score": round(1.0 - r["distance"], 4)}
            for r in rows
        ]

    async def _search_bruteforce(self, qvec, kinds, limit) -> list[dict]:
        import numpy as np

        sql = ("SELECT rowid, id, kind, title, uri, ts, session_id, ref_id,"
               " substr(body,1,400) AS text, embedding FROM documents"
               " WHERE embedding IS NOT NULL")
        params: list = []
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        async with self._db._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        if not rows:
            return []

        matrix = np.vstack([from_blob(r["embedding"]) for r in rows])
        scores = matrix @ np.asarray(qvec, dtype="float32")   # both are normalised
        order = np.argsort(-scores)[:limit]
        return [
            {"id": rows[i]["id"], "type": rows[i]["kind"], "title": rows[i]["title"],
             "url": rows[i]["uri"], "ts": rows[i]["ts"], "session_id": rows[i]["session_id"],
             "ref_id": rows[i]["ref_id"], "text": rows[i]["text"],
             "score": round(float(scores[i]), 4)}
            for i in order
        ]

    async def count(self) -> int:
        async with self._db._db.execute(
            "SELECT COUNT(*) FROM documents WHERE embedding IS NOT NULL"
        ) as cur:
            return (await cur.fetchone())[0]
