"""Unit tests for prompt templates and event narrative builder."""
import time
import pytest
from capman.events import Event, EventType, Session
from capman.pipeline.prompts import (
    PASS1_SUMMARIZE,
    PASS2_CHAIN_OF_THOUGHT,
    PASS3_TRIPLE_EXTRACT,
    build_event_narrative,
)


def _make_session(*event_specs) -> Session:
    """Build a Session from (EventType, app, payload) tuples."""
    session = Session(dominant_app="Chrome", primary_domain="example.com")
    start = time.time() - 300
    session.started_at = start
    for i, (etype, app, payload) in enumerate(event_specs):
        e = Event(type=etype, app=app, payload=payload)
        e.ts = start + i * 30
        session.events.append(e)
    return session


# ---------------------------------------------------------------------------
# Template format sanity
# ---------------------------------------------------------------------------

def test_pass1_template_formats():
    rendered = PASS1_SUMMARIZE.format(
        duration_minutes=15.0,
        dominant_app="Chrome",
        primary_domain="stackoverflow.com",
        search_queries='["react hydration"]',
        urls_visited='["https://react.dev"]',
        commands_run='["npm run dev"]',
        files='[]',
    )
    assert "15.0 minutes" in rendered
    assert "react hydration" in rendered
    assert "stackoverflow.com" in rendered
    assert "problem_statement" in rendered
    assert "reusability_estimate" in rendered


def test_pass2_template_formats():
    rendered = PASS2_CHAIN_OF_THOUGHT.format(
        duration_minutes=23.5,
        dominant_app="Terminal",
        problem_statement="User was debugging a memory leak",
        approach_description="Used valgrind to trace allocation",
        event_narrative="[+00:00] CMD | Terminal | valgrind ./app",
    )
    assert "23.5 minutes" in rendered
    assert "memory leak" in rendered
    assert "valgrind ./app" in rendered
    assert "methodology_pattern" in rendered
    assert "reusability_score" in rendered


def test_pass3_template_formats():
    rendered = PASS3_TRIPLE_EXTRACT.format(
        problem_statement="Debugging React hydration",
        approach_description="Used suppressHydrationWarning",
        methodology_tags='["docs-first"]',
        knowledge_acquired='["suppressHydrationWarning prop"]',
        methodology_pattern="search → docs → apply",
        # Pass 3 is given the raw trail as well as the summary, so it can ground
        # triples in specific error messages, commands and versions rather than
        # only in the paraphrase produced by pass 1.
        event_narrative="[+00:12] SHELL_COMMAND | Terminal | npm run dev",
    )
    assert "subject" in rendered
    assert "predicate" in rendered
    assert "confidence" in rendered
    assert "is_caused_by" in rendered
    assert "npm run dev" in rendered


# ---------------------------------------------------------------------------
# build_event_narrative
# ---------------------------------------------------------------------------

def test_narrative_search_query():
    session = _make_session(
        (EventType.SEARCH_QUERY, "Chrome", {"engine": "google", "query": "python asyncio", "url": "https://google.com"}),
    )
    narrative = build_event_narrative(session)
    assert "SEARCH" in narrative
    assert "python asyncio" in narrative
    assert "google" in narrative


def test_narrative_url_visit():
    session = _make_session(
        (EventType.URL_VISIT, "Chrome", {"url": "https://docs.python.org/asyncio", "title": "asyncio — Python", "visit_duration_s": 120.0, "referrer": ""}),
    )
    narrative = build_event_narrative(session)
    assert "URL" in narrative
    assert "docs.python.org" in narrative


def test_narrative_shell_command():
    session = _make_session(
        (EventType.SHELL_COMMAND, "Terminal", {"command": "pytest -xvs tests/", "cwd": "/app", "shell": "bash", "command_id": ""}),
    )
    narrative = build_event_narrative(session)
    assert "CMD" in narrative
    assert "pytest -xvs tests/" in narrative


def test_narrative_shell_output():
    session = _make_session(
        (EventType.SHELL_OUTPUT, "Terminal", {"stdout": "5 passed in 0.3s", "stderr": "", "exit_code": 0, "command_id": ""}),
    )
    narrative = build_event_narrative(session)
    assert "OUTPUT" in narrative
    assert "exit 0" in narrative
    assert "5 passed" in narrative


def test_narrative_clipboard_copy_paste():
    session = _make_session(
        (EventType.CLIPBOARD_COPY, "Chrome", {"content": "suppressHydrationWarning", "content_type": "text", "char_count": 24}),
        (EventType.CLIPBOARD_PASTE, "VSCode", {"content": "suppressHydrationWarning", "target_app": "VSCode"}),
    )
    narrative = build_event_narrative(session)
    assert "COPY" in narrative
    assert "PASTE" in narrative
    assert "suppressHydrationWarning" in narrative


