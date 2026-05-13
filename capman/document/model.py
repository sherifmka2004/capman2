"""
Domain models for document-content capture.

`DocumentView`   — an immutable description of *one thing the user is looking at*
                   inside a document (a slide, a page, a sheet, a note). Built
                   from the navigation state the platform layer reports.
`ExtractedContent` — the text that was actually pulled off the screen / document
                     model for that view, plus where it came from.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# How a DocState `doc_type` maps onto the kind of "item" the user navigates.
_ITEM_KIND_BY_DOC_TYPE = {
    "presentation": "slide",
    "document": "page",
    "pdf": "page",
    "spreadsheet": "sheet",
    "notes": "note",
}


@dataclass(frozen=True)
class DocumentView:
    """One discrete unit of attention inside a document."""
    doc_type: str            # presentation|document|pdf|spreadsheet|notes
    doc_name: str
    doc_path: str
    app: str
    window_title: str
    item_kind: str           # slide|page|sheet|note
    item_index: int          # 1-based slide/page index, sheet index, or 0 if N/A
    item_label: str          # slide title / section heading / sheet name / note title
    nav_direction: str = ""  # forward|backward|jump|first
    arrived_at: float = field(default_factory=time.time, compare=False)
    revisit_count: int = field(default=1, compare=False)

    @property
    def key(self) -> tuple:
        """Stable identity of *which* unit this is (ignores timing / revisit count)."""
        return (self.doc_path or self.doc_name, self.item_kind, self.item_index, self.item_label)

    @classmethod
    def from_doc_state(cls, state, app: str, window_title: str) -> "DocumentView | None":
        """Build a view from a `capman.events.DocState`. Returns None if the state
        doesn't describe a navigable unit (e.g. a notes app with no note open)."""
        doc_type = getattr(state, "doc_type", "") or ""
        item_kind = _ITEM_KIND_BY_DOC_TYPE.get(doc_type)
        if not item_kind:
            return None

        if item_kind == "slide":
            idx = int(getattr(state, "current_slide", 0) or 0)
            label = getattr(state, "slide_title", "") or ""
        elif item_kind == "page":
            idx = int(getattr(state, "current_page", 0) or 0)
            label = getattr(state, "section_heading", "") or ""
        elif item_kind == "sheet":
            idx = int(getattr(state, "sheet_index", 0) or 0)
            label = getattr(state, "sheet_name", "") or ""
        else:  # note
            idx = 0
            label = getattr(state, "note_title", "") or ""

        # A presentation/document with no resolved index isn't a capturable unit.
        if item_kind in ("slide", "page") and idx <= 0:
            return None
        if item_kind in ("sheet", "note") and not label:
            return None

        return cls(
            doc_type=doc_type,
            doc_name=getattr(state, "doc_name", "") or "",
            doc_path=getattr(state, "doc_path", "") or "",
            app=getattr(state, "app", "") or app or "",
            window_title=window_title or "",
            item_kind=item_kind,
            item_index=idx,
            item_label=label,
            nav_direction=getattr(state, "nav_direction", "") or "",
        )

    def with_revisit(self, count: int) -> "DocumentView":
        return DocumentView(
            doc_type=self.doc_type, doc_name=self.doc_name, doc_path=self.doc_path,
            app=self.app, window_title=self.window_title, item_kind=self.item_kind,
            item_index=self.item_index, item_label=self.item_label,
            nav_direction=self.nav_direction, arrived_at=time.time(), revisit_count=count,
        )


@dataclass(frozen=True)
class ExtractedContent:
    """The text pulled off the screen / document model for a `DocumentView`."""
    text: str
    source: str               # "ocr" | "app_model" | ...
    item_label: str = ""      # extractor may refine the label (e.g. a slide title it read)
