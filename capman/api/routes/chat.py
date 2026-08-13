"""
POST /chat  — LLM chatbot with full access to captured knowledge.

Builds context from:
  1. Vector search (semantic match to user question)
  2. Recent session analyses (problem statements, CoT patterns)
  3. Knowledge triples (subject → predicate → object facts)

Then calls OpenRouter (claude-sonnet-4-6) to answer.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
CHAT_MODEL = "anthropic/claude-sonnet-4-6"


class ChatMessage(BaseModel):
    role: str   # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


async def _build_context(question: str, request: Request) -> str:
    """Pull relevant data from all stores to ground the LLM response."""
    config = request.app.state.config
    db = request.app.state.db
    sections: list[str] = []
    _chat = config.get("api", {}).get("chat", {})

    # 1. Vector search — semantically relevant sessions and nodes
    try:
        from capman.storage.vector import get_vector_store
        chroma_path = config.get("storage", {}).get("chroma_path", "~/.capman/chroma")
        vs = get_vector_store(chroma_path)
        if vs.count() > 0:
            results = vs.search(question, top_k=int(_chat.get("vector_top_k", 5)))
            if results:
                lines = []
                for r in results:
                    lines.append(f"  [{r['score']:.2f}] ({r['type']}) {r['title']}: {r['text'][:200]}")
                sections.append("## Semantically Relevant Knowledge\n" + "\n".join(lines))
    except Exception as e:
        logger.debug("Vector search skipped: %s", e)

    # 2. Recent session analyses
    if db:
        try:
            async with db._db.execute(
                """SELECT s.started_at, sa.problem_statement, sa.approach_description,
                          sa.methodology_tags, sa.chain_of_thought, sa.knowledge_acquired
                   FROM sessions s
                   JOIN session_analyses sa ON s.id = sa.session_id
                   ORDER BY s.started_at DESC LIMIT ?""",
                (int(_chat.get("recent_sessions", 10)),)
            ) as cur:
                rows = await cur.fetchall()

            if rows:
                lines = []
                for row in rows:
                    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["started_at"]))
                    tags = json.loads(row["methodology_tags"] or "[]")
                    acquired = json.loads(row["knowledge_acquired"] or "[]")
                    lines.append(f"  [{ts}] {row['problem_statement']}")
                    lines.append(f"    Approach: {(row['approach_description'] or '')[:200]}")
                    if tags:
                        lines.append(f"    Tags: {', '.join(tags)}")
                    if acquired:
                        lines.append(f"    Learned: {', '.join(acquired)}")
                    # Include CoT methodology pattern if present
                    if row["chain_of_thought"]:
                        cot = json.loads(row["chain_of_thought"])
                        pattern = cot.get("methodology_pattern", "")
                        if pattern:
                            lines.append(f"    Workflow pattern: {pattern}")
                        gaps = cot.get("knowledge_gaps_revealed", [])
                        if gaps:
                            lines.append(f"    Knowledge gaps revealed: {', '.join(gaps)}")
                sections.append("## Recent Work Sessions\n" + "\n".join(lines))
        except Exception as e:
            logger.debug("Session fetch skipped: %s", e)

    # 3. Knowledge triples
    if db:
        try:
            async with db._db.execute(
                """SELECT subject, predicate, object, confidence
                   FROM knowledge_triples
                   ORDER BY confidence DESC, last_observed DESC LIMIT ?""",
                (int(_chat.get("knowledge_triples", 40)),)
            ) as cur:
                rows = await cur.fetchall()

            if rows:
                lines = [
                    f"  ({r['subject']}) --[{r['predicate']}]--> ({r['object']})  [{r['confidence']:.2f}]"
                    for r in rows
                ]
                sections.append("## Knowledge Graph Facts\n" + "\n".join(lines))
        except Exception as e:
            logger.debug("Triples fetch skipped: %s", e)

    # 4. Recent URLs visited
    if db:
        try:
            async with db._db.execute(
                """SELECT payload, ts FROM events
                   WHERE type='url_visit'
                   ORDER BY ts DESC LIMIT ?""",
                (int(_chat.get("recent_urls", 25)),)
            ) as cur:
                rows = await cur.fetchall()
            if rows:
                lines = []
                seen = set()
                for r in rows:
                    p = json.loads(r["payload"])
                    url = p.get("url", "")
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"]))
                    title = (p.get("title") or "")[:60]
                    lines.append(f"  [{when}] {url[:120]}" + (f"  — {title}" if title else ""))
                if lines:
                    sections.append("## Recently Visited URLs\n" + "\n".join(lines))
        except Exception as e:
            logger.debug("URLs fetch skipped: %s", e)

    # 5. Recent search queries
    if db:
        try:
            async with db._db.execute(
                """SELECT payload, ts FROM events
                   WHERE type='search_query'
                   ORDER BY ts DESC LIMIT ?""",
                (int(_chat.get("recent_searches", 20)),)
            ) as cur:
                rows = await cur.fetchall()
            if rows:
                lines = []
                seen = set()
                for r in rows:
                    p = json.loads(r["payload"])
                    q = p.get("query", "")
                    if not q or q in seen:
                        continue
                    seen.add(q)
                    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"]))
                    lines.append(f"  [{when}] [{p.get('engine', '')}] {q}")
                if lines:
                    sections.append("## Recent Search Queries\n" + "\n".join(lines))
        except Exception as e:
            logger.debug("Searches fetch skipped: %s", e)

    # 6. Recent shell commands
    if db:
        try:
            async with db._db.execute(
                """SELECT payload, ts FROM events
                   WHERE type='shell_command'
                   ORDER BY ts DESC LIMIT ?""",
                (int(_chat.get("recent_commands", 25)),)
            ) as cur:
                rows = await cur.fetchall()
            if rows:
                lines = []
                for r in rows:
                    p = json.loads(r["payload"])
                    cmd = p.get("command", "")
                    if not cmd:
                        continue
                    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"]))
                    lines.append(f"  [{when}] $ {cmd[:140]}")
                if lines:
                    sections.append("## Recent Shell Commands\n" + "\n".join(lines))
        except Exception as e:
            logger.debug("Commands fetch skipped: %s", e)

    # 6a. Recent file activity (files the user directly opened / changed / deleted)
    if db:
        try:
            async with db._db.execute(
                """SELECT type, payload, ts FROM events
                   WHERE type IN ('file_open','file_save','file_delete','file_rename','code_diff')
                   ORDER BY ts DESC LIMIT ?""",
                (int(_chat.get("recent_files", 60)),)
            ) as cur:
                rows = await cur.fetchall()
            if rows:
                lines = []
                seen_diff: set = set()
                for r in rows:
                    p = json.loads(r["payload"])
                    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"]))
                    attr = p.get("attribution", "")
                    actor = p.get("actor", {}) or {}
                    who = actor.get("app") or actor.get("comm") or ""
                    via = p.get("via_command", "")
                    t = r["type"]
                    if t == "code_diff":
                        path = p.get("path", "")
                        key = (path, p.get("lines_added"), p.get("lines_removed"))
                        if key in seen_diff:
                            continue
                        seen_diff.add(key)
                        repo = f" [{p['repo']}]" if p.get("repo") else ""
                        lines.append(f"  [{when}] CHANGED {path}{repo}  (+{p.get('lines_added',0)}/-{p.get('lines_removed',0)})"
                                     + (f"  via `{who}`" if who else ""))
                        diff_txt = (p.get("diff", "") or "").strip()
                        if diff_txt:
                            for dl in diff_txt.splitlines()[:12]:
                                lines.append(f"      {dl[:120]}")
                    elif t == "file_rename":
                        lines.append(f"  [{when}] RENAMED {p.get('src_path','')} → {p.get('dest_path','')}"
                                     + (f"  via `{who}`" if who else ""))
                    elif t == "file_delete":
                        lines.append(f"  [{when}] DELETED {p.get('path','')}" + (f"  via `{who}`" if who else ""))
                    elif t == "file_open":
                        lines.append(f"  [{when}] OPENED  {p.get('path','')}" + (f"  via `{who}`" if who else ""))
                    else:  # file_save
                        extra = f"  via `{who}`" if who else ""
                        if via:
                            extra += f"  (cmd: {via[:60]})"
                        lines.append(f"  [{when}] SAVED   {p.get('path','')}{extra}")
                if lines:
                    sections.append("## Recent File Activity (user-driven file opens / edits / deletes)\n" + "\n".join(lines[:120]))
        except Exception as e:
            logger.debug("File activity fetch skipped: %s", e)

    # 6a-bis. Semantically relevant document content (slides/pages/sheets the user actually read)
    try:
        from capman.storage.vector import get_vector_store
        chroma_path = config.get("storage", {}).get("chroma_path", "~/.capman/chroma")
        vs = get_vector_store(chroma_path)
        doc_hits = vs.search(question, top_k=int(_chat.get("doc_top_k", 6)), types=["doc"])
        if doc_hits:
            lines = []
            for h in doc_hits:
                meta = h.get("metadata", {}) or {}
                ts = meta.get("ts", 0)
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""
                doc_name = meta.get("doc_name", "") or meta.get("doc_path", "") or ""
                kind = meta.get("item_kind", "")
                idx = meta.get("item_index", "")
                label = meta.get("item_label", "")
                head = f"{doc_name} — {kind} {idx}".strip()
                if label:
                    head += f": {label}"
                lines.append(f"  [{when}] [{h['score']:.2f}] {head}")
                if meta.get("app"):
                    lines.append(f"    App: {meta['app']}")
                lines.append(f"    Excerpt: {h['text'][:800]}")
                lines.append("")
            sections.append("## Relevant Document Content (slides / pages / sheets the user actually read)\n"
                            + "\n".join(lines))
    except Exception as e:
        logger.debug("Doc semantic search skipped: %s", e)

    # 6b. Semantically relevant page chunks (vector search on embedded page text)
    try:
        from capman.storage.vector import get_vector_store
        chroma_path = config.get("storage", {}).get("chroma_path", "~/.capman/chroma")
        vs = get_vector_store(chroma_path)
        page_hits = vs.search(question, top_k=int(_chat.get("page_top_k", 6)), types=["page"])
        if page_hits:
            lines = []
            for h in page_hits:
                ts = h["metadata"].get("ts", 0)
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""
                title = h.get("title", "")
                url = h.get("url", "")
                lines.append(f"  [{when}] [{h['score']:.2f}] {title}")
                lines.append(f"    URL: {url}")
                lines.append(f"    Excerpt: {h['text'][:800]}")
                lines.append("")
            sections.append("## Relevant Page Content (semantic match)\n" + "\n".join(lines))
    except Exception as e:
        logger.debug("Page semantic search skipped: %s", e)

    # 6c. Most relevant troubleshooting playbooks (THE differentiator)
    try:
        from capman.storage.vector import get_vector_store
        chroma_path = config.get("storage", {}).get("chroma_path", "~/.capman/chroma")
        vs = get_vector_store(chroma_path)
        pb_hits = vs.search(question, top_k=int(_chat.get("playbook_top_k", 3)), types=["playbook"])
        if pb_hits and db:
            lines = []
            for h in pb_hits:
                pb_id = h["metadata"].get("playbook_id", "")
                if not pb_id:
                    continue
                async with db._db.execute(
                    "SELECT * FROM playbooks WHERE id = ?", (pb_id,)
                ) as cur:
                    row = await cur.fetchone()
                if not row:
                    continue
                steps = json.loads(row["diagnostic_steps"] or "[]")
                lines.append(f"  ━ Playbook: {row['title']} (domain: {row['domain']}, score {h['score']:.2f})")
                if row["root_cause"]:
                    lines.append(f"    Root cause: {row['root_cause']}")
                for s in steps[:5]:
                    lines.append(f"    {s.get('sequence','')}. {s.get('action','')}  [{s.get('tool','')}]")
                lines.append("")
            if lines:
                sections.append("## Matching Troubleshooting Playbooks (replicable methodology)\n" + "\n".join(lines))
    except Exception as e:
        logger.debug("Playbook search skipped: %s", e)

    # 6d. Knowledge gaps user keeps looking up
    if db:
        try:
            top_gaps = await db.get_top_knowledge_gaps(limit=10)
            if top_gaps:
                lines = []
                for g in top_gaps[:8]:
                    examples = json.loads(g["query_examples"] or "[]")[:2]
                    lines.append(f"  - {g['concept']} ({g['domain'] or 'unspecified'}, looked up {g['lookup_count']}×)")
                    if examples:
                        lines.append(f"      e.g. {' / '.join(examples)}")
                sections.append("## Recurring Knowledge Gaps (concepts user keeps looking up)\n" + "\n".join(lines))
        except Exception as e:
            logger.debug("Gap fetch skipped: %s", e)

    # 6e. Active vs AFK periods (last 7 days, totals by day)
    if db:
        try:
            since = time.time() - int(_chat.get("activity_window_days", 7)) * 86400
            async with db._db.execute(
                """SELECT type, payload, ts FROM events
                   WHERE type IN ('idle_start','idle_end') AND ts > ?
                   ORDER BY ts ASC""",
                (since,),
            ) as cur:
                rows = await cur.fetchall()
            if rows:
                # Pair each IDLE_START with the next IDLE_END to get an idle window.
                idle_intervals: list[tuple[float, float]] = []
                start_ts: float | None = None
                for r in rows:
                    if r["type"] == "idle_start":
                        start_ts = r["ts"]
                    elif r["type"] == "idle_end" and start_ts is not None:
                        idle_intervals.append((start_ts, r["ts"]))
                        start_ts = None
                # Bucket idle seconds per local day; active = (24h − idle − unknown).
                idle_by_day: dict[str, float] = {}
                for s, e in idle_intervals:
                    day = time.strftime("%Y-%m-%d", time.localtime(s))
                    idle_by_day[day] = idle_by_day.get(day, 0.0) + (e - s)
                # Observed span per local day, in one grouped query rather than a
                # per-day subquery in a loop. SQLite's 'localtime' modifier also
                # handles DST correctly — the previous
                # mktime(strptime(day, "%Y-%m-%d")) left tm_isdst = -1, so day
                # boundaries drifted an hour across a transition and idle time
                # was attributed to the wrong day.
                async with db._db.execute(
                    """SELECT date(ts, 'unixepoch', 'localtime') AS day,
                              MIN(ts) AS lo, MAX(ts) AS hi
                       FROM events WHERE ts > ?
                       GROUP BY day ORDER BY day DESC LIMIT 7""",
                    (since,),
                ) as cur2:
                    spans = {r["day"]: (r["lo"], r["hi"]) for r in await cur2.fetchall()}

                lines = []
                for day in sorted(idle_by_day.keys(), reverse=True)[:7]:
                    idle_h = idle_by_day[day] / 3600.0
                    # Active hours = time we observed *any* event between first
                    # and last event of that day, minus idle.
                    lo, hi = spans.get(day, (0, 0))
                    span_h = ((hi or 0) - (lo or 0)) / 3600.0
                    active_h = max(0.0, span_h - idle_h)
                    lines.append(
                        f"  [{day}] active ≈ {active_h:.1f} h  |  idle ≈ {idle_h:.1f} h  "
                        f"(observed window {span_h:.1f} h)"
                    )
                if lines:
                    sections.append("## Active vs AFK Periods (last 7 days)\n" + "\n".join(lines))
        except Exception as e:
            logger.debug("AFK summary skipped: %s", e)

    # 7. Activity summary (last 24h)
    if db:
        try:
            since = time.time() - 86400
            async with db._db.execute(
                """SELECT type, COUNT(*) as cnt FROM events
                   WHERE ts > ? GROUP BY type ORDER BY cnt DESC""",
                (since,),
            ) as cur:
                rows = await cur.fetchall()
            if rows:
                summary = ", ".join(f"{r['cnt']} {r['type']}" for r in rows)
                sections.append(f"## Last 24h Activity Summary\n  {summary}")
        except Exception as e:
            logger.debug("Events summary skipped: %s", e)

    if not sections:
        return "No captured data available yet."
    return "\n\n".join(sections)


def _system_prompt(context: str) -> str:
    return f"""You are a personal knowledge assistant for a domain expert.
