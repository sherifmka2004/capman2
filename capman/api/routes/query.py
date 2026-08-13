"""GET /query — hybrid search (BM25 + vector, fused with RRF) over captured knowledge."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Query

from capman.storage.search import ALL_KINDS, SearchIndex

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/query", tags=["query"])


@router.get("")
async def hybrid_query(
    q: str = Query(..., description="Search query"),
    top_k: int = Query(10, ge=1, le=50),
    kinds: str | None = Query(None, description=f"Comma-separated subset of {','.join(ALL_KINDS)}"),
    mode: str = Query("hybrid", pattern="^(hybrid|keyword|vector)$"),
    request: Request = None,
):
    """Search captured knowledge.

    `hybrid` (default) fuses BM25 and vector rankings — use it unless you have
    a reason not to. Pure `vector` misses exact strings (commands, paths, error
    codes); pure `keyword` misses paraphrases.
    """
    config = request.app.state.config
    db = request.app.state.db
    kind_list = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else None

    try:
        vs = None
        if mode in ("hybrid", "vector"):
            try:
                from capman.storage.vector import get_vector_store
                chroma_path = config.get("storage", {}).get("chroma_path", "~/.capman/chroma")
                vs = get_vector_store(chroma_path)
            except Exception as e:
                # Keyword-only is a usable degradation, not a failure.
                logger.warning("Vector store unavailable, falling back to keyword: %s", e)

        index = SearchIndex(db, vs)
        if mode == "keyword":
            results = await index.keyword_search(q, kind_list, limit=top_k)
        elif mode == "vector":
            results = index.vector_search(q, kind_list, limit=top_k)
        else:
            results = await index.hybrid_search(q, kind_list, top_k=top_k)

        return {"query": q, "mode": mode, "results": results, "total": len(results)}
    except Exception as e:
        logger.error("Query failed: %s", e, exc_info=True)
        return {"query": q, "mode": mode, "results": [], "total": 0, "error": str(e)}


@router.get("/events")
async def event_query(
    q: str = Query(..., description="Exact-recall search over raw event text"),
    limit: int = Query(25, ge=1, le=200),
    request: Request = None,
):
    """Keyword search over raw events — shell commands, URLs, paths, form labels.

    This is the lookup that vector search cannot do: `ECONNREFUSED`,
    `/etc/nginx/nginx.conf`, `git bisect`.
    """
    db = request.app.state.db
    try:
        results = await SearchIndex(db).event_search(q, limit=limit)
        return {"query": q, "results": results, "total": len(results)}
    except Exception as e:
        logger.error("Event query failed: %s", e, exc_info=True)
        return {"query": q, "results": [], "total": 0, "error": str(e)}
