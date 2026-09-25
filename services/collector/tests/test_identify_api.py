"""POST /identify — auth, validation, tenancy. Mirrors test_collect_api.py's
shape; /identify is a different trust boundary (see main.py's docstring), so
it gets its own FakeDB stub and its own test file rather than sharing one."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from capman.web.manifest import load_manifests
from main import create_app, parse_keys

ACME_TOML = """
[product]
key = "acme"
[[events]]
name = "page_view"
props = { path = { type = "short_string", pattern = "^/[a-z0-9_/-]*$" } }
"""

SID = "3f2b8c1e-9d4a-4e2f-8c1e-9d4a4e2f8c1e"


class FakeDB:
    def __init__(self):
        self.identities: list[tuple[str, str, str, float]] = []

    async def insert_events(self, tenant, product_key, events):
        return len(events)

    async def upsert_identity(self, tenant, sid_hash, email, ts):
        self.identities.append((tenant, sid_hash, email, ts))


@pytest.fixture()
def env(tmp_path: Path):
    (tmp_path / "acme.toml").write_text(textwrap.dedent(ACME_TOML))
    manifests = load_manifests([tmp_path / "acme.toml"])
    keys = parse_keys("acme=secret-acme")
    db = FakeDB()
    app = create_app(manifests, keys, db)
    with TestClient(app) as client:
        yield client, db


def test_identify_happy_path(env):
    client, db = env
    r = client.post(
        "/identify",
        headers={"x-cw-key": "secret-acme"},
        content=json.dumps({"sid": SID, "email": "Jane@Example.com"}),
    )
    assert r.status_code == 204
    assert len(db.identities) == 1
    tenant, sid_hash, email, _ts = db.identities[0]
    assert tenant == "product:acme"
    assert email == "jane@example.com"  # lowercased, trimmed
    assert len(sid_hash) == 16  # sha256(sid)[:16], never the raw sid


def test_identify_rejects_missing_key(env):
    client, _db = env
    r = client.post("/identify", content=json.dumps({"sid": SID, "email": "a@b.com"}))
    assert r.status_code == 401


def test_identify_rejects_wrong_key(env):
    client, _db = env
    r = client.post(
        "/identify",
        headers={"x-cw-key": "wrong"},
        content=json.dumps({"sid": SID, "email": "a@b.com"}),
    )
    assert r.status_code == 401


def test_identify_rejects_bad_sid(env):
    client, _db = env
    r = client.post(
        "/identify",
        headers={"x-cw-key": "secret-acme"},
        content=json.dumps({"sid": "short", "email": "a@b.com"}),
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_sid"


@pytest.mark.parametrize("bad_email", ["", "not-an-email", "a@b", "a" * 300 + "@b.com", 42, None])
def test_identify_rejects_bad_email(env, bad_email):
    client, _db = env
    r = client.post(
        "/identify",
        headers={"x-cw-key": "secret-acme"},
        content=json.dumps({"sid": SID, "email": bad_email}),
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_email"


def test_identify_unknown_product_404s_like_unauthorized(env):
    client, _db = env
    r = client.post(
        "/identify",
        headers={"x-cw-key": "secret-globex"},
        content=json.dumps({"sid": SID, "email": "a@b.com"}),
    )
    assert r.status_code == 401


def test_identify_rejects_oversized_body(env):
    client, _db = env
    r = client.post(
        "/identify",
        headers={"x-cw-key": "secret-acme"},
        content=json.dumps({"sid": SID, "email": "a@b.com", "pad": "x" * 3000}),
    )
    assert r.status_code == 413