You have access to everything they have done on their computer — every search, URL visited, command run, document navigated, and the LLM-extracted chain-of-thought workflows from each work session.

Your job is to answer questions about:
- What the user has been working on
- How they approached problems (their methodology and thought process)
- What they have learned or looked up
- Patterns in their workflow
- Specific technical knowledge captured from their sessions

Always ground your answers in the captured data below. Be specific — mention actual URLs, search queries, commands, and methodology patterns when relevant. If something is not in the captured data, say so clearly.

---

{context}

---

Answer conversationally but precisely. Reference specific sessions, URLs, or patterns from the data when they are relevant to the question."""


async def _call_openrouter(messages: list[dict], api_key: str, config: dict) -> str:
    """One-shot call to OpenRouter, returns the assistant's full text."""
    _chat = config.get("api", {}).get("chat", {})
    model = _chat.get("model", CHAT_MODEL)
    max_tokens = int(_chat.get("max_tokens", 1024))
    timeout = float(_chat.get("http_timeout_s", 60.0))
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/capman2",
                "X-Title": "capman2-chat",
            },
            json={
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


@router.post("/message")
async def chat_message(body: ChatRequest, request: Request):
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"reply": "No LLM API key configured on server.", "error": True}

    last_user_msg = next(
        (m.content for m in reversed(body.messages) if m.role == "user"), ""
    )
    context = await _build_context(last_user_msg, request)

    messages = [{"role": "system", "content": _system_prompt(context)}]
    messages += [{"role": m.role, "content": m.content} for m in body.messages]

    try:
        reply = await _call_openrouter(messages, api_key, request.app.state.config)
        return {"reply": reply}
    except Exception as e:
        logger.error("Chat call failed: %s", e)
        return {"reply": f"Error: {e}", "error": True}
