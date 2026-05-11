"""Tests for document navigation detection."""
import time
import pytest
from capman.events import Event, EventType, DocState
from capman.platform.base import classify_app, parse_doc_state_from_title
from capman.sensors.documents import _nav_direction, DocumentSensor


# ── App classification ────────────────────────────────────────────────────────

def test_classify_powerpoint():
    assert classify_app("Microsoft PowerPoint") == "presentation"
    assert classify_app("POWERPNT.EXE") == "presentation"


def test_classify_keynote():
    assert classify_app("Keynote") == "presentation"


def test_classify_libreoffice_impress():
    assert classify_app("soffice (LibreOffice Impress)") == "presentation"


def test_classify_word():
    assert classify_app("Microsoft Word") == "document"
    assert classify_app("WINWORD.EXE") == "document"


def test_classify_excel():
    assert classify_app("Microsoft Excel") == "spreadsheet"


def test_classify_notes():
    assert classify_app("Notes") == "notes"
    assert classify_app("Apple Notes") == "notes"
    assert classify_app("OneNote") == "notes"


def test_classify_pdf_viewers():
    assert classify_app("Evince") == "pdf"
    assert classify_app("Okular") == "pdf"
    assert classify_app("Preview") == "pdf"


def test_classify_unknown_returns_none():
    assert classify_app("Slack") is None
    assert classify_app("Terminal") is None
    assert classify_app("Chrome") is None


# ── Title parsing ─────────────────────────────────────────────────────────────

def test_parse_slide_from_title():
    state = parse_doc_state_from_title("Microsoft PowerPoint", "Q1 Results - Microsoft PowerPoint")
    assert state is not None
    assert state.doc_type == "presentation"
    assert state.doc_name == "Q1 Results"


def test_parse_slide_number_from_title():
    state = parse_doc_state_from_title(
        "Keynote", "My Deck - Keynote (Slide 5 of 20)"
    )
    assert state is not None
    assert state.current_slide == 5
    assert state.total_slides == 20


def test_parse_page_number_from_title():
    state = parse_doc_state_from_title(
        "Evince", "Report.pdf [Page 3 of 45] - Evince"
    )
    assert state is not None
    assert state.doc_type == "pdf"
    assert state.current_page == 3
    assert state.total_pages == 45


def test_parse_word_doc_name():
    state = parse_doc_state_from_title("Microsoft Word", "Budget 2026.docx - Word")
    assert state is not None
    assert state.doc_name == "Budget 2026.docx"


def test_unknown_app_returns_none():
    state = parse_doc_state_from_title("Slack", "#general - Slack")
    assert state is None


# ── Navigation direction ──────────────────────────────────────────────────────

def test_nav_direction_first():
    assert _nav_direction(0, 1) == "first"


def test_nav_direction_forward():
    assert _nav_direction(3, 4) == "forward"


def test_nav_direction_backward():
    assert _nav_direction(5, 4) == "backward"


def test_nav_direction_jump():
    assert _nav_direction(2, 8) == "jump"
    assert _nav_direction(10, 3) == "jump"


# ── Diff-to-event logic ───────────────────────────────────────────────────────

def _sensor() -> DocumentSensor:
    import asyncio
    q = asyncio.Queue()
    return DocumentSensor(config={}, queue=q)


def test_first_doc_open_emits_open_event():
    s = _sensor()
    state = DocState(doc_type="presentation", doc_name="Deck.pptx", app="Keynote",
                     current_slide=1, total_slides=10)
    event = s._diff_to_event(state, None, time.time(), "Keynote", "Deck.pptx - Keynote")
    assert event is not None
    assert event.type == EventType.DOC_OPEN
    assert event.payload["doc_name"] == "Deck.pptx"


def test_slide_change_emits_slide_event():
    s = _sensor()
    prev = DocState(doc_type="presentation", doc_name="Deck.pptx", app="Keynote",
                    current_slide=2, total_slides=10)
    curr = DocState(doc_type="presentation", doc_name="Deck.pptx", app="Keynote",
                    current_slide=5, total_slides=10, slide_title="Revenue")
    event = s._diff_to_event(curr, prev, time.time() - 30, "Keynote", "Deck.pptx")
    assert event is not None
    assert event.type == EventType.DOC_SLIDE_CHANGE
    assert event.payload["current_slide"] == 5
    assert event.payload["prev_slide"] == 2
    assert event.payload["nav_direction"] == "jump"
    assert event.payload["dwell_s"] >= 0


def test_no_event_when_slide_unchanged():
    s = _sensor()
    state = DocState(doc_type="presentation", doc_name="Deck.pptx",
                     current_slide=3, total_slides=10)
    event = s._diff_to_event(state, state, time.time(), "Keynote", "Deck.pptx")
    assert event is None


def test_page_change_emits_page_event():
    s = _sensor()
    prev = DocState(doc_type="document", doc_name="Report.docx", current_page=1, total_pages=20)
    curr = DocState(doc_type="document", doc_name="Report.docx", current_page=2, total_pages=20,
                    section_heading="Introduction")
    event = s._diff_to_event(curr, prev, time.time() - 45, "Microsoft Word", "Report.docx")
    assert event is not None
    assert event.type == EventType.DOC_PAGE_CHANGE
    assert event.payload["current_page"] == 2
    assert event.payload["nav_direction"] == "forward"


def test_sheet_change_emits_sheet_event():
    s = _sensor()
    prev = DocState(doc_type="spreadsheet", doc_name="Budget.xlsx", sheet_name="Q1")
    curr = DocState(doc_type="spreadsheet", doc_name="Budget.xlsx", sheet_name="Q2")
    event = s._diff_to_event(curr, prev, time.time(), "Microsoft Excel", "Budget.xlsx")
    assert event is not None
    assert event.type == EventType.DOC_SHEET_CHANGE
    assert event.payload["sheet_name"] == "Q2"
    assert event.payload["prev_sheet"] == "Q1"


def test_doc_name_change_emits_open_event():
    s = _sensor()
    prev = DocState(doc_type="presentation", doc_name="OldDeck.pptx", current_slide=3)
    curr = DocState(doc_type="presentation", doc_name="NewDeck.pptx", current_slide=1)
    event = s._diff_to_event(curr, prev, time.time(), "Keynote", "NewDeck.pptx")
    assert event is not None
    assert event.type == EventType.DOC_OPEN


def test_note_change_emits_note_event():
    s = _sensor()
    prev = DocState(doc_type="notes", note_title="Old Note", notebook="Work")
    curr = DocState(doc_type="notes", note_title="Meeting Notes", notebook="Work")
    event = s._diff_to_event(curr, prev, time.time(), "Notes", "Notes")
    assert event is not None
    assert event.type == EventType.DOC_NOTE_OPEN
    assert event.payload["note_title"] == "Meeting Notes"
