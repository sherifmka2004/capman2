"""Golden segmentation tests — fixture event logs in, expected episodes out."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from capman.web.manifest import load_manifest
from segment import furthest_step, label_outcome, segment_events, sid_hash
from signature import coarse_signature, signature

MANIFEST_TOML = """
[product]
key = "acme"
description = "A generic widget product."

[session]
idle_threshold_s = 30
cool_period_s    = 45
hard_break_s     = 1800
min_events       = 3

[steps]
order = ["land", "edit", "done"]

[outcomes]
success_when = ["export_result.ok == true"]

[[events]]
name = "page_view"
step = "land"

[[events]]
name = "widget_click"
step = "edit"

[[events]]
name = "score_view"
step = "done"

[[events]]
name = "export_result"
step = "done"
props = { ok = { type = "bool", optional = false } }
"""

T0 = 1_700_000_000.0


@pytest.fixture()
def manifest(tmp_path: Path):
    p = tmp_path / "acme.toml"
    p.write_text(textwrap.dedent(MANIFEST_TOML))
    return load_manifest(p)


def row(i: int, ts: float, name: str, sid: str, props: dict | None = None) -> dict:
    return {"id": f"ev-{sid[:2]}-{i}", "ts": ts, "name": name,
            "payload": {"sid": sid, "seq": i, "t_rel_ms": int((ts - T0) * 1000),
                        "props": props or {}}}


SID_A = "aaaa1111-2222-3333-4444-555566667777"
SID_B = "bbbb1111-2222-3333-4444-555566667777"


class TestSegmentation:
    def test_single_successful_visit(self, manifest):
        rows = [
            row(0, T0, "page_view", SID_A),
            row(1, T0 + 5, "widget_click", SID_A),
            row(2, T0 + 10, "export_result", SID_A, {"ok": True}),
        ]
        episodes = segment_events(rows, manifest, "acme")
        assert len(episodes) == 1
        ep = episodes[0]
        assert ep["outcome"] == "succeeded"
        assert ep["step_reached"] == "done"
        assert ep["n_events"] == 3
        assert ep["sid_hash"] == sid_hash(SID_A)
        assert ep["names"] == ["page_view", "widget_click", "export_result"]

    def test_two_interleaved_visitors_stay_separate(self, manifest):
        rows = [
            row(0, T0, "page_view", SID_A),
            row(1, T0 + 1, "page_view", SID_B),
            row(2, T0 + 2, "widget_click", SID_A),
            row(3, T0 + 3, "widget_click", SID_B),
            row(4, T0 + 4, "export_result", SID_A, {"ok": True}),
            row(5, T0 + 5, "score_view", SID_B),
        ]
        episodes = segment_events(rows, manifest, "acme")
        assert len(episodes) == 2
        by_sid = {ep["sid_hash"]: ep for ep in episodes}
        assert by_sid[sid_hash(SID_A)]["outcome"] == "succeeded"
        assert by_sid[sid_hash(SID_A)]["n_events"] == 3
        assert by_sid[sid_hash(SID_B)]["n_events"] == 3
        assert by_sid[sid_hash(SID_B)]["outcome"] == "bounced" or \
               by_sid[sid_hash(SID_B)]["outcome"].startswith("abandoned")

    def test_idle_gap_splits_episodes(self, manifest):
        rows = [
            row(0, T0, "page_view", SID_A),
            row(1, T0 + 5, "widget_click", SID_A),
            row(2, T0 + 10, "widget_click", SID_A),
            # gap of 120s > idle(30) + cool(45)
            row(3, T0 + 130, "page_view", SID_A),
            row(4, T0 + 135, "widget_click", SID_A),
            row(5, T0 + 140, "export_result", SID_A, {"ok": True}),
        ]
        episodes = segment_events(rows, manifest, "acme")
        assert len(episodes) == 2
        assert episodes[0]["n_events"] == 3
        assert episodes[1]["outcome"] == "succeeded"

    def test_short_episode_below_min_events_dropped(self, manifest):
        rows = [row(0, T0, "page_view", SID_A), row(1, T0 + 1, "widget_click", SID_A)]
        assert segment_events(rows, manifest, "acme") == []

    def test_abandoned_outcome_labels_step(self, manifest):
        rows = [
            row(0, T0, "page_view", SID_A),
            row(1, T0 + 5, "widget_click", SID_A),
            row(2, T0 + 10, "widget_click", SID_A),
        ]
        ep = segment_events(rows, manifest, "acme")[0]
        assert ep["outcome"] == "abandoned_at_edit"
        assert ep["step_reached"] == "edit"

    def test_errored_outcome_from_error_shown(self, manifest):
        rows = [
            row(0, T0, "page_view", SID_A),
            row(1, T0 + 5, "widget_click", SID_A),
            row(2, T0 + 10, "error_shown", SID_A, {"code_class": "http_5xx"}),
        ]
        ep = segment_events(rows, manifest, "acme")[0]
        assert ep["outcome"] == "errored"
        assert "error_shown" in ep["friction_flags"]

    def test_bounced_outcome_first_step_only(self, manifest):
        rows = [
            row(0, T0, "page_view", SID_A),
            row(1, T0 + 5, "page_view", SID_A),
            row(2, T0 + 10, "page_view", SID_A),
        ]
        ep = segment_events(rows, manifest, "acme")[0]
        assert ep["outcome"] == "bounced"

    def test_rows_without_sid_skipped(self, manifest):
        rows = [{"id": "x", "ts": T0, "name": "page_view", "payload": {}}]
        assert segment_events(rows, manifest, "acme") == []

    def test_signatures_are_stable_and_distinct(self, manifest):
        rows1 = [row(0, T0, "page_view", SID_A), row(1, T0 + 5, "widget_click", SID_A),
                 row(2, T0 + 10, "export_result", SID_A, {"ok": True})]
        rows2 = [row(0, T0, "page_view", SID_B), row(1, T0 + 5, "page_view", SID_B),
                 row(2, T0 + 10, "export_result", SID_B, {"ok": True})]
        e1 = segment_events(rows1, manifest, "acme")[0]
        e2 = segment_events(rows2, manifest, "acme")[0]
        e1["signature"] = signature(e1["names"])
        e2["signature"] = signature(e2["names"])
        assert e1["signature"] == signature(["page_view", "widget_click", "export_result"])
        assert e1["signature"] != e2["signature"]


class TestOutcomeHelpers:
    def test_furthest_step(self, manifest):
        assert furthest_step(manifest, ["page_view", "widget_click"]) == "edit"
        assert furthest_step(manifest, ["widget_click", "page_view"]) == "edit"
        assert furthest_step(manifest, ["export_result"]) == "done"
        assert furthest_step(manifest, ["unknown_event"]) == ""

    def test_label_outcome_priority(self, manifest):
        events = [{"name": "export_result", "props": {"ok": True}}]
        assert label_outcome(manifest, events, "done", ["error_shown"]) == "succeeded"
        assert label_outcome(manifest, [], "edit", ["error_shown"]) == "errored"
        assert label_outcome(manifest, [], "", []) == "bounced"
        assert label_outcome(manifest, [], "land", []) == "bounced"
        assert label_outcome(manifest, [], "edit", []) == "abandoned_at_edit"


class TestSignature:
    def test_rle(self):
        from signature import run_length_encode
        assert run_length_encode([]) == ""
        assert run_length_encode(["a"]) == "ax1"
        assert run_length_encode(["a", "a", "b"]) == "ax2>bx1"
        assert run_length_encode(["a", "b", "a"]) == "ax1>bx1>ax1"

    def test_signature_deterministic_16_hex(self):
        s = signature(["a", "b", "c"])
        assert len(s) == 16
        assert all(c in "0123456789abcdef" for c in s)
        assert s == signature(["a", "b", "c"])

    def test_coarse_uses_first3_last3(self):
        names = ["a", "b", "c", "d", "e", "f", "g", "h"]
        assert coarse_signature(names) == coarse_signature(["a", "b", "c", "x", "y", "f", "g", "h"])
        assert coarse_signature(names) != coarse_signature(["a", "b", "x", "d", "e", "f", "g", "h"])

    def test_coarse_short_sequence_equals_exact(self):
        names = ["a", "b", "c"]
        assert coarse_signature(names) == signature(names)
