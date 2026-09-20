"""Tests for capman.web.manifest — the privacy boundary of the web pipeline."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from capman.web.manifest import (
    MAX_SHORT_STRING,
    ManifestError,
    bucket_label,
    eval_success_when,
    load_manifest,
    load_manifests,
    tenant_id,
    validate_event,
)

VALID_MANIFEST = """
[product]
key         = "acme"
description = "A generic widget product."
glossary    = { widget = "the main interactive surface" }

[retention]
raw_events_days = 45
episodes_days   = 180

[session]
idle_threshold_s = 30
cool_period_s    = 45
hard_break_s     = 1800
min_events       = 3

[steps]
order = ["land", "start", "edit", "done"]

[outcomes]
success_when = ["export_result.ok == true"]

[[events]]
name = "page_view"
step = "land"

[[events]]
name = "widget_click"
step = "edit"
props = { target = { type = "enum", values = ["save", "cancel"] }, n = { type = "int", max = 20 } }

[[events]]
name = "export_result"
step = "done"
props = { ok = { type = "bool", optional = false }, size = { type = "bucket", buckets = [1.0, 5.0, 10.0] } }

[[events]]
name = "route_change"
props = { path = { type = "short_string", pattern = "^/[a-z0-9_/-]*$" } }
"""


@pytest.fixture()
def manifest_path(tmp_path: Path) -> Path:
    p = tmp_path / "acme.toml"
    p.write_text(textwrap.dedent(VALID_MANIFEST))
    return p


@pytest.fixture()
def manifest(manifest_path):
    return load_manifest(manifest_path)


def _write(tmp_path: Path, body: str, name: str = "m.toml") -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body))
    return p


class TestLoadManifest:
    def test_valid_manifest_loads_with_defaults_and_values(self, manifest):
        assert manifest.key == "acme"
        assert manifest.description == "A generic widget product."
        assert manifest.glossary == {"widget": "the main interactive surface"}
        assert manifest.raw_events_days == 45
        assert manifest.episodes_days == 180
        assert manifest.idle_threshold_s == 30
        assert manifest.cool_period_s == 45
        assert manifest.hard_break_s == 1800
        assert manifest.min_events == 3
        assert manifest.steps == ["land", "start", "edit", "done"]
        assert manifest.success_when == ["export_result.ok == true"]
        assert set(manifest.events) == {"page_view", "widget_click", "export_result", "route_change"}
        assert manifest.events["widget_click"].step == "edit"

    def test_defaults_when_sections_missing(self, tmp_path):
        p = _write(tmp_path, """
            [product]
            key = "minimal"
        """)
        m = load_manifest(p)
        assert m.raw_events_days == 45
        assert m.episodes_days == 180
        assert m.idle_threshold_s == 30
        assert m.steps == []
        assert m.events == {}

    def test_tenant_id(self, manifest):
        assert tenant_id(manifest) == "product:acme"

    def test_unknown_top_level_section_rejected(self, tmp_path):
        p = _write(tmp_path, """
            [product]
            key = "acme"
            [surprise]
            x = 1
        """)
        with pytest.raises(ManifestError, match="unknown section"):
            load_manifest(p)

    def test_unknown_key_in_section_rejected(self, tmp_path):
        p = _write(tmp_path, """
            [product]
            key = "acme"
            surprise = 1
        """)
        with pytest.raises(ManifestError, match="unknown key"):
            load_manifest(p)

    def test_invalid_key_charset_rejected(self, tmp_path):
        p = _write(tmp_path, """
            [product]
            key = "Acme-Corp"
        """)
        with pytest.raises(ManifestError):
            load_manifest(p)

    def test_missing_key_rejected(self, tmp_path):
        p = _write(tmp_path, "[product]\ndescription = 'x'\n")
        with pytest.raises(ManifestError, match="requires 'key'"):
            load_manifest(p)

    def test_missing_file(self, tmp_path):
        with pytest.raises(ManifestError, match="not found"):
            load_manifest(tmp_path / "nope.toml")

    def test_invalid_toml(self, tmp_path):
        p = _write(tmp_path, "this is not = = toml")
        with pytest.raises(ManifestError, match="invalid TOML"):
            load_manifest(p)

    def test_event_step_must_be_declared(self, tmp_path):
        p = _write(tmp_path, """
            [product]
            key = "acme"
            [steps]
            order = ["land"]
            [[events]]
            name = "x"
            step = "checkout"
        """)
        with pytest.raises(ManifestError, match="not in steps.order"):
            load_manifest(p)

    def test_duplicate_event_rejected(self, tmp_path):
        p = _write(tmp_path, """
            [product]
            key = "acme"
            [[events]]
            name = "x"
            [[events]]
            name = "x"
        """)
        with pytest.raises(ManifestError, match="duplicate event"):
            load_manifest(p)

    def test_success_when_must_reference_declared_event(self, tmp_path):
        p = _write(tmp_path, """
            [product]
            key = "acme"
            [outcomes]
            success_when = ["nope.ok == true"]
        """)
        with pytest.raises(ManifestError, match="undeclared event"):
            load_manifest(p)

    def test_success_when_must_reference_declared_prop(self, tmp_path):
        p = _write(tmp_path, """
            [product]
            key = "acme"
            [[events]]
            name = "done"
            [outcomes]
            success_when = ["done.missing == true"]
        """)
        with pytest.raises(ManifestError, match="undeclared prop"):
            load_manifest(p)

    def test_invalid_success_when_grammar(self, tmp_path):
        p = _write(tmp_path, """
            [product]
            key = "acme"
            [[events]]
            name = "done"
            props = { ok = { type = "bool" } }
            [outcomes]
            success_when = ["done ok == true"]
        """)
        with pytest.raises(ManifestError, match="invalid success_when"):
            load_manifest(p)


class TestPropTypeVocabulary:
    """The privacy boundary: the vocabulary is closed and has no free-text type."""

    @pytest.mark.parametrize("bad_type", ["text", "string", "free_text", "json", ""])
    def test_free_text_types_rejected(self, tmp_path, bad_type):
        p = _write(tmp_path, f"""
            [product]
            key = "acme"
            [[events]]
            name = "x"
            props = {{ note = {{ type = "{bad_type}" }} }}
        """)
        with pytest.raises(ManifestError, match="free-text props are forbidden|unknown prop type"):
            load_manifest(p)

    def test_enum_requires_values(self, tmp_path):
        p = _write(tmp_path, """
            [product]
            key = "acme"
            [[events]]
            name = "x"
            props = { e = { type = "enum" } }
        """)
        with pytest.raises(ManifestError, match="non-empty 'values'"):
            load_manifest(p)

    def test_bucket_requires_ascending_boundaries(self, tmp_path):
        p = _write(tmp_path, """
            [product]
            key = "acme"
            [[events]]
            name = "x"
            props = { b = { type = "bucket", buckets = [10.0, 5.0] } }
        """)
        with pytest.raises(ManifestError, match="ascending"):
            load_manifest(p)

    def test_invalid_pattern_rejected(self, tmp_path):
        p = _write(tmp_path, """
            [product]
            key = "acme"
            [[events]]
            name = "x"
            props = { s = { type = "short_string", pattern = "([a-z" } }
        """)
        with pytest.raises(ManifestError, match="invalid short_string pattern"):
            load_manifest(p)

    def test_unknown_prop_spec_key_rejected(self, tmp_path):
        p = _write(tmp_path, """
            [product]
            key = "acme"
            [[events]]
            name = "x"
            props = { s = { type = "short_string", min_length = 3 } }
        """)
        with pytest.raises(ManifestError):
            load_manifest(p)


class TestValidateEvent:
    def test_valid_event_returns_normalized_props(self, manifest):
        out = validate_event(manifest, "widget_click", {"target": "save", "n": 3})
        assert out == {"target": "save", "n": 3}

    def test_optional_props_may_be_absent(self, manifest):
        assert validate_event(manifest, "widget_click", {}) == {}

    def test_event_without_props(self, manifest):
        assert validate_event(manifest, "page_view", {}) == {}

    def test_unknown_event_rejected(self, manifest):
        with pytest.raises(ManifestError, match="unknown event"):
            validate_event(manifest, "checkout_done", {})

    def test_unknown_prop_key_rejected(self, manifest):
        with pytest.raises(ManifestError, match="unknown prop 'note'"):
            validate_event(manifest, "widget_click", {"note": "hello"})

    def test_enum_violation_rejected(self, manifest):
        with pytest.raises(ManifestError, match="must be one of"):
            validate_event(manifest, "widget_click", {"target": "destroy"})

    def test_int_over_max_rejected(self, manifest):
        with pytest.raises(ManifestError, match="exceeds declared max"):
            validate_event(manifest, "widget_click", {"n": 21})

    def test_int_type_enforced(self, manifest):
        with pytest.raises(ManifestError, match="must be an int"):
            validate_event(manifest, "widget_click", {"n": "3"})
        with pytest.raises(ManifestError, match="must be an int"):
            validate_event(manifest, "widget_click", {"n": True})

    def test_bool_type_enforced(self, manifest):
        with pytest.raises(ManifestError, match="must be a bool"):
            validate_event(manifest, "export_result", {"ok": "true"})

    def test_required_prop_enforced(self, manifest):
        with pytest.raises(ManifestError, match="missing required prop 'ok'"):
            validate_event(manifest, "export_result", {})

    def test_short_string_over_64_chars_rejected(self, manifest):
        with pytest.raises(ManifestError, match="exceeds 64 chars"):
            validate_event(manifest, "route_change", {"path": "/" + "a" * MAX_SHORT_STRING})

    def test_short_string_pattern_enforced(self, manifest):
        with pytest.raises(ManifestError, match="fails allowlist pattern"):
            validate_event(manifest, "route_change", {"path": "/he lló"})

    def test_short_string_default_pattern_allows_typical_paths(self, tmp_path):
        p = _write(tmp_path, """
            [product]
            key = "acme"
            [[events]]
            name = "x"
            props = { s = { type = "short_string" } }
        """)
        m = load_manifest(p)
        assert validate_event(m, "x", {"s": "btn-save.primary/v2"}) == {"s": "btn-save.primary/v2"}
        with pytest.raises(ManifestError, match="fails allowlist pattern"):
            validate_event(m, "x", {"s": "has spaces"})

    def test_pii_shaped_payload_cannot_enter(self, manifest):
        # An email fails the default short_string pattern; a phone fails too.
        with pytest.raises(ManifestError):
            validate_event(manifest, "route_change", {"path": "user@example.com"})
        with pytest.raises(ManifestError):
            validate_event(manifest, "route_change", {"path": "+1-555-0100"})

    def test_bucket_normalization(self, manifest):
        out = validate_event(manifest, "export_result", {"ok": True, "size": 7})
        assert out == {"ok": True, "size": "5-10"}

    def test_invalid_event_name_rejected(self, manifest):
        with pytest.raises(ManifestError, match="invalid event name"):
            validate_event(manifest, "Widget-Click!", {})

    def test_props_must_be_object(self, manifest):
        with pytest.raises(ManifestError, match="props must be an object"):
            validate_event(manifest, "page_view", "not-a-dict")  # type: ignore[arg-type]


class TestBucketLabel:
    @pytest.mark.parametrize(
        "value,expected",
        [(0.5, "<1"), (1, "1-5"), (4.9, "1-5"), (5, "5-10"), (9.99, "5-10"), (10, ">=10"), (42, ">=10")],
    )
    def test_boundaries(self, value, expected):
        assert bucket_label(value, [1.0, 5.0, 10.0]) == expected


class TestLoadManifests:
    def test_two_manifests_distinct_tenants(self, tmp_path):
        a = _write(tmp_path, "[product]\nkey = 'acme'\n", "a.toml")
        b = _write(tmp_path, "[product]\nkey = 'globex'\n", "b.toml")
        out = load_manifests([a, b])
        assert set(out) == {"acme", "globex"}
        assert tenant_id(out["acme"]) == "product:acme"
        assert tenant_id(out["globex"]) == "product:globex"

    def test_duplicate_key_rejected(self, tmp_path):
        a = _write(tmp_path, "[product]\nkey = 'acme'\n", "a.toml")
        b = _write(tmp_path, "[product]\nkey = 'acme'\n", "b.toml")
        with pytest.raises(ManifestError, match="duplicate product key"):
            load_manifests([a, b])


class TestEvalSuccessWhen:
    def test_match(self):
        events = [
            {"name": "page_view", "props": {}},
            {"name": "export_result", "props": {"ok": True}},
        ]
        assert eval_success_when("export_result.ok == true", events) is True

    def test_no_match_wrong_value(self):
        events = [{"name": "export_result", "props": {"ok": False}}]
        assert eval_success_when("export_result.ok == true", events) is False

    def test_no_match_wrong_event(self):
        events = [{"name": "page_view", "props": {}}]
        assert eval_success_when("export_result.ok == true", events) is False

    def test_string_literal(self):
        events = [{"name": "x", "props": {"mode": "import"}}]
        assert eval_success_when('x.mode == "import"', events) is True
