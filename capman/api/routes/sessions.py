"""GET /sessions — list and inspect sessions."""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("")
async def list_sessions(request: Request, limit: int = 20, offset: int = 0):
    db = request.app.state.db
    if db is None:
        return {"sessions": []}
    async with db._db.execute(
        """SELECT id, started_at, ended_at, dominant_app, primary_domain,
                  event_count, analyzed
           FROM sessions ORDER BY started_at DESC LIMIT ? OFFSET ?""",
        (limit, offset),
    ) as cur:
        rows = await cur.fetchall()
    return {"sessions": [dict(r) for r in rows]}


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request):
    db = request.app.state.db
    if db is None:
        return {}
    async with db._db.execute(
        "SELECT * FROM sessions WHERE id = ?", (session_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Session not found")

    async with db._db.execute(
        "SELECT * FROM session_analyses WHERE session_id = ?", (session_id,)
    ) as cur:
        analysis_row = await cur.fetchone()

    return {
        "session": dict(row),
        "analysis": dict(analysis_row) if analysis_row else None,
    }
