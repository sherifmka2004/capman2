"""
Unit tests for the document-content capture pipeline.

Covers the small SOLID layer that lives in `capman/document/`:
  - DocumentView mapping from DocState
  - DwellAttentionPolicy (dwell + revisits)
  - ContentExtractionChain priority + skip-on-failure
  - DocumentContentTracker:
      • quick scroll-through → no emit
      • dwell ≥ threshold     → exactly one emit, correct payload
      • dedup by content hash
      • per-doc cap honored
"""
from __future__ import annotations

import asyncio
import pytest

from capman.document.attention import DwellAttentionPolicy
from capman.document.extractors import ContentExtractionChain, ContentExtractor
from capman.document.model import DocumentView, ExtractedContent
from capman.document.tracker import DocumentContentTracker
from capman.events import DocState, EventType


# ── DocumentView.from_doc_state ───────────────────────────────────────────────

def test_view_from_slide_state():
    state = DocState(doc_type="presentation", doc_name="Q4.pptx", doc_path="/x/Q4.pptx",
                     app="Keynote", current_slide=12, slide_title="Pricing")
    v = DocumentView.from_doc_state(state, "Keynote", "Q4.pptx — Slide 12 of 30")
    assert v is not None
    assert v.item_kind == "slide"
    assert v.item_index == 12
    assert v.item_label == "Pricing"
    assert v.key == ("/x/Q4.pptx", "slide", 12, "Pricing")


def test_view_from_page_state():
    state = DocState(doc_type="document", doc_name="Spec.docx", doc_path="",
                     app="Word", current_page=3, section_heading="Schema")
    v = DocumentView.from_doc_state(state, "Microsoft Word", "Spec.docx")
    assert v is not None
    assert v.item_kind == "page"
    assert v.item_index == 3
    assert v.item_label == "Schema"


def test_view_from_sheet_state():
    state = DocState(doc_type="spreadsheet", doc_name="Budget.xlsx", app="Excel",
                     sheet_name="Q4", sheet_index=2)
    v = DocumentView.from_doc_state(state, "Excel", "Budget.xlsx")
    assert v is not None
    assert v.item_kind == "sheet"
    assert v.item_label == "Q4"


def test_view_from_note_state():
    state = DocState(doc_type="notes", doc_name="Daily", app="Obsidian",
                     note_title="2026-05-13")
    v = DocumentView.from_doc_state(state, "Obsidian", "Obsidian – 2026-05-13")
    assert v is not None
    assert v.item_kind == "note"
    assert v.item_label == "2026-05-13"


def test_view_returns_none_when_no_index():
    state = DocState(doc_type="presentation", doc_name="x", current_slide=0)
    assert DocumentView.from_doc_state(state, "Keynote", "x") is None


def test_view_returns_none_for_unknown_doc_type():
    state = DocState(doc_type="", doc_name="x")
    assert DocumentView.from_doc_state(state, "?", "?") is None


def test_view_with_revisit_resets_arrived_at():
    state = DocState(doc_type="presentation", doc_name="x", current_slide=1, slide_title="t")
    v = DocumentView.from_doc_state(state, "Keynote", "x")
    v2 = v.with_revisit(3)
    assert v2.revisit_count == 3
    assert v2.key == v.key  # identity unchanged
    assert v2.arrived_at >= v.arrived_at


# ── DwellAttentionPolicy ──────────────────────────────────────────────────────

def test_dwell_policy_threshold_property():
    p = DwellAttentionPolicy(min_attention_s=4.0)
    assert p.dwell_threshold_s == 4.0


def test_dwell_policy_below_threshold_dropped():
    p = DwellAttentionPolicy(min_attention_s=4.0, revisit_threshold=2)
    state = DocState(doc_type="presentation", doc_name="x", current_slide=1, slide_title="t")
    v = DocumentView.from_doc_state(state, "Keynote", "x")
    assert p.worth_capturing(v, dwell_so_far_s=1.0) is False


