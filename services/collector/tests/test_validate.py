"""Collector batch-validation tests — the privacy boundary's enforcement layer."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from capman.web.manifest import load_manifest
from validate import MAX_BATCH_EVENTS, BatchError, validate_batch

MANIFEST_TOML = """
[product]
key = "acme"
description = "A generic widget product."

[steps]
order = ["land", "edit", "done"]

[[events]]
name = "page_view"
step = "land"
props = { path = { type = "short_string", pattern = "^/[a-z0-9_/-]*$" } }

[[events]]
name = "widget_click"
step = "edit"
props = { target = { type = "enum", values = ["save", "cancel"] }, n = { type = "int", max = 20 } }

[[events]]
name = "export_result"
step = "done"
props = { ok = { type = "bool", optional = false } }
"""

SID = "3f2b8c1e-9d4a-4e2f-8c1e-9d4a4e2f8c1e"


@pytest.fixture()
def manifest(tmp_path: Path):
    p = tmp_path / "acme.toml"
    p.write_text(textwrap.dedent(MANIFEST_TOML))
    return load_manifest(p)


def batch(*events, sid=SID):
    return {"sid": sid, "events": list(events)}


def ev(name, props=None, seq=0, t_rel_ms=0):
    return {"name": name, "props": props or {}, "seq": seq, "t_rel_ms": t_rel_ms}


class TestHappyPath:
    def test_valid_batch_returns_normalized_events(self, manifest):
        out = validate_batch(manifest, batch(
            ev("page_view", {"path": "/home"}, seq=0, t_rel_ms=0),
            ev("widget_click", {"target": "save", "n": 2}, seq=1, t_rel_ms=1500),
        ), recv_ts=1000.0)
        assert [e.name for e in out] == ["page_view", "widget_click"]
        assert out[0].props == {"path": "/home"}
        assert out[1].props == {"target": "save", "n": 2}
        assert all(e.sid == SID for e in out)

    def test_timestamps_reconstructed_relative_to_recv(self, manifest):
        out = validate_batch(manifest, batch(
            ev("page_view", seq=0, t_rel_ms=0),
            ev("widget_click", {"target": "save"}, seq=1, t_rel_ms=2000),
        ), recv_ts=1000.0)
        assert out[0].ts == pytest.approx(998.0)
        assert out[1].ts == pytest.approx(1000.0)

    def test_reserved_friction_event_accepted(self, manifest):
        out = validate_batch(manifest, batch(
            ev("rage_click", {"element": "btn-save", "n": 5}),
        ), recv_ts=1000.0)
        assert out[0].name == "rage_click"


class TestRejections:
    def test_unknown_event_name(self, manifest):
        with pytest.raises(BatchError) as e:
            validate_batch(manifest, batch(ev("checkout_done")))
        assert e.value.code == "schema_violation"

    def test_unknown_prop_key(self, manifest):
        with pytest.raises(BatchError) as e:
            validate_batch(manifest, batch(ev("page_view", {"note": "hi"})))
        assert e.value.code == "schema_violation"

    def test_oversized_string(self, manifest):
        with pytest.raises(BatchError) as e:
            validate_batch(manifest, batch(ev("page_view", {"path": "/" + "a" * 80})))
        assert e.value.code == "schema_violation"

    def test_email_shaped_string_rejected_and_nothing_returned(self, manifest):
        with pytest.raises(BatchError):
            validate_batch(manifest, batch(
                ev("page_view", {"path": "/ok"}),
                ev("page_view", {"path": "user@example.com"}),
            ))

    def test_phone_shaped_string_rejected(self, manifest):
        with pytest.raises(BatchError):
            validate_batch(manifest, batch(ev("page_view", {"path": "+1-555-0100"})))

    def test_oversized_batch(self, manifest):
        events = [ev("page_view", seq=i, t_rel_ms=i) for i in range(MAX_BATCH_EVENTS + 1)]
        with pytest.raises(BatchError) as e:
            validate_batch(manifest, batch(*events))
        assert e.value.code == "batch_too_large"

    def test_empty_batch(self, manifest):
        with pytest.raises(BatchError) as e:
            validate_batch(manifest, {"sid": SID, "events": []})
        assert e.value.code == "invalid_body"

    def test_missing_sid(self, manifest):
        with pytest.raises(BatchError) as e:
            validate_batch(manifest, {"events": [ev("page_view")]})
        assert e.value.code == "invalid_sid"

    def test_sid_with_non_hex_chars_rejected(self, manifest):
        with pytest.raises(BatchError) as e:
            validate_batch(manifest, batch(ev("page_view"), sid="hello world; DROP TABLE"))
        assert e.value.code == "invalid_sid"

    def test_body_not_object(self, manifest):
        with pytest.raises(BatchError) as e:
            validate_batch(manifest, ["not", "an", "object"])
        assert e.value.code == "invalid_body"

    def test_negative_seq_rejected(self, manifest):
        with pytest.raises(BatchError) as e:
            validate_batch(manifest, batch(ev("page_view", seq=-1)))
        assert e.value.code == "invalid_event"

    def test_bool_as_int_rejected(self, manifest):
        with pytest.raises(BatchError):
            validate_batch(manifest, batch(ev("widget_click", {"target": "save", "n": True})))

    def test_enum_violation(self, manifest):
        with pytest.raises(BatchError) as e:
            validate_batch(manifest, batch(ev("widget_click", {"target": "destroy"})))
        assert e.value.code == "schema_violation"

    def test_all_or_nothing_one_bad_event_rejects_batch(self, manifest):
        with pytest.raises(BatchError):
            validate_batch(manifest, batch(
                ev("page_view", {"path": "/a"}, seq=0),
                ev("page_view", {"path": "/b"}, seq=1),
                ev("nope", seq=2),
            ))
