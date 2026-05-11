"""Tests for data models in capman/events.py"""
import time
from capman.events import (
    Event, EventType, Session, SessionAnalysis, ChainOfThought,
    CognitiveStep, DecisionPoint, Triple, KnowledgeNode, KnowledgeEdge,
)


def test_event_has_auto_id_and_ts():
    e = Event(type=EventType.KEYSTROKE, app="VSCode", window_title="main.py",
              payload={"text": "hello", "is_paste": False, "field_type": "text"})
    assert e.id and len(e.id) > 0
    assert e.ts > 0


def test_event_type_values():
    assert EventType.SEARCH_QUERY.value == "search_query"
    assert EventType.SHELL_COMMAND.value == "shell_command"
    assert EventType.SCREENSHOT.value == "screenshot"


def test_session_defaults():
    s = Session()
    assert s.id and len(s.id) > 0
    assert s.events == []
    assert s.search_queries == []
    assert s.analysis is None


def test_chain_of_thought_structure():
    cot = ChainOfThought(
        session_id="sess-1",
        problem_type="debugging",
        trigger="Error in console",
        steps=[
            CognitiveStep(sequence=1, action="searched", target="react hydration google",
                          reasoning="First thing to do with unknown error", duration_estimate_s=30)
        ],
        decision_points=[],
        outcome="Fixed",
        methodology_pattern="search → docs → apply",
        reusability_score=0.8,
        knowledge_gaps_revealed=["suppressHydrationWarning"],
        duration_seconds=780.0,
    )
    assert cot.reusability_score == 0.8
    assert len(cot.steps) == 1
    assert cot.steps[0].action == "searched"


def test_triple_defaults():
    t = Triple(subject="A", predicate="causes", object="B", confidence=0.9,
               source_session="sess-1")
    assert t.id and len(t.id) > 0
    assert t.observed_at > 0
