"""Tests for capman.web.friction — product-independent friction primitives."""
from __future__ import annotations

from capman.web.friction import (
    BACKTRACK_MIN_DROP,
    FRICTION_EVENTS,
    RETRY_MIN_COUNT,
    RETRY_WINDOW_MS,
    SLOW_FIRST_ACTION_MS,
    derive_friction,
)
from capman.web.manifest import ManifestError, load_manifest, validate_event
import textwrap

STEPS = ["land", "start", "edit", "score", "done"]
EVENT_STEPS = {
    "page_view": "land",
    "widget_start": "start",
    "widget_edit": "edit",
    "widget_score": "score",
    "export_result": "done",
}


def ev(name: str, t_rel_ms: int, seq: int = 0, props: dict | None = None) -> dict:
    return {"name": name, "props": props or {}, "t_rel_ms": t_rel_ms, "seq": seq}


class TestReservedEvents:
    def test_all_primitives_declared(self):
        assert set(FRICTION_EVENTS) == {
            "rage_click", "dead_click", "retry", "backtrack",
            "zero_results", "error_shown", "field_abandon", "time_to_first_action",
        }

    def test_reserved_events_validate_without_manifest_declaration(self, tmp_path):
        p = tmp_path / "m.toml"
        p.write_text(textwrap.dedent("""
            [product]
            key = "acme"
        """))
        m = load_manifest(p)
        assert validate_event(m, "rage_click", {"element": "btn-save", "n": 4}) == {
            "element": "btn-save", "n": 4,
        }
        assert validate_event(m, "error_shown", {"code_class": "http_5xx"}) == {"code_class": "http_5xx"}

    def test_reserved_event_required_props_enforced(self, tmp_path):
        p = tmp_path / "m.toml"
        p.write_text("[product]\nkey = 'acme'\n")
        m = load_manifest(p)
        try:
            validate_event(m, "rage_click", {"element": "btn"})
            raise AssertionError("expected ManifestError for missing 'n'")
        except ManifestError as e:
            assert "missing required prop 'n'" in str(e)


class TestDeriveFriction:
    def test_empty_episode(self):
        assert derive_friction([], EVENT_STEPS, STEPS) == []

    def test_clean_episode_has_no_flags(self):
        events = [
            ev("page_view", 0, 0),
            ev("widget_start", 2_000, 1),
            ev("widget_edit", 5_000, 2),
            ev("export_result", 20_000, 3, {"ok": True}),
        ]
        assert derive_friction(events, EVENT_STEPS, STEPS) == []

    def test_client_rage_click_passthrough(self):
        events = [ev("page_view", 0), ev("rage_click", 1_000, 1, {"element": "btn", "n": 5})]
        assert derive_friction(events, EVENT_STEPS, STEPS) == ["rage_click"]

    def test_all_client_flags_pass_through(self):
        events = [
            ev("rage_click", 0, 0, {"element": "a", "n": 3}),
            ev("dead_click", 10, 1, {"element": "b"}),
            ev("zero_results", 20, 2, {"surface": "search"}),
            ev("error_shown", 30, 3, {"code_class": "http_5xx"}),
            ev("field_abandon", 40, 4, {"field": "email_field"}),
        ]
        assert derive_friction(events, EVENT_STEPS, STEPS) == [
            "dead_click", "error_shown", "field_abandon", "rage_click", "zero_results",
        ]

    def test_retry_three_same_events_within_window(self):
        events = [
            ev("page_view", 0, 0),
            ev("widget_edit", 1_000, 1),
            ev("widget_edit", 3_000, 2),
            ev("widget_edit", 9_000, 3),
        ]
        assert "retry" in derive_friction(events, EVENT_STEPS, STEPS)

    def test_retry_not_flagged_outside_window(self):
        events = [
            ev("widget_edit", 0, 0),
            ev("widget_edit", RETRY_WINDOW_MS + 1, 1),
            ev("widget_edit", 2 * RETRY_WINDOW_MS + 2, 2),
        ]
        assert "retry" not in derive_friction(events, EVENT_STEPS, STEPS)

    def test_retry_ignores_reserved_events(self):
        events = [
            ev("rage_click", 0, 0, {"element": "a", "n": 3}),
            ev("rage_click", 100, 1, {"element": "a", "n": 3}),
            ev("rage_click", 200, 2, {"element": "a", "n": 3}),
        ]
        flags = derive_friction(events, EVENT_STEPS, STEPS)
        assert "retry" not in flags
        assert "rage_click" in flags

    def test_backtrack_two_step_drop(self):
        events = [
            ev("page_view", 0, 0),
            ev("widget_edit", 1_000, 1),
            ev("widget_score", 2_000, 2),
            ev("page_view", 3_000, 3),  # score(3) -> land(0): drop of 3 >= BACKTRACK_MIN_DROP
        ]
        assert "backtrack" in derive_friction(events, EVENT_STEPS, STEPS)

    def test_single_step_back_is_not_backtrack(self):
        events = [
            ev("widget_edit", 0, 0),
            ev("widget_score", 1_000, 1),
            ev("widget_edit", 2_000, 2),  # drop of 1 < BACKTRACK_MIN_DROP
        ]
        assert "backtrack" not in derive_friction(events, EVENT_STEPS, STEPS)

    def test_slow_first_action(self):
        events = [ev("page_view", SLOW_FIRST_ACTION_MS + 1, 0)]
        assert derive_friction(events, EVENT_STEPS, STEPS) == ["slow_first_action"]

    def test_events_sorted_by_t_rel_then_seq(self):
        events = [
            ev("widget_score", 2_000, 1),
            ev("page_view", 0, 0),
            ev("widget_edit", 1_000, 0),
        ]
        assert derive_friction(events, EVENT_STEPS, STEPS) == []

    def test_unknown_events_are_skipped_by_step_tracking(self):
        events = [
            ev("widget_score", 0, 0),
            ev("mystery_event", 1_000, 1),
            ev("page_view", 2_000, 2),
        ]
        assert "backtrack" in derive_friction(events, EVENT_STEPS, STEPS)

    def test_deterministic_sorted_output(self):
        events = [
            ev("rage_click", SLOW_FIRST_ACTION_MS + 5_000, 0, {"element": "a", "n": 3}),
            ev("page_view", SLOW_FIRST_ACTION_MS + 6_000, 1),
        ]
        flags = derive_friction(events, EVENT_STEPS, STEPS)
        assert flags == sorted(flags) == ["rage_click", "slow_first_action"]

    def test_constants_are_sane(self):
        assert RETRY_MIN_COUNT == 3
        assert RETRY_WINDOW_MS == 10_000
        assert BACKTRACK_MIN_DROP == 2
        assert SLOW_FIRST_ACTION_MS == 30_000
