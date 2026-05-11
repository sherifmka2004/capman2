"""GET /query — semantic search over sessions and knowledge nodes."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/query", tags=["query"])


@router.get("")
async def semantic_query(
    q: str = Query(..., description="Search query"),
    top_k: int = Query(10, ge=1, le=50),
    request: Request = None,
):
    try:
        from capman.storage.vector import VectorStore
        config = request.app.state.config
        chroma_path = config.get("storage", {}).get("chroma_path", "~/.capman/chroma")
        vs = VectorStore(chroma_path)
        results = vs.search(q, top_k=top_k)
        return {
            "query": q,
            "results": results,
            "total": len(results),
        }
    except Exception as e:
        logger.error("Query failed: %s", e)
        return {"query": q, "results": [], "total": 0, "error": str(e)}
