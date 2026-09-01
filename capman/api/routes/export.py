"""GET /export — controlled export of *extracted knowledge*.

This is the only sanctioned way data leaves the machine, and it is deliberately
narrow. capman2 captures keystrokes, clipboard contents, screenshots and full
page text; none of that is exportable here at any setting. What can leave is
the derived layer — playbooks, concept nodes, chain-of-thought, session
summaries — which is the part with value to anyone else and the part a user
might reasonably want to share.

Policy is allow-list, not deny-list: a kind that nobody has explicitly
permitted is not exportable, so adding a new capture type can never silently
widen what gets shared. `--redact` additionally strips hostnames, absolute
paths, emails and anything that looks like a credential from the output.

Defaults to a dry run. You have to ask for the payload.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from capman.knowledge.privacy import redact_derived_text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/export", tags=["export"])

#: Derived knowledge only. Raw capture is absent by construction, not by filter.
EXPORTABLE_KINDS = ("playbook", "node", "session")

#: Never exportable, at any setting. Listed explicitly so the intent is
#: reviewable and a future contributor has to argue with it in a diff.
NEVER_EXPORTABLE = ("page", "doc", "ocr", "event", "screenshot", "keystroke", "clipboard")

def redact(text: str) -> str:
    """Strip identifying and secret-shaped substrings."""
    return redact_derived_text(text)


def resolve_policy(config: dict) -> list[str]:
    """Kinds the user has permitted, intersected with what is ever exportable."""
    cfg = config.get("storage", {}).get("sharing", {}) if config else {}
    allowed = cfg.get("allow_kinds")
    if allowed is None:
        allowed = list(EXPORTABLE_KINDS)
    resolved = []
    for kind in allowed:
        if kind in NEVER_EXPORTABLE:
            logger.warning("Refusing to export %r — raw capture is never exportable", kind)
            continue
        if kind not in EXPORTABLE_KINDS:
            logger.warning("Ignoring unknown export kind %r", kind)
            continue
        resolved.append(kind)
    return resolved


async def collect(db, kinds: list[str], since: float | None = None,
                  limit: int = 1000, do_redact: bool = True) -> list[dict[str, Any]]:
    if not kinds or db is None:
        return []
    sql = ("SELECT id, kind, title, uri, ts, session_id, body FROM documents "
           f"WHERE kind IN ({','.join('?' * len(kinds))})")
    params: list[Any] = list(kinds)
    if since:
        sql += " AND ts >= ?"
        params.append(since)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)

    async with db._db.execute(sql, params) as cur:
        rows = await cur.fetchall()

    out = []
    for r in rows:
        item = {"id": r["id"], "kind": r["kind"], "title": r["title"] or "",
                "ts": r["ts"], "body": r["body"] or ""}
        if do_redact:
            item["title"] = redact(item["title"])
            item["body"] = redact(item["body"])
        else:
            item["uri"] = r["uri"] or ""
        out.append(item)
    return out


@router.get("")
async def export(
    kinds: str | None = Query(None, description=f"Subset of {','.join(EXPORTABLE_KINDS)}"),
    since_days: int | None = Query(None, ge=1),
    limit: int = Query(1000, ge=1, le=10000),
    redact_output: bool = Query(True, alias="redact"),
    dry_run: bool = Query(True, description="Report what would be exported without returning it"),
    request: Request = None,
):
    config = request.app.state.config or {}
    db = request.app.state.db

    permitted = resolve_policy(config)
    requested = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else permitted
    selected = [k for k in requested if k in permitted]
    refused = [k for k in requested if k not in permitted]

    since = time.time() - since_days * 86400 if since_days else None
    items = await collect(db, selected, since, limit, redact_output)

    summary = {
        "policy": {"permitted_kinds": permitted, "never_exportable": list(NEVER_EXPORTABLE)},
        "requested_kinds": requested,
        "exported_kinds": selected,
        "refused_kinds": refused,
        "redacted": redact_output,
        "count": len(items),
        "counts_by_kind": {k: sum(1 for i in items if i["kind"] == k) for k in selected},
        "dry_run": dry_run,
    }
    if dry_run:
        summary["sample_titles"] = [i["title"][:80] for i in items[:5]]
        return summary

    summary["items"] = items
    return summary


@router.get("/jsonl")
async def export_jsonl(
    kinds: str | None = Query(None),
    since_days: int | None = Query(None, ge=1),
    limit: int = Query(1000, ge=1, le=10000),
    redact_output: bool = Query(True, alias="redact"),
    request: Request = None,
):
    """Same policy, newline-delimited JSON for piping into other tools."""
    from fastapi.responses import PlainTextResponse

    config = request.app.state.config or {}
    db = request.app.state.db
    permitted = resolve_policy(config)
    requested = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else permitted
    selected = [k for k in requested if k in permitted]

    items = await collect(db, selected, time.time() - since_days * 86400 if since_days else None,
                          limit, redact_output)
    return PlainTextResponse("\n".join(json.dumps(i, ensure_ascii=False) for i in items),
                             media_type="application/x-ndjson")


# ---------------------------------------------------------------------------
# Fine-tuning export (merged from the training-data work on main).
#
# This emits analysed sessions with full chain-of-thought, triples and
# playbooks — derived knowledge, so it sits inside the same allow-list as the
# rest of this module rather than beside it. Two guards were added when the
# two export implementations were merged: it now refuses to run unless
# `session` is a permitted kind, and it redacts free text by default like
# every other export path.
# ---------------------------------------------------------------------------

@router.get("/training-data")
async def export_training_data(request: Request, format: str = "jsonl", limit: int = 1000,
                               redact_output: bool = Query(True, alias="redact")):
    """
    Export all analyzed sessions as structured JSONL suitable for AI/LLM fine-tuning.
    Each line is a self-contained JSON record with full chain-of-thought, triples, and playbook.
    """
    db = request.app.state.db
    if db is None:
        return {"error": "database not available"}

    # Same allow-list as every other export path.
    if "session" not in resolve_policy(request.app.state.config or {}):
        return {"error": "session export is not permitted by storage.sharing.allow_kinds",
                "permitted_kinds": resolve_policy(request.app.state.config or {})}

    _r = redact if redact_output else (lambda s: s)

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
                "problem_statement": _r(row["problem_statement"] or ""),
                "approach_description": _r(row["approach_description"] or ""),
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
