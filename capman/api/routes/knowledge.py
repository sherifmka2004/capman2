"""GET /knowledge — browse knowledge graph nodes, gaps, and playbooks."""
from __future__ import annotations

import json

from fastapi import APIRouter, Request, HTTPException

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


@router.get("/gaps")
async def list_knowledge_gaps(request: Request, top: int = 20):
    """Top concepts the user repeatedly looks up — sign of unmastered knowledge."""
    db = request.app.state.db
    if db is None:
        return {"gaps": []}
    rows = await db.get_top_knowledge_gaps(limit=top)
    return {
        "gaps": [
            {
                "concept": r["concept"],
                "domain": r["domain"],
                "lookup_count": r["lookup_count"],
                "first_seen": r["first_seen"],
                "last_seen": r["last_seen"],
                "examples": json.loads(r["query_examples"] or "[]"),
                "session_count": len(json.loads(r["sessions"] or "[]")),
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.get("/playbooks")
async def list_playbooks(request: Request, domain: str | None = None, limit: int = 50):
    """All extracted troubleshooting playbooks. Filter by ?domain=networking|react|..."""
    db = request.app.state.db
    if db is None:
        return {"playbooks": []}
    rows = await db.get_playbooks(domain=domain, limit=limit)
    return {
        "playbooks": [
            {
                "id": r["id"],
                "title": r["title"],
                "domain": r["domain"],
                "symptoms": json.loads(r["symptoms"] or "[]"),
                "diagnostic_step_count": len(json.loads(r["diagnostic_steps"] or "[]")),
                "root_cause": r["root_cause"],
                "reusability_score": r["reusability_score"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.get("/playbooks/{playbook_id}")
async def get_playbook(playbook_id: str, request: Request):
    db = request.app.state.db
    if db is None:
        return {}
    async with db._db.execute(
        "SELECT * FROM playbooks WHERE id = ?", (playbook_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Playbook not found")
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "title": row["title"],
        "domain": row["domain"],
        "symptoms": json.loads(row["symptoms"] or "[]"),
        "context_signals": json.loads(row["context_signals"] or "[]"),
        "diagnostic_steps": json.loads(row["diagnostic_steps"] or "[]"),
        "root_cause": row["root_cause"],
        "fix": json.loads(row["fix"] or "[]"),
        "verification": json.loads(row["verification"] or "[]"),
        "references": json.loads(row["references_json"] or "[]"),
        "related_playbooks": json.loads(row["related_playbooks"] or "[]"),
        "reusability_score": row["reusability_score"],
        "created_at": row["created_at"],
    }
