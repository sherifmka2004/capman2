"""
POST /context/suggest  — Active context retrieval for IDEs / coding agents.

Given a free-text task description, returns:
  - Matching troubleshooting playbooks (most actionable)
  - Relevant past sessions (similar problems you've solved)
  - Related knowledge graph nodes
  - Page content excerpts that match

Designed to be embedded in an IDE's "I'm about to work on X" trigger so
the LLM gets your prior methodology as part of its system prompt — turning
capman2 from passive memory into active assistance.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/context", tags=["context"])


class ContextRequest(BaseModel):
    task: str                     # Free-text description of what user is about to do
    domain: str | None = None     # Optional filter
    top_k: int = 5


@router.post("/suggest")
async def suggest_context(body: ContextRequest, request: Request):
    """Returns prior knowledge that could help with the given task."""
    config = request.app.state.config
    db = request.app.state.db

    out = {
        "task": body.task,
        "playbooks":          [],
        "similar_sessions":   [],
        "related_concepts":   [],
        "page_excerpts":      [],
        "knowledge_gaps":     [],
    }

    try:
        from capman.storage.vector import VectorStore
        chroma_path = config.get("storage", {}).get("chroma_path", "~/.capman/chroma")
        vs = VectorStore(chroma_path)

        # 1. Most relevant playbooks (the actionable methodologies)
        pb_hits = vs.search(body.task, top_k=body.top_k, types=["playbook"])
        for h in pb_hits:
            pb_id = h["metadata"].get("playbook_id", "")
            if not pb_id or not db:
                continue
            async with db._db.execute(
                "SELECT * FROM playbooks WHERE id = ?", (pb_id,)
            ) as cur:
                row = await cur.fetchone()
            if row:
                out["playbooks"].append({
                    "id":              row["id"],
                    "title":           row["title"],
                    "domain":          row["domain"],
                    "score":           h["score"],
                    "symptoms":        json.loads(row["symptoms"] or "[]"),
                    "context_signals": json.loads(row["context_signals"] or "[]"),
                    "diagnostic_steps": json.loads(row["diagnostic_steps"] or "[]"),
                    "root_cause":      row["root_cause"],
                    "fix":             json.loads(row["fix"] or "[]"),
                    "verification":    json.loads(row["verification"] or "[]"),
                    "references":      json.loads(row["references_json"] or "[]"),
                })

        # 2. Similar past sessions (workflow patterns)
        sess_hits = vs.search(body.task, top_k=body.top_k, types=["session"])
        for h in sess_hits:
            sid = h["metadata"].get("session_id", "")
            if not sid or not db:
                continue
            async with db._db.execute(
                "SELECT problem_statement, approach_description, methodology_tags, chain_of_thought FROM session_analyses WHERE session_id = ?",
                (sid,),
            ) as cur:
                row = await cur.fetchone()
            if row:
                cot_data = json.loads(row["chain_of_thought"] or "{}") if row["chain_of_thought"] else {}
                out["similar_sessions"].append({
                    "session_id": sid,
                    "score": h["score"],
                    "problem_statement": row["problem_statement"],
                    "approach": row["approach_description"],
                    "methodology_pattern": cot_data.get("methodology_pattern", ""),
                    "tags": json.loads(row["methodology_tags"] or "[]"),
                })

        # 3. Related concept nodes
        node_hits = vs.search(body.task, top_k=body.top_k, types=["knowledge_node"])
        for h in node_hits:
            out["related_concepts"].append({
                "title": h["title"],
                "score": h["score"],
                "summary": h["text"][:300],
            })

        # 4. Page content excerpts you've read
        page_hits = vs.search(body.task, top_k=body.top_k, types=["page"])
        for h in page_hits:
            out["page_excerpts"].append({
                "url": h.get("url", ""),
                "title": h.get("title", ""),
                "score": h["score"],
                "excerpt": h["text"][:600],
            })
    except Exception as e:
        logger.error("Vector retrieval failed: %s", e)

    # 5. Active knowledge gaps that might be relevant
    if db:
        try:
            top_gaps = await db.get_top_knowledge_gaps(limit=10)
            task_lower = body.task.lower()
            for g in top_gaps:
                if any(word in g["concept"] for word in task_lower.split() if len(word) > 3):
                    out["knowledge_gaps"].append({
                        "concept": g["concept"],
                        "domain": g["domain"],
                        "lookup_count": g["lookup_count"],
                        "examples": json.loads(g["query_examples"] or "[]")[:3],
                    })
        except Exception as e:
            logger.debug("Gap fetch failed: %s", e)

    return out


@router.get("/health")
async def context_health(request: Request):
    config = request.app.state.config
    db = request.app.state.db
    out = {"status": "ok"}
    if db:
        async with db._db.execute("SELECT COUNT(*) as n FROM playbooks") as cur:
            out["playbook_count"] = (await cur.fetchone())["n"]
        async with db._db.execute("SELECT COUNT(*) as n FROM knowledge_gaps") as cur:
            out["knowledge_gap_count"] = (await cur.fetchone())["n"]
    return out