def test_dwell_policy_above_threshold_captured():
    p = DwellAttentionPolicy(min_attention_s=4.0)
    state = DocState(doc_type="presentation", doc_name="x", current_slide=1, slide_title="t")
    v = DocumentView.from_doc_state(state, "Keynote", "x")
    assert p.worth_capturing(v, dwell_so_far_s=5.0) is True


def test_dwell_policy_revisit_short_dwell_captured():
    p = DwellAttentionPolicy(min_attention_s=10.0, revisit_threshold=2, revisit_min_dwell_s=1.5)
    state = DocState(doc_type="presentation", doc_name="x", current_slide=1, slide_title="t")
    v = DocumentView.from_doc_state(state, "Keynote", "x").with_revisit(2)
    assert p.worth_capturing(v, dwell_so_far_s=2.0) is True


def test_dwell_policy_revisit_too_short_still_dropped():
    p = DwellAttentionPolicy(min_attention_s=10.0, revisit_threshold=2, revisit_min_dwell_s=1.5)
    state = DocState(doc_type="presentation", doc_name="x", current_slide=1, slide_title="t")
    v = DocumentView.from_doc_state(state, "Keynote", "x").with_revisit(2)
    assert p.worth_capturing(v, dwell_so_far_s=0.5) is False


# ── ContentExtractionChain ────────────────────────────────────────────────────

class _StubExtractor(ContentExtractor):
    def __init__(self, name: str, *, available: bool = True, text: str | None = "hi",
                 raises: bool = False, source: str = "stub"):
        self.name = name
        self._available = available
        self._text = text
        self._raises = raises
        self._source = source
        self.calls = 0

    def available(self) -> bool:
        return self._available

    def extract(self, view):
        self.calls += 1
        if self._raises:
            raise RuntimeError("boom")
        if self._text is None:
            return None
        return ExtractedContent(text=self._text, source=self._source, item_label=view.item_label)


def _make_view():
    state = DocState(doc_type="presentation", doc_name="x", current_slide=1, slide_title="t")
    return DocumentView.from_doc_state(state, "Keynote", "x")


def test_chain_returns_first_available_result():
    a = _StubExtractor("a", available=True, text="from-a")
    b = _StubExtractor("b", available=True, text="from-b")
    chain = ContentExtractionChain([a, b])
    out = chain.extract(_make_view())
    assert out and out.text == "from-a"
    assert a.calls == 1
    assert b.calls == 0


def test_chain_skips_unavailable():
    a = _StubExtractor("a", available=False)
    b = _StubExtractor("b", available=True, text="from-b")
    chain = ContentExtractionChain([a, b])
    out = chain.extract(_make_view())
    assert out and out.text == "from-b"


def test_chain_skips_extractor_that_raises():
    a = _StubExtractor("a", available=True, raises=True)
    b = _StubExtractor("b", available=True, text="from-b")
    chain = ContentExtractionChain([a, b])
    out = chain.extract(_make_view())
    assert out and out.text == "from-b"


def test_chain_returns_none_when_all_extractors_yield_none():
    a = _StubExtractor("a", available=True, text=None)
    b = _StubExtractor("b", available=True, text="")
    chain = ContentExtractionChain([a, b])
    assert chain.extract(_make_view()) is None


def test_chain_usable_property():
    assert ContentExtractionChain([_StubExtractor("a", available=False)]).usable is False
    assert ContentExtractionChain([_StubExtractor("a", available=True)]).usable is True


# ── DocumentContentTracker ────────────────────────────────────────────────────

def _state(slide: int, doc_path: str = "/x/Deck.pptx", title: str = "t"):
    return DocState(
        doc_type="presentation", doc_name="Deck.pptx", doc_path=doc_path,
        app="Keynote", current_slide=slide, slide_title=title,
    )


def _make_tracker(text="slide content here", *, dwell_s=0.5,
                  max_items_per_doc=80, max_chars=8000):
    """Build a tracker. Note: DwellAttentionPolicy floors dwell at 0.5s, so any
    dwell_s < 0.5 is silently bumped — tests must respect that and pace waits
    accordingly."""
    captured: list = []

    async def emit(event):
        captured.append(event)

    extractor = _StubExtractor("stub", available=True, text=text, source="stub")
    chain = ContentExtractionChain([extractor])
    policy = DwellAttentionPolicy(min_attention_s=dwell_s, revisit_threshold=2,
                                  revisit_min_dwell_s=0.2)
    return (
        DocumentContentTracker(emit, chain, policy,
                               max_items_per_doc=max_items_per_doc,
                               max_chars=max_chars),
        captured,
        extractor,
    )