def test_narrative_file_save():
    session = _make_session(
        (EventType.FILE_SAVE, "VSCode", {"path": "/app/src/layout.tsx", "extension": ".tsx", "size_bytes": 1024}),
    )
    narrative = build_event_narrative(session)
    assert "SAVE" in narrative
    assert "layout.tsx" in narrative


def test_narrative_file_open_delete_rename():
    session = _make_session(
        (EventType.FILE_OPEN, "kitty", {"path": "/app/src/new.py", "extension": ".py"}),
        (EventType.FILE_DELETE, "kitty", {"path": "/app/old.py", "extension": ".py"}),
        (EventType.FILE_RENAME, "Finder", {"src_path": "/app/a.py", "dest_path": "/app/b.py"}),
    )
    narrative = build_event_narrative(session)
    assert "OPEN" in narrative and "new.py" in narrative
    assert "DELETE" in narrative and "old.py" in narrative
    assert "RENAME" in narrative and "a.py" in narrative and "b.py" in narrative


def test_narrative_code_diff_with_excerpt():
    diff_text = (
        "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,3 @@\n"
        " import os\n-x = 1\n+x = 2\n+y = 3\n"
    )
    session = _make_session(
        (EventType.CODE_DIFF, "vim", {
            "path": "/app/app.py", "extension": ".py", "diff": diff_text,
            "lines_added": 2, "lines_removed": 1, "repo": "myproj", "branch": "main",
        }),
    )
    narrative = build_event_narrative(session)
    assert "DIFF" in narrative
    assert "app.py" in narrative
    assert "[myproj]" in narrative
    assert "+2" in narrative and "-1" in narrative
    assert "x = 2" in narrative  # excerpt rendered


def test_narrative_slide_change():
    session = _make_session(
        (EventType.DOC_SLIDE_CHANGE, "PowerPoint", {
            "doc_name": "Q1.pptx", "doc_type": "presentation", "app": "PowerPoint",
            "current_slide": 5, "total_slides": 20, "slide_title": "Revenue",
            "prev_slide": 4, "dwell_s": 47.0, "nav_direction": "forward",
        }),
    )
    narrative = build_event_narrative(session)
    assert "SLIDE" in narrative
    assert "5/20" in narrative
    assert "Revenue" in narrative
    assert "forward" in narrative
    assert "47s" in narrative


def test_narrative_page_change():
    session = _make_session(
        (EventType.DOC_PAGE_CHANGE, "Word", {
            "doc_name": "Report.docx", "doc_type": "document", "app": "Word",
            "current_page": 3, "total_pages": 15, "section_heading": "Methods",
            "dwell_s": 65.0, "nav_direction": "forward",
        }),
    )
    narrative = build_event_narrative(session)
    assert "PAGE" in narrative
    assert "3/15" in narrative
    assert "Methods" in narrative


def test_narrative_sheet_change():
    session = _make_session(
        (EventType.DOC_SHEET_CHANGE, "Excel", {
            "doc_name": "Budget.xlsx", "doc_type": "spreadsheet", "app": "Excel",
            "sheet_name": "Q2", "prev_sheet": "Q1", "sheet_index": 2, "dwell_s": 90.0,
        }),
    )
    narrative = build_event_narrative(session)
    assert "SHEET" in narrative
    assert "Q2" in narrative
    assert "Q1" in narrative


def test_narrative_note_open():
    session = _make_session(
        (EventType.DOC_NOTE_OPEN, "Obsidian", {
            "doc_name": "React Notes", "doc_type": "notes", "app": "Obsidian",
            "note_title": "React Hydration", "notebook": "Dev Notes",
        }),
    )
    narrative = build_event_narrative(session)
    assert "NOTE" in narrative
    assert "React Hydration" in narrative
    assert "Dev Notes" in narrative


def test_narrative_empty_session():
    session = Session(dominant_app="Chrome")
    session.started_at = time.time()
    narrative = build_event_narrative(session)
    assert "no significant events" in narrative


def test_narrative_deduplicates_rapid_url_revisits():
    """Same URL visited twice within 10s should appear only once."""
    session = Session(dominant_app="Chrome")
    session.started_at = time.time() - 60
    for i in range(2):
        e = Event(type=EventType.URL_VISIT, app="Chrome",
                  payload={"url": "https://example.com", "title": "Ex", "referrer": "", "visit_duration_s": 5.0})
        e.ts = session.started_at + i * 3  # 3 seconds apart — within 10s window
        session.events.append(e)
    narrative = build_event_narrative(session)
    assert narrative.count("example.com") == 1


