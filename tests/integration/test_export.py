"""Export policy: what may leave the machine, and what may never.

capman2 captures keystrokes, clipboard contents, screenshots and full page
text. The export surface is the one place any of that could escape, so these
tests are written as a containment check rather than a feature check.
"""
import time

import pytest
from fastapi.testclient import TestClient

from capman.api.routes.export import (
    EXPORTABLE_KINDS, NEVER_EXPORTABLE, redact, resolve_policy,
)
from capman.storage.timeline import TimelineDB


@pytest.fixture
async def client(tmp_path):
    from capman.api.server import create_app

    db = TimelineDB(str(tmp_path / "t.db"))
    await db.migrate()
    now = time.time()
    await db.upsert_document("playbook:p1", "playbook", "Restart the pod and check the ingress",
                             ts=now, title="Fix 502s")
    await db.upsert_document("node:n1", "node", "Nginx buffers large upstream responses",
                             ts=now, title="Nginx buffering")
    await db.upsert_document("session:s1", "session", "Debugged a 502 in staging",
                             ts=now, title="502 debugging")
    # Raw capture that must never appear in any export
    await db.upsert_document("page:pg1", "page", "SECRET_PAGE_BODY private banking details",
                             ts=now, title="My bank")
    await db.upsert_document("ocr:o1", "ocr", "SECRET_OCR_TEXT from a screenshot",
                             ts=now, title="screenshot")

    app = create_app({"storage": {"sqlite_path": str(tmp_path / "t.db")}}, db)
    yield TestClient(app)
    await db.close()


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------

def test_exportable_and_never_exportable_do_not_overlap():
    assert not (set(EXPORTABLE_KINDS) & set(NEVER_EXPORTABLE))


def test_raw_capture_cannot_be_permitted_by_config():
    """Even an explicit config entry must not open up raw capture."""
    policy = resolve_policy({"storage": {"sharing": {"allow_kinds": ["page", "ocr", "playbook"]}}})
    assert policy == ["playbook"]


def test_unknown_kinds_are_ignored():
    assert resolve_policy({"storage": {"sharing": {"allow_kinds": ["nonsense"]}}}) == []


def test_empty_allowlist_exports_nothing():
    assert resolve_policy({"storage": {"sharing": {"allow_kinds": []}}}) == []


def test_default_policy_is_derived_knowledge_only():
    assert set(resolve_policy({})) == set(EXPORTABLE_KINDS)


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,gone", [
    ("mail me at alice@example.com", "alice@example.com"),
    ("saved to /Users/sherif/notes.md", "/Users/sherif"),
    ("saved to /home/sherif/notes.md", "/home/sherif"),
    ("connect to 192.168.51.80:7331", "192.168.51.80"),
    ("api_key=abcdef123456", "abcdef123456"),
    ("token: ghp_abcdefghijklmnop", "ghp_abcdefghijklmnop"),
])
def test_redaction_removes_identifying_and_secret_strings(raw, gone):
    assert gone not in redact(raw)


def test_redaction_keeps_the_useful_content():
    out = redact("Restart nginx after editing /home/bob/nginx.conf")
    assert "Restart nginx" in out
    assert "nginx.conf" in out


# --------------------------------------------------------------------------
# Endpoint containment
# --------------------------------------------------------------------------

def test_export_defaults_to_a_dry_run(client):
    body = client.get("/export").json()
    assert body["dry_run"] is True
    assert "items" not in body, "a dry run must not return the payload"
    assert body["count"] == 3


def test_export_never_returns_raw_capture(client):
    body = client.get("/export", params={"dry_run": False}).json()
    blob = str(body)
    assert "SECRET_PAGE_BODY" not in blob
    assert "SECRET_OCR_TEXT" not in blob
    assert set(body["counts_by_kind"]) <= set(EXPORTABLE_KINDS)


def test_requesting_raw_capture_is_refused_not_silently_dropped(client):
    body = client.get("/export", params={"kinds": "page,ocr,playbook"}).json()
    assert body["exported_kinds"] == ["playbook"]
    assert set(body["refused_kinds"]) == {"page", "ocr"}


def test_jsonl_export_respects_the_same_policy(client):
    text = client.get("/export/jsonl", params={"kinds": "page,playbook"}).text
    assert "SECRET_PAGE_BODY" not in text
    assert "Fix 502s" in text
    assert len([ln for ln in text.splitlines() if ln.strip()]) == 1


def test_redaction_is_on_by_default_in_the_endpoint(client):
    assert client.get("/export").json()["redacted"] is True


def test_since_days_filters_by_age(client):
    old = client.get("/export", params={"since_days": 1}).json()["count"]
    assert old == 3  # all seeded rows are recent
