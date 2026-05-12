"""
All data models for capman2. Every other module imports from here.
This is the canonical source of truth for all event and knowledge types.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    KEYSTROKE        = "keystroke"
    CLIPBOARD_COPY   = "clipboard_copy"
    CLIPBOARD_PASTE  = "clipboard_paste"
    MOUSE_CLICK      = "mouse_click"
    WINDOW_FOCUS     = "window_focus"
    WINDOW_BLUR      = "window_blur"
    TAB_OPEN         = "tab_open"
    TAB_CLOSE        = "tab_close"
    URL_VISIT        = "url_visit"
    SEARCH_QUERY     = "search_query"
    PAGE_TEXT        = "page_text"
    SCREENSHOT       = "screenshot"
    FILE_OPEN        = "file_open"
    FILE_SAVE        = "file_save"
    SHELL_COMMAND    = "shell_command"
    SHELL_OUTPUT     = "shell_output"
    SESSION_START    = "session_start"
    SESSION_END      = "session_end"
    # Document navigation events
    DOC_OPEN         = "doc_open"          # Document/note opened or focused
    DOC_SLIDE_CHANGE = "doc_slide_change"  # Slide navigation in presentation apps
    DOC_PAGE_CHANGE  = "doc_page_change"   # Page navigation in word processors / PDFs
    DOC_SHEET_CHANGE = "doc_sheet_change"  # Sheet tab switch in spreadsheets
    DOC_NOTE_OPEN    = "doc_note_open"     # Note opened in notes apps
    # Code & error capture
    CODE_DIFF        = "code_diff"         # File diff after save (before/after content)
    ERROR_DETECTED   = "error_detected"    # Stack trace or error message in output
    AI_CONVERSATION  = "ai_conversation"   # Captured ChatGPT/Claude/etc. exchange
    # In-page interaction events (from browser extension)
    USER_CLICK       = "user_click"        # Click on a button/link/element
    FORM_INPUT       = "form_input"        # Text typed into an input field
    FORM_SUBMIT      = "form_submit"       # Form submission
    DOM_MUTATION     = "dom_mutation"      # Significant DOM change (modal opened, etc.)


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Event:
    """
    Atomic unit of capture. Every sensor emits these into the async queue.

    Payload schema per type:
      KEYSTROKE:       {"text": str, "is_paste": bool, "field_type": "text|password|search"}
      CLIPBOARD_COPY:  {"content": str, "content_type": "text|html|image", "char_count": int}
      CLIPBOARD_PASTE: {"content": str, "target_app": str}
      URL_VISIT:       {"url": str, "title": str, "referrer": str, "visit_duration_s": float}
      SEARCH_QUERY:    {"engine": str, "query": str, "url": str, "result_count": int}
      PAGE_TEXT:       {"url": str, "title": str, "excerpt": str, "headings": list[str]}
      SCREENSHOT:      {"path": str, "trigger": "periodic|event", "ocr_text": str}
      SHELL_COMMAND:   {"command": str, "cwd": str, "shell": str, "command_id": str}
      SHELL_OUTPUT:    {"stdout": str, "stderr": str, "exit_code": int, "command_id": str}
      FILE_OPEN/SAVE:  {"path": str, "extension": str, "size_bytes": int}
    """
    type: EventType = EventType.WINDOW_FOCUS
    app: str = ""
    window_title: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    sensor_id: str = ""
    id: str = field(default_factory=_new_id)
    ts: float = field(default_factory=time.time)


@dataclass
class DocState:
    """
    Structured state of the currently active document/presentation/note.
    Emitted by DocumentSensor on every navigation change.

    Payload per EventType:
      DOC_OPEN:         {"doc_type": str, "doc_name": str, "doc_path": str, "app": str}
      DOC_SLIDE_CHANGE: {"doc_type": "presentation", "doc_name": str, "doc_path": str,
                         "current_slide": int, "total_slides": int, "slide_title": str,
                         "prev_slide": int, "dwell_s": float,
                         "nav_direction": "forward|backward|jump|first"}
      DOC_PAGE_CHANGE:  {"doc_type": "document|pdf", "doc_name": str, "doc_path": str,
                         "current_page": int, "total_pages": int,
                         "section_heading": str, "dwell_s": float,
                         "nav_direction": "forward|backward|jump|first"}
      DOC_SHEET_CHANGE: {"doc_type": "spreadsheet", "doc_name": str, "doc_path": str,
                         "sheet_name": str, "prev_sheet": str, "sheet_index": int}
      DOC_NOTE_OPEN:    {"doc_type": "notes", "note_title": str, "notebook": str,
                         "doc_path": str}
    """
    doc_type: str = ""       # presentation|document|spreadsheet|notes|pdf
    doc_name: str = ""       # filename without path
    doc_path: str = ""       # full path if available
    app: str = ""
    # Slide-specific
    current_slide: int = 0
    total_slides: int = 0
    slide_title: str = ""
    prev_slide: int = 0
    # Page-specific
    current_page: int = 0
    total_pages: int = 0
    section_heading: str = ""
    # Sheet-specific
    sheet_name: str = ""
    prev_sheet: str = ""
    sheet_index: int = 0
    # Notes-specific
    note_title: str = ""
    notebook: str = ""
    # Common
    dwell_s: float = 0.0
    nav_direction: str = ""  # forward|backward|jump|first


@dataclass
class CognitiveStep:
    sequence: int = 0
    action: str = ""       # searched|read|copied|executed|pivoted|compared|annotated
    target: str = ""       # URL, query, filename, command acted on
    reasoning: str = ""    # Inferred WHY — "unknown" if cannot be determined
    duration_estimate_s: float = 0.0


@dataclass
class DecisionPoint:
    at_step: int = 0
    options_considered: list[str] = field(default_factory=list)
    chosen: str = ""
    signals: list[str] = field(default_factory=list)


@dataclass
class ChainOfThought:
    """The core differentiator — the replicable cognitive workflow."""
    session_id: str = ""
    problem_type: str = ""     # debugging|research|implementation|review|learning
    trigger: str = ""          # One sentence: what started this cognitive episode
    steps: list[CognitiveStep] = field(default_factory=list)
    decision_points: list[DecisionPoint] = field(default_factory=list)
    outcome: str = ""
    methodology_pattern: str = ""  # "docs-first → reproduce → binary-search"
    reusability_score: float = 0.0
    knowledge_gaps_revealed: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass
class Triple:
    """A knowledge graph edge (subject, predicate, object)."""
    subject: str = ""
    predicate: str = ""    # causes|requires|related_to|contradicts|enables|resolves|part_of
    object: str = ""
    confidence: float = 1.0
    source_session: str = ""
    observed_at: float = field(default_factory=time.time)
    id: str = field(default_factory=_new_id)


@dataclass
class DiagnosticStep:
    """One troubleshooting check: what to look at, why, what's expected."""
    sequence: int = 0
    action: str = ""               # "Check if X is running", "Inspect log Y"
    rationale: str = ""            # Why this check helps
    expected_signal: str = ""      # What result tells you you're on the right track
    tool: str = ""                 # Command/tool used (e.g. "kubectl logs", "tcpdump")


