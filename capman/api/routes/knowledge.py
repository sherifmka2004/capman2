"""GET /knowledge — browse knowledge graph nodes."""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/nodes")
async def list_nodes(request: Request, limit: int = 50):
    try:
        config = request.app.state.config
        knowledge_dir = config.get("storage", {}).get("knowledge_dir", "~/.capman/knowledge")
        from capman.knowledge.graph import KnowledgeGraph
        graph = KnowledgeGraph(knowledge_dir=knowledge_dir)
        graph.load()
        nodes = list(graph.nodes.values())[:limit]
        return {
            "nodes": [
                {
                    "id": n.id,
                    "title": n.title,
                    "node_type": n.node_type,
                    "tags": n.tags,
                    "edge_count": len(n.outgoing_edges),
                    "source_sessions": len(n.source_sessions),
                }
                for n in nodes
            ],
            "total": len(graph.nodes),
        }
    except Exception as e:
        return {"nodes": [], "total": 0, "error": str(e)}


@router.get("/triples")
async def list_triples(request: Request, limit: int = 100):
    db = request.app.state.db
    if db is None:
        return {"triples": []}
    async with db._db.execute(
        "SELECT * FROM knowledge_triples ORDER BY last_observed DESC LIMIT ?", (limit,)
    ) as cur:
        rows = await cur.fetchall()
    return {"triples": [dict(r) for r in rows]}
