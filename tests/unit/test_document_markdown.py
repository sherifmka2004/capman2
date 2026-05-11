"""Tests for document node markdown generation."""
import time
import pytest
from pathlib import Path
from capman.knowledge.markdown import save_document_node, _infer_nav_pattern, _build_nav_table


def _pptx_events(slides: list[tuple[int, str, float]]) -> list[dict]:
    """Helper: [(slide_num, title, dwell_s), ...]"""
    events = []
    prev = 0
    for slide, title, dwell in slides:
        events.append({
            "doc_type": "presentation",
            "doc_name": "Q1 Results.pptx",
            "doc_path": "/docs/Q1 Results.pptx",
            "app": "Keynote",
            "current_slide": slide,
            "total_slides": 20,
            "slide_title": title,
            "prev_slide": prev,
            "dwell_s": dwell,
            "nav_direction": "forward" if slide == prev + 1 else ("backward" if slide < prev else "jump"),
        })
        prev = slide
    return events


def _docx_events(pages: list[tuple[int, str, float]]) -> list[dict]:
    """Helper: [(page_num, heading, dwell_s), ...]"""
    events = []
    prev = 0
    for page, heading, dwell in pages:
        events.append({
            "doc_type": "document",
            "doc_name": "Report.docx",
            "doc_path": "/docs/Report.docx",
            "app": "Microsoft Word",
            "current_page": page,
            "total_pages": 15,
            "section_heading": heading,
            "dwell_s": dwell,
            "nav_direction": "forward" if page > prev else "backward",
        })
        prev = page
    return events


def test_presentation_node_created(tmp_path):
    events = _pptx_events([(1, "Intro", 30), (2, "Revenue", 120), (3, "Costs", 60)])
    path = save_document_node(events, "sess-abc", time.time() - 300, tmp_path)
    assert path is not None
    assert path.exists()
    content = path.read_text()
    assert "Q1 Results.pptx" in content
    assert "presentation" in content
    assert "Keynote" in content


def test_presentation_node_has_navigation_table(tmp_path):
    events = _pptx_events([(1, "Intro", 30), (3, "Revenue", 120), (2, "Costs", 60)])
    path = save_document_node(events, "sess-abc", time.time() - 300, tmp_path)
    content = path.read_text()
    assert "| Slide |" in content
    assert "| 1 |" in content
    assert "| 3 |" in content
    assert "Revenue" in content
    assert "120s" in content


def test_presentation_node_has_nav_pattern(tmp_path):
    events = _pptx_events([(1, "A", 10), (3, "B", 20), (2, "C", 15), (5, "D", 30)])
    path = save_document_node(events, "sess-abc", time.time() - 300, tmp_path)
    content = path.read_text()
    assert "Navigation pattern" in content
    # Non-linear due to jump and backward move
    assert any(word in content for word in ["Non-linear", "Jump", "jump"])


def test_presentation_node_has_hotspots(tmp_path):
    events = _pptx_events([(1, "Intro", 10), (2, "Revenue", 300), (3, "Costs", 45)])
    path = save_document_node(events, "sess-abc", time.time() - 400, tmp_path)
    content = path.read_text()
    assert "Most Studied" in content
    assert "Revenue" in content  # Highest dwell should appear


def test_document_node_created(tmp_path):
    events = _docx_events([(1, "Executive Summary", 60), (2, "Background", 90), (3, "Methods", 45)])
    path = save_document_node(events, "sess-def", time.time() - 200, tmp_path)
    assert path is not None
    content = path.read_text()
    assert "Report.docx" in content
    assert "| Page |" in content
    assert "Executive Summary" in content


def test_session_appended_on_second_run(tmp_path):
    events = _pptx_events([(1, "A", 30), (2, "B", 60)])
    save_document_node(events, "sess-1", time.time() - 300, tmp_path)

    events2 = _pptx_events([(3, "C", 45), (4, "D", 90)])
    save_document_node(events2, "sess-2", time.time() - 100, tmp_path)

    path = list((tmp_path / "docs" / "presentation").glob("*.md"))[0]
    content = path.read_text()
    assert "sess-1" in content
    assert "sess-2" in content
    assert content.count("### Session") == 2


def test_spreadsheet_node_created(tmp_path):
    events = [
        {"doc_type": "spreadsheet", "doc_name": "Budget.xlsx", "doc_path": "", "app": "Excel",
         "sheet_name": "Q1", "prev_sheet": "", "sheet_index": 1, "dwell_s": 45},
        {"doc_type": "spreadsheet", "doc_name": "Budget.xlsx", "doc_path": "", "app": "Excel",
         "sheet_name": "Q2", "prev_sheet": "Q1", "sheet_index": 2, "dwell_s": 90},
    ]
    path = save_document_node(events, "sess-xyz", time.time(), tmp_path)
    assert path is not None
    content = path.read_text()
    assert "Budget.xlsx" in content
    assert "Q1" in content
    assert "Q2" in content


def test_nav_pattern_linear():
    events = _pptx_events([(1, "A", 10), (2, "B", 10), (3, "C", 10)])
    pattern = _infer_nav_pattern("presentation", events)
    assert "Linear" in pattern or "linear" in pattern


def test_nav_pattern_nonlinear_backtrack():
    events = _pptx_events([(1, "A", 10), (2, "B", 10), (1, "A", 5), (3, "C", 10)])
    pattern = _infer_nav_pattern("presentation", events)
    assert "backward" in pattern or "Non-linear" in pattern


def test_empty_events_returns_none(tmp_path):
    result = save_document_node([], "sess-empty", time.time(), tmp_path)
    assert result is None