# Dwell threshold is floored at 0.5s by the policy; tests use 0.5s and pace waits
# below that for "scroll-through" cases and above for "dwelled" cases.

@pytest.mark.asyncio
async def test_quick_scroll_through_emits_nothing():
    tracker, captured, _ = _make_tracker(dwell_s=0.5)
    # Land on five slides much faster than the dwell threshold
    for i in range(1, 6):
        tracker.note_navigation(_state(i), "Keynote", "Deck.pptx")
        await asyncio.sleep(0.05)   # 5 × 0.05 = 0.25s total, below 0.5 threshold
    # Immediately leave the document so the last pending capture is cancelled too.
    tracker.note_doc_closed()
    await asyncio.sleep(0.7)
    assert captured == []
    assert tracker.captured == 0
    tracker.stop()


@pytest.mark.asyncio
async def test_dwelling_long_enough_emits_one_event():
    tracker, captured, extractor = _make_tracker(text="real content", dwell_s=0.5)
    tracker.note_navigation(_state(7, title="Pricing"), "Keynote", "Deck.pptx")
    await asyncio.sleep(0.9)
    assert len(captured) == 1
    ev = captured[0]
    assert ev.type == EventType.DOC_CONTENT
    assert ev.payload["item_kind"] == "slide"
    assert ev.payload["item_index"] == 7
    assert ev.payload["item_label"] == "Pricing"
    assert ev.payload["text"] == "real content"
    assert ev.payload["source"] == "stub"
    assert ev.payload["content_hash"]
    assert extractor.calls == 1
    tracker.stop()


@pytest.mark.asyncio
async def test_dedup_when_same_text_extracted_twice():
    tracker, captured, _ = _make_tracker(text="identical body", dwell_s=0.5)
    tracker.note_navigation(_state(3, title="A"), "Keynote", "Deck.pptx")
    await asyncio.sleep(0.9)
    # Move briefly to another slide (won't trigger — quick), then back
    tracker.note_navigation(_state(99), "Keynote", "Deck.pptx")
    await asyncio.sleep(0.1)
    tracker.note_navigation(_state(3, title="A"), "Keynote", "Deck.pptx")
    await asyncio.sleep(0.9)
    # Even though we visited slide 3 twice, only one event for that key.
    by_key = [(e.payload["item_index"], e.payload["item_label"]) for e in captured]
    assert by_key.count((3, "A")) == 1
    tracker.stop()


@pytest.mark.asyncio
async def test_per_doc_cap_honored():
    tracker, captured, _ = _make_tracker(text="x", dwell_s=0.5, max_items_per_doc=2)
    for i in range(1, 6):
        tracker.note_navigation(_state(i, title=f"t{i}"), "Keynote", "Deck.pptx")
        await asyncio.sleep(0.8)  # let each capture fire (> 0.5)
    assert len(captured) == 2
    tracker.stop()


@pytest.mark.asyncio
async def test_text_truncated_to_max_chars():
    long_text = "z" * 50_000
    tracker, captured, _ = _make_tracker(text=long_text, dwell_s=0.5, max_chars=500)
    tracker.note_navigation(_state(1), "Keynote", "Deck.pptx")
    await asyncio.sleep(0.9)
    assert len(captured) == 1
    assert captured[0].payload["text_chars"] == 500


@pytest.mark.asyncio
async def test_doc_closed_cancels_pending():
    tracker, captured, _ = _make_tracker(text="x", dwell_s=0.5)
    tracker.note_navigation(_state(1), "Keynote", "Deck.pptx")
    await asyncio.sleep(0.1)
    tracker.note_doc_closed()
    await asyncio.sleep(0.7)
    assert captured == []
    tracker.stop()
