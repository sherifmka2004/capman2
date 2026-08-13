"""Populate `documents` from data captured before the FTS index existed.

Without this, migration 003 would leave every existing install with an empty
keyword index — searchable only for content captured from that moment on. The
sources below reconstruct the corpus from what was already persisted.

Idempotent: every row is an upsert keyed by a deterministic id, so running it
twice is a no-op. Safe to run against a live database.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

#: Rebuilt whenever the backfill logic changes in a way that needs re-running.
BACKFILL_VERSION = 1


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


async def backfill_documents(db, knowledge_dir: str | None = None) -> dict[str, int]:
    """Rebuild `documents` from events, analyses, playbooks and the markdown vault.

    Returns a per-source count of rows written.
    """
    counts: dict[str, int] = {}
    counts["events_fts"] = await index_existing_events(db)
    counts["page"] = await _from_page_events(db)
    counts["doc"] = await _from_doc_events(db)
    counts["session"] = await _from_analyses(db)
    counts["playbook"] = await _from_playbooks(db)
    counts["node"] = await _from_markdown(db, knowledge_dir) if knowledge_dir else 0
    total = sum(counts.values())
    logger.info("Backfilled %d documents: %s", total, counts)
    return counts


#: Kept in sync with the trigger guards in migrations/003_fts.sql.
_UNINDEXED_EVENT_TYPES = ("mouse_heatmap_tick", "mouse_scroll", "dom_mutation")


async def index_existing_events(db) -> int:
    """Index events captured before events_fts existed.

    Not `INSERT INTO events_fts(events_fts) VALUES('rebuild')` — that would
    index every row including the contentless types the triggers deliberately
    skip, and the index would then disagree with the delete guard.
    """
    async with db._db.execute("SELECT COUNT(*) FROM events_fts_docsize") as cur:
        if (await cur.fetchone())[0] > 0:
            return 0  # already populated

    placeholders = ",".join("?" * len(_UNINDEXED_EVENT_TYPES))
    await db._db.execute(
        f"""INSERT INTO events_fts(rowid, search_text)
            SELECT rowid, search_text FROM events
            WHERE type NOT IN ({placeholders}) AND trim(search_text) != ''""",
        _UNINDEXED_EVENT_TYPES,
    )
    await db._db.commit()
    async with db._db.execute("SELECT COUNT(*) FROM events_fts_docsize") as cur:
        return (await cur.fetchone())[0]


async def _from_page_events(db) -> int:
    """Page text. Only the 300-char excerpt survives in SQLite for old rows —
    that is still far better than nothing for keyword search, and re-visiting
    the page overwrites it with the full text."""
    async with db._db.execute(
        "SELECT id, ts, payload FROM events WHERE type = 'page_text' ORDER BY ts"
    ) as cur:
        rows = await cur.fetchall()

    docs = []
    for r in rows:
        try:
            p = json.loads(r["payload"])
        except (json.JSONDecodeError, TypeError):
            continue
        body = (p.get("excerpt") or "").strip()
        url = p.get("url") or ""
        if not body or not url:
            continue
        docs.append({
            "id": "page:" + _sha(url), "kind": "page", "body": body, "ts": r["ts"],
            "title": p.get("title") or "", "uri": url, "ref_id": r["id"],
        })
    return await db.upsert_documents_bulk(docs)


async def _from_doc_events(db) -> int:
    async with db._db.execute(
        "SELECT id, ts, payload FROM events WHERE type = 'doc_content' ORDER BY ts"
    ) as cur:
        rows = await cur.fetchall()

    docs = []
    for r in rows:
        try:
            p = json.loads(r["payload"])
        except (json.JSONDecodeError, TypeError):
            continue
        body = (p.get("text") or "").strip()
        if not body:
            continue
        doc_path = p.get("doc_path") or ""
        key = f"{doc_path}|{p.get('item_kind','')}|{p.get('item_index',0)}|{p.get('item_label','')}"
        docs.append({
            "id": "doc:" + _sha(key), "kind": "doc", "body": body, "ts": r["ts"],
            "title": f"{p.get('doc_name') or doc_path} — {p.get('item_kind','')} {p.get('item_index','')}".strip(),
            "uri": doc_path, "ref_id": r["id"],
        })
    return await db.upsert_documents_bulk(docs)


async def _from_analyses(db) -> int:
    """Session analyses — the summaries that were never indexed at all.

    VectorStore.add_session_summary had no production caller, so `type=session`
    search has always returned nothing. This gives those sessions a home.
    """
    async with db._db.execute(
        "SELECT session_id, problem_statement, approach_description, "
        "       methodology_tags, knowledge_applied, knowledge_acquired, analyzed_at "
        "FROM session_analyses"
    ) as cur:
        rows = await cur.fetchall()

    docs = []
    for r in rows:
        parts = [r["problem_statement"] or "", r["approach_description"] or ""]
        for col in ("methodology_tags", "knowledge_applied", "knowledge_acquired"):
            try:
                vals = json.loads(r[col] or "[]")
                if isinstance(vals, list):
                    parts.append(" ".join(str(v) for v in vals))
            except (json.JSONDecodeError, TypeError):
                pass
        body = "\n".join(p for p in parts if p.strip())
        if not body.strip():
            continue
        docs.append({
            "id": f"session:{r['session_id']}", "kind": "session", "body": body,
            "ts": r["analyzed_at"] or time.time(),
            "title": (r["problem_statement"] or "")[:120],
            "ref_id": r["session_id"], "session_id": r["session_id"],
        })
    return await db.upsert_documents_bulk(docs)


async def _from_playbooks(db) -> int:
    """Playbooks. Vector indexing of these fails silently today (the handler is
    a logger.debug), so playbook search returns nothing; BM25 fixes that
    independently of whether the embedding path is working."""
    async with db._db.execute(
        "SELECT id, session_id, title, domain, symptoms, context_signals, "
        "       root_cause, fix, verification, created_at FROM playbooks"
    ) as cur:
        rows = await cur.fetchall()

    docs = []
    for r in rows:
        parts = [r["title"] or "", r["domain"] or "", r["root_cause"] or ""]
        for col in ("symptoms", "context_signals", "fix", "verification"):
            try:
                vals = json.loads(r[col] or "[]")
                if isinstance(vals, list):
                    parts.append(" ".join(str(v) for v in vals))
            except (json.JSONDecodeError, TypeError):
                pass
        body = "\n".join(p for p in parts if p.strip())
        if not body.strip():
            continue
        docs.append({
            "id": f"playbook:{r['id']}", "kind": "playbook", "body": body,
            "ts": r["created_at"] or time.time(), "title": r["title"] or "",
            "ref_id": r["id"], "session_id": r["session_id"],
        })
    return await db.upsert_documents_bulk(docs)


_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


async def _from_markdown(db, knowledge_dir: str) -> int:
    """The Obsidian vault — concept nodes, chain-of-thought files, playbook notes."""
    root = Path(knowledge_dir).expanduser()
    if not root.is_dir():
        return 0

    docs = []
    for path in root.rglob("*.md"):
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        body = _FRONTMATTER_RE.sub("", raw).strip()
        if not body:
            continue
        title = next((ln.lstrip("# ").strip() for ln in body.splitlines()
                      if ln.startswith("# ")), path.stem)
        rel = str(path.relative_to(root))
        docs.append({
            "id": "node:" + _sha(rel), "kind": "node", "body": body,
            "ts": path.stat().st_mtime, "title": title, "uri": rel, "ref_id": path.stem,
        })
    return await db.upsert_documents_bulk(docs)
