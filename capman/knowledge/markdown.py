"""Persist a session's document-navigation activity as a Markdown 'document node'.

One file per document under `<knowledge_dir>/docs/<doc_type>/`, accumulating a
`### Session` block per reading session. The point is not the raw trail but
what it reveals: which slides or pages you actually dwelled on, and whether you
read front-to-back or hunted around — both of which say more about what
mattered than a list of page numbers does.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DOCS_SUBDIR = "docs"

#: Unit column header per document type.
_UNIT_LABEL = {
    "presentation": "Slide",
    "document": "Page",
    "spreadsheet": "Sheet",
    "notebook": "Note",
}


def _safe_filename(text: str, limit: int = 80) -> str:
    cleaned = "".join(c if c.isalnum() or c in " -_" else "_" for c in text)[:limit]
    return cleaned.strip().replace(" ", "_") or "untitled"


def _slug(text: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", (text or "").lower(), flags=re.UNICODE)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:maxlen] or "untitled"


def _unit_key(ev: dict[str, Any]) -> Any:
    """The identity of the thing being viewed — slide/page number, or sheet name."""
    for field in ("current_slide", "current_page", "sheet_index"):
        if ev.get(field):
            return ev[field]
    return ev.get("sheet_name") or ev.get("note_title") or ""


def _unit_title(ev: dict[str, Any]) -> str:
    for field in ("slide_title", "section_heading", "sheet_name", "note_title"):
        if ev.get(field):
            return str(ev[field])
    return ""


def _unit_ordinal(ev: dict[str, Any]) -> int | None:
    """Numeric position, when the document type has one."""
    for field in ("current_slide", "current_page", "sheet_index"):
        value = ev.get(field)
        if isinstance(value, int) and value:
            return value
    return None


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


def _infer_nav_pattern(doc_type: str, events: list[dict[str, Any]]) -> str:
    """Describe how the reader moved through the document.

    Linear reading and hunting look completely different in the trail, and the
    difference is the interesting part: a backtrack marks something that did
    not land the first time.
    """
    ordinals = [o for o in (_unit_ordinal(e) for e in events) if o is not None]
    if len(ordinals) < 2:
        return "Single view — not enough navigation to infer a pattern."

    backward = jumps = forward = 0
    for prev, cur in zip(ordinals, ordinals[1:]):
        if cur == prev + 1:
            forward += 1
        elif cur < prev:
            backward += 1
        elif cur > prev + 1:
            jumps += 1

    if backward == 0 and jumps == 0:
        return "Linear — read straight through, front to back."

    parts = []
    if backward:
        parts.append(f"{backward} backward move{'s' if backward != 1 else ''}")
    if jumps:
        parts.append(f"{jumps} forward jump{'s' if jumps != 1 else ''}")
    detail = ", ".join(parts)
    return f"Non-linear — {detail} (of {len(ordinals) - 1} transitions)."


def _aggregate_units(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse repeat visits to the same unit, summing dwell and counting revisits."""
    by_unit: dict[Any, dict[str, Any]] = {}
    order: list[Any] = []
    for ev in events:
        key = _unit_key(ev)
        if key == "":
            continue
        entry = by_unit.get(key)
        if entry is None:
            entry = {"key": key, "ordinal": _unit_ordinal(ev), "title": _unit_title(ev),
                     "dwell": 0.0, "visits": 0}
            by_unit[key] = entry
            order.append(key)
        entry["dwell"] += float(ev.get("dwell_s") or 0.0)
        entry["visits"] += 1
        if not entry["title"]:
            entry["title"] = _unit_title(ev)
    return [by_unit[k] for k in order]


