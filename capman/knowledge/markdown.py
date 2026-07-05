"""Persist a session's document-navigation activity as a Markdown 'document node'."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DOCS_SUBDIR = "documents"


def _safe_filename(text: str, limit: int = 80) -> str:
    cleaned = "".join(c if c.isalnum() or c in " -_" else "_" for c in text)[:limit]
    return cleaned.strip().replace(" ", "_") or "untitled"


def _navigation_line(ev: dict[str, Any]) -> str:
    """Render a single DOC_* payload as a navigation bullet."""
    dwell = float(ev.get("dwell_s") or 0.0)
    dwell_str = f" ({dwell:.0f}s)" if dwell > 0 else ""
    direction = ev.get("nav_direction")
    dir_str = f" [{direction}]" if direction and direction != "first" else ""

    if ev.get("current_slide"):
        total = ev.get("total_slides")
        loc = f"slide {ev['current_slide']}" + (f"/{total}" if total else "")
        title = ev.get("slide_title")
        label = f"{loc}: {title}" if title else loc
    elif ev.get("current_page"):
        total = ev.get("total_pages")
        loc = f"page {ev['current_page']}" + (f"/{total}" if total else "")
        heading = ev.get("section_heading")
        label = f"{loc}: {heading}" if heading else loc
    elif ev.get("sheet_name"):
        label = f"sheet '{ev['sheet_name']}'"
    elif ev.get("note_title"):
        notebook = ev.get("notebook")
        label = f"note '{ev['note_title']}'" + (f" in {notebook}" if notebook else "")
    else:
        label = "opened"

    return f"- {label}{dwell_str}{dir_str}"


def save_document_node(
    doc_events: list[dict[str, Any]],
    session_id: str,
    session_started_at: float,
    knowledge_dir: Path,
) -> Path | None:
    """Summarize a session's document navigation as a Markdown node.

    ``doc_events`` are the raw payloads of DOC_OPEN / DOC_SLIDE_CHANGE /
    DOC_PAGE_CHANGE / DOC_SHEET_CHANGE / DOC_NOTE_OPEN events (see
    ``capman.events.DocState``). Events are grouped by document and rendered as a
    navigation trail with dwell times. Returns the path written, or ``None`` when
    there is nothing worth persisting.
    """
    if not doc_events:
        return None

    # Group events per document, preserving first-seen order.
    docs: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for ev in doc_events:
        key = ev.get("doc_path") or ev.get("doc_name") or ev.get("note_title") or "unknown"
        if key not in docs:
            docs[key] = []
            order.append(key)
        docs[key].append(ev)

    if not order:
        return None

    started = datetime.fromtimestamp(session_started_at)
    lines: list[str] = [
        f"# Documents — session {session_id[:8]}",
        "",
        f"**Session started:** {started.isoformat(timespec='seconds')}",
        f"**Documents touched:** {len(order)}",
        "",
    ]

    for key in order:
        evs = docs[key]
        first = evs[0]
        doc_name = first.get("doc_name") or first.get("note_title") or key
        doc_type = first.get("doc_type") or "document"
        app = first.get("app") or ""

        lines += [f"## {doc_name}", ""]
        meta = [f"**Type:** {doc_type}"]
        if app:
            meta.append(f"**App:** {app}")
        if first.get("doc_path"):
            meta.append(f"**Path:** `{first['doc_path']}`")
        lines += ["  ·  ".join(meta), ""]

        trail = [_navigation_line(e) for e in evs]
        if trail:
            lines += ["### Navigation", ""] + trail + [""]

        total_dwell = sum(float(e.get("dwell_s") or 0.0) for e in evs)
        if total_dwell > 0:
            lines += [f"*Total dwell: {total_dwell:.0f}s across {len(evs)} events*", ""]

    out_dir = knowledge_dir / _DOCS_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    primary = docs[order[0]][0]
    label = primary.get("doc_name") or primary.get("note_title") or "documents"
    path = out_dir / f"{session_id[:8]}_{_safe_filename(label)}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