def test_narrative_includes_slow_keystroke():
    """Keystrokes over 20 chars should appear in narrative."""
    session = _make_session(
        (EventType.KEYSTROKE, "VSCode", {"text": "async def handle_request(self, req):", "is_paste": False, "field_type": "text"}),
    )
    narrative = build_event_narrative(session)
    assert "TYPE" in narrative
    assert "async def" in narrative


def test_narrative_skips_short_keystroke():
    """Keystrokes under 20 chars should be omitted (noise)."""
    session = _make_session(
        (EventType.KEYSTROKE, "VSCode", {"text": "hi", "is_paste": False, "field_type": "text"}),
    )
    narrative = build_event_narrative(session)
    assert "TYPE" not in narrative


def test_narrative_timestamp_offsets():
    """Timestamps should display as MM:SS offsets from session start."""
    session = Session(dominant_app="Terminal")
    session.started_at = 1000.0
    e = Event(type=EventType.SHELL_COMMAND, app="Terminal",
              payload={"command": "ls", "cwd": "", "shell": "bash", "command_id": ""})
    e.ts = 1075.0  # +01:15
    session.events.append(e)
    narrative = build_event_narrative(session)
    assert "+01:15" in narrative


def test_narrative_ocr_screenshot():
    """Screenshots with OCR text should appear in narrative."""
    session = _make_session(
        (EventType.SCREENSHOT, "mss", {"path": "/tmp/screen.png", "trigger": "periodic", "ocr_text": "Error: Cannot read property of undefined"}),
    )
    narrative = build_event_narrative(session)
    assert "SCREEN" in narrative
    assert "Cannot read property" in narrative


# ---------------------------------------------------------------------------
# Mouse + idle narrative cases
# ---------------------------------------------------------------------------

def test_narrative_click_with_element_renders():
    session = _make_session(
        (EventType.MOUSE_CLICK, "PyCharm", {
            "button": "left", "x": 10, "y": 20,
            "element": {"role": "AXButton", "label": "Run all", "value": "", "app": "PyCharm"},
        }),
    )
    narrative = build_event_narrative(session)
    assert "CLICK" in narrative
    assert "Run all" in narrative
    assert "AXButton" in narrative


def test_narrative_click_without_element_is_silent():
    session = _make_session(
        (EventType.MOUSE_CLICK, "Chrome", {"button": "left", "x": 10, "y": 20}),
    )
    narrative = build_event_narrative(session)
    assert "CLICK" not in narrative


def test_narrative_long_scroll_burst_renders():
    session = _make_session(
        (EventType.MOUSE_SCROLL, "Chrome", {
            "direction": "down", "ticks": 25, "duration_s": 6.4,
            "delta_total": 25, "dx": 0, "dy": -25,
            "start_x": 0, "start_y": 0, "end_x": 0, "end_y": 0,
        }),
    )
    narrative = build_event_narrative(session)
    assert "SCROLL" in narrative
    assert "down" in narrative


def test_narrative_short_scroll_is_silent():
    session = _make_session(
        (EventType.MOUSE_SCROLL, "Chrome", {
            "direction": "down", "ticks": 4, "duration_s": 0.3,
            "delta_total": 4, "dx": 0, "dy": -4,
            "start_x": 0, "start_y": 0, "end_x": 0, "end_y": 0,
        }),
    )
    narrative = build_event_narrative(session)
    assert "SCROLL" not in narrative


def test_narrative_idle_start_and_end():
    session = _make_session(
        (EventType.IDLE_START, "", {"last_input_ts": 1000.0}),
        (EventType.IDLE_END, "", {"idle_started_at": 1000.0, "idle_duration_s": 240.0}),
    )
    narrative = build_event_narrative(session)
    assert "AFK" in narrative
    assert "went idle" in narrative
    assert "returned" in narrative
    assert "240" in narrative


def test_narrative_heatmap_tick_is_never_rendered():
    session = _make_session(
        (EventType.MOUSE_HEATMAP_TICK, "Chrome", {
            "app": "Chrome", "minute_bucket": 1000,
            "grid": {"0,0": 5}, "grid_size": 100, "screen_size": [100, 100],
        }),
    )
    narrative = build_event_narrative(session)
    # Heatmap is a data-only event — should produce no narrative line at all.
    # The session contains only one event so an empty result yields the
    # "no significant events" placeholder.
    assert "HEATMAP" not in narrative
    assert "MOUSE" not in narrative