def _build_nav_table(doc_type: str, events: list[dict[str, Any]]) -> list[str]:
    """Markdown table of units visited, with dwell time and revisit count."""
    units = _aggregate_units(events)
    if not units:
        return []

    label = _UNIT_LABEL.get(doc_type, "Unit")
    rows = [
        f"| {label} | Title | Dwell | Visits |",
        "|---|---|---|---|",
    ]
    for u in units:
        shown = u["ordinal"] if u["ordinal"] is not None else u["key"]
        rows.append(f"| {shown} | {u['title'] or '—'} | {u['dwell']:.0f}s | {u['visits']} |")
    return rows


def _build_hotspots(events: list[dict[str, Any]], top_n: int = 3) -> list[str]:
    """The units that held attention longest — the actual signal in the trail."""
    units = [u for u in _aggregate_units(events) if u["dwell"] > 0]
    if not units:
        return []
    units.sort(key=lambda u: u["dwell"], reverse=True)
    lines = ["## Most Studied", ""]
    for u in units[:top_n]:
        shown = u["ordinal"] if u["ordinal"] is not None else u["key"]
        title = u["title"] or str(shown)
        lines.append(f"- **{title}** ({shown}) — {u['dwell']:.0f}s"
                     + (f", {u['visits']} visits" if u["visits"] > 1 else ""))
    lines.append("")
    return lines


def _session_block(doc_type: str, events: list[dict[str, Any]],
                   session_id: str, session_started_at: float) -> list[str]:
    when = datetime.fromtimestamp(session_started_at).strftime("%Y-%m-%d %H:%M")
    total_dwell = sum(float(e.get("dwell_s") or 0.0) for e in events)

    lines = [f"### Session {session_id}", "", f"- **When:** {when}",
             f"- **Time in document:** {total_dwell:.0f}s",
             f"- **Navigation pattern:** {_infer_nav_pattern(doc_type, events)}", ""]
    table = _build_nav_table(doc_type, events)
    if table:
        lines += table + [""]
    lines += _build_hotspots(events)
    lines += ["#### Trail", ""] + [_navigation_line(e) for e in events] + [""]
    return lines


def save_document_node(
    doc_events: list[dict[str, Any]],
    session_id: str,
    session_started_at: float,
    knowledge_dir: Path,
) -> Path | None:
    """Summarize a session's document navigation as a Markdown node.

    ``doc_events`` are the raw payloads of DOC_OPEN / DOC_SLIDE_CHANGE /
    DOC_PAGE_CHANGE / DOC_SHEET_CHANGE / DOC_NOTE_OPEN events. Returns the path
    written, or ``None`` when there is nothing worth persisting.

    Re-reading a document appends another ``### Session`` block rather than
    replacing the file, so the node accumulates a history of how attention to
    that document changed over time.
    """
    if not doc_events:
        return None

    knowledge_dir = Path(knowledge_dir).expanduser()
    first = doc_events[0]
    doc_type = first.get("doc_type") or "document"
    doc_name = first.get("doc_name") or first.get("doc_path") or "untitled"
    doc_path = first.get("doc_path") or ""
    app = first.get("app") or ""

    out_dir = knowledge_dir / _DOCS_SUBDIR / _slug(doc_type, 30)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_slug(doc_name)}.md"

    block = _session_block(doc_type, doc_events, session_id, session_started_at)

    try:
        if path.exists():
            existing = path.read_text(encoding="utf-8").rstrip("\n")
            path.write_text(existing + "\n\n" + "\n".join(block), encoding="utf-8")
        else:
            header = [
                "---",
                f'doc_name: "{doc_name}"',
                f'doc_path: "{doc_path}"',
                f"node_type: document",
                f'doc_type: "{doc_type}"',
                f'app: "{app}"',
                "---",
                "",
                f"# {doc_name}",
                "",
                f"- **Type:** {doc_type}",
                f"- **App:** {app}",
            ]
            if doc_path:
                header.append(f"- **Path:** `{doc_path}`")
            header += ["", "## Sessions", ""]
            path.write_text("\n".join(header + block), encoding="utf-8")
    except OSError as e:
        logger.error("Failed to write document node %s: %s", path, e)
        return None

    return path