@dataclass
class TroubleshootingPlaybook:
    """
    Replayable problem-solving playbook extracted from a debugging session.
    The CORE differentiator: turns a one-off fix into a reusable methodology.
    """
    id: str = field(default_factory=_new_id)
    session_id: str = ""
    title: str = ""                                # "L3VPN traffic interruption diagnosis"
    domain: str = ""                               # "networking", "react", "kubernetes"
    symptoms: list[str] = field(default_factory=list)        # Triggers: error msgs, behaviors
    context_signals: list[str] = field(default_factory=list)  # When this playbook applies (e.g. "Huawei NCE", "Next.js SSR")
    diagnostic_steps: list[DiagnosticStep] = field(default_factory=list)
    root_cause: str = ""
    fix: list[str] = field(default_factory=list)             # Concrete action steps
    verification: list[str] = field(default_factory=list)    # How to confirm the fix worked
    references: list[str] = field(default_factory=list)      # URLs / docs consulted
    related_playbooks: list[str] = field(default_factory=list)
    reusability_score: float = 0.0
    created_at: float = field(default_factory=time.time)


@dataclass
class KnowledgeGap:
    """A concept the user repeatedly looks up — sign of incomplete mastery."""
    id: str = field(default_factory=_new_id)
    concept: str = ""                              # Normalized concept name
    domain: str = ""                               # "networking", "react", etc.
    lookup_count: int = 1
    query_examples: list[str] = field(default_factory=list)  # Verbatim search queries
    sessions: list[str] = field(default_factory=list)        # Where it was looked up
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    resolved: bool = False                          # Marked once user explicitly says they learned it


@dataclass
class SessionAnalysis:
    session_id: str = ""
    problem_statement: str = ""
    approach_description: str = ""
    methodology_tags: list[str] = field(default_factory=list)
    knowledge_applied: list[str] = field(default_factory=list)
    knowledge_acquired: list[str] = field(default_factory=list)
    triples: list[Triple] = field(default_factory=list)
    chain_of_thought: ChainOfThought | None = None
    playbook: TroubleshootingPlaybook | None = None  # Pass 4: only for debugging sessions
    confidence: float = 0.0
    model_used: str = ""
    analyzed_at: float = field(default_factory=time.time)


@dataclass
class Session:
    id: str = field(default_factory=_new_id)
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    events: list[Event] = field(default_factory=list)
    dominant_app: str = ""
    primary_domain: str = ""
    search_queries: list[str] = field(default_factory=list)
    urls_visited: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    analysis: SessionAnalysis | None = None


@dataclass
class KnowledgeEdge:
    predicate: str = ""    # causes|requires|related_to|contradicts|enables|part_of
    target_id: str = ""
    weight: float = 1.0
    observed_count: int = 1
    last_observed: float = field(default_factory=time.time)


@dataclass
class KnowledgeNode:
    title: str = ""
    node_type: str = ""    # concept|technology|pattern|tool|methodology|domain
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    first_seen: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)
    source_sessions: list[str] = field(default_factory=list)
    outgoing_edges: list[KnowledgeEdge] = field(default_factory=list)
    obsidian_path: str = ""
    id: str = field(default_factory=_new_id)
