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

    # File operations the user performed during this session
    import json as _json
    file_activity: list[dict] = []
    try:
        async with db._db.execute(
            """SELECT type, ts, payload FROM events
               WHERE session_id = ?
                 AND type IN ('file_open','file_save','file_delete','file_rename','code_diff')
               ORDER BY ts ASC""",
            (session_id,),
        ) as cur:
            for r in await cur.fetchall():
                try:
                    p = _json.loads(r["payload"])
                except Exception:
                    p = {}
                actor = p.get("actor", {}) or {}
                file_activity.append({
                    "type": r["type"], "ts": r["ts"],
                    "path": p.get("path") or p.get("dest_path") or "",
                    "src_path": p.get("src_path", ""),
                    "attribution": p.get("attribution", ""),
                    "actor": actor.get("app") or actor.get("comm") or "",
                    "via_command": p.get("via_command", ""),
                    "lines_added": p.get("lines_added"),
                    "lines_removed": p.get("lines_removed"),
                    "repo": p.get("repo", ""),
                    "diff": (p.get("diff", "") or "")[:4000] if r["type"] == "code_diff" else "",
                })
    except Exception:
        pass

    return {
        "session": dict(row),
        "analysis": dict(analysis_row) if analysis_row else None,
        "file_activity": file_activity,
    }
