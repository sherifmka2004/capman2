"""GET /export/training-data — export analyzed sessions as JSONL for AI training."""
from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRouter

router = APIRouter(prefix="/export", tags=["export"])


@router.get("/training-data")
async def export_training_data(request: Request, format: str = "jsonl", limit: int = 1000):
    """
    Export all analyzed sessions as structured JSONL suitable for AI/LLM fine-tuning.
    Each line is a self-contained JSON record with full chain-of-thought, triples, and playbook.
    """
    db = request.app.state.db
    if db is None:
        return {"error": "database not available"}

    async with db._db.execute(
        """SELECT sa.session_id, sa.problem_statement, sa.approach_description,
                  sa.methodology_tags, sa.knowledge_applied, sa.knowledge_acquired,
                  sa.chain_of_thought, sa.triples, sa.confidence, sa.model_used,
                  sa.analyzed_at,
                  s.dominant_app, s.primary_domain, s.started_at, s.ended_at, s.event_count
           FROM session_analyses sa
           JOIN sessions s ON s.id = sa.session_id
           WHERE sa.problem_statement != '' AND sa.problem_statement != '[LLM not configured]'
           ORDER BY sa.analyzed_at DESC
           LIMIT ?""",
        (limit,),
    ) as cur:
        rows = await cur.fetchall()

    # Fetch playbooks indexed by session_id for joining
    async with db._db.execute("SELECT session_id, title, domain, symptoms, root_cause, fix, diagnostic_steps, reusability_score FROM playbooks") as cur:
        pb_rows = await cur.fetchall()
    playbooks_by_session = {r["session_id"]: dict(r) for r in pb_rows}

    def _records():
        for row in rows:
            sid = row["session_id"]
            cot = None
            try:
                cot = json.loads(row["chain_of_thought"]) if row["chain_of_thought"] else None
            except Exception:
                pass
            triples = []
            try:
                triples = json.loads(row["triples"]) if row["triples"] else []
            except Exception:
                pass
            pb = playbooks_by_session.get(sid)
            if pb:
                pb = {
                    "title": pb["title"],
                    "domain": pb["domain"],
                    "symptoms": json.loads(pb["symptoms"] or "[]"),
                    "root_cause": pb["root_cause"],
                    "fix": json.loads(pb["fix"] or "[]"),
                    "diagnostic_steps": json.loads(pb["diagnostic_steps"] or "[]"),
                    "reusability_score": pb["reusability_score"],
                }
            record = {
                "session_id": sid,
                "dominant_app": row["dominant_app"],
                "primary_domain": row["primary_domain"],
                "duration_s": (row["ended_at"] or row["started_at"]) - row["started_at"],
                "event_count": row["event_count"],
                "problem_statement": row["problem_statement"],
                "approach_description": row["approach_description"],
                "methodology_tags": json.loads(row["methodology_tags"] or "[]"),
                "knowledge_applied": json.loads(row["knowledge_applied"] or "[]"),
                "knowledge_acquired": json.loads(row["knowledge_acquired"] or "[]"),
                "confidence": row["confidence"],
                "chain_of_thought": cot,
                "triples": triples,
                "playbook": pb,
                "analyzed_at": row["analyzed_at"],
            }
            yield json.dumps(record, ensure_ascii=False) + "\n"

    if format == "json":
        all_records = [json.loads(line) for line in _records()]
        return {"sessions": all_records, "total": len(all_records)}

    return StreamingResponse(
        _records(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="capman_training_data.jsonl"'},
    )
