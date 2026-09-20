"""Collector HTTP API tests — auth, tenancy, rate limits, all-or-nothing writes."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from capman.web.manifest import load_manifests
from main import SidRateLimiter, create_app, parse_keys

ACME_TOML = """
[product]
key = "acme"
[[events]]
name = "page_view"
props = { path = { type = "short_string", pattern = "^/[a-z0-9_/-]*$" } }
[[events]]
name = "export_result"
props = { ok = { type = "bool", optional = false } }
"""

GLOBEX_TOML = """
[product]
key = "globex"
[[events]]
name = "search_run"
props = { surface = { type = "enum", values = ["home", "results"] } }
"""

SID = "3f2b8c1e-9d4a-4e2f-8c1e-9d4a4e2f8c1e"


class FakeDB:
    def __init__(self):
        self.rows: list[tuple[str, str, list]] = []
        self.fail = False

    async def insert_events(self, tenant, product_key, events):
        if self.fail:
            raise RuntimeError("boom")
        self.rows.append((tenant, product_key, events))
        return len(events)


@pytest.fixture()
def env(tmp_path: Path):
    (tmp_path / "acme.toml").write_text(textwrap.dedent(ACME_TOML))
    (tmp_path / "globex.toml").write_text(textwrap.dedent(GLOBEX_TOML))
    manifests = load_manifests([tmp_path / "acme.toml", tmp_path / "globex.toml"])
    keys = parse_keys("acme=secret-acme,globex=secret-globex")
    db = FakeDB()
    app = create_app(manifests, keys, db)
    with TestClient(app) as client:
        yield client, db, keys


def body(*events, sid=SID):
    return {"sid": sid, "events": list(events)}


def ev(name, props=None, seq=0, t_rel_ms=0):
    return {"name": name, "props": props or {}, "seq": seq, "t_rel_ms": t_rel_ms}


class TestAuth:
    def test_missing_key_401(self, env):
        client, db, _ = env
        r = client.post("/collect", json=body(ev("page_view")))
        assert r.status_code == 401
        assert db.rows == []

    def test_wrong_key_401(self, env):
        client, db, _ = env
        r = client.post("/collect", json=body(ev("page_view")), headers={"X-CW-Key": "nope"})
        assert r.status_code == 401
        assert db.rows == []

    def test_valid_key_accepted(self, env):
        client, db, _ = env
        r = client.post("/collect", json=body(ev("page_view", {"path": "/home"})),
                        headers={"X-CW-Key": "secret-acme"})
        assert r.status_code == 200
        assert r.json() == {"accepted": 1}
        assert len(db.rows) == 1


class TestTenancy:
    def test_events_route_to_distinct_tenants(self, env):
        client, db, _ = env
        client.post("/collect", json=body(ev("page_view", {"path": "/a"})),
                    headers={"X-CW-Key": "secret-acme"})
        client.post("/collect", json=body(ev("search_run", {"surface": "home"})),
                    headers={"X-CW-Key": "secret-globex"})
        assert [(t, p) for t, p, _ in db.rows] == [
            ("product:acme", "acme"),
            ("product:globex", "globex"),
        ]

    def test_event_from_other_products_manifest_rejected(self, env):
        client, db, _ = env
        r = client.post("/collect", json=body(ev("search_run", {"surface": "home"})),
                        headers={"X-CW-Key": "secret-acme"})
        assert r.status_code == 400
        assert r.json()["error"] == "schema_violation"
        assert db.rows == []


class TestValidation:
    def test_invalid_batch_writes_nothing(self, env):
        client, db, _ = env
        r = client.post("/collect", json=body(
            ev("page_view", {"path": "/ok"}, seq=0),
            ev("page_view", {"path": "user@example.com"}, seq=1),
        ), headers={"X-CW-Key": "secret-acme"})
        assert r.status_code == 400
        assert db.rows == []

    def test_invalid_json_400(self, env):
        client, db, _ = env
        r = client.post("/collect", content=b"{not json",
                        headers={"X-CW-Key": "secret-acme", "Content-Type": "application/json"})
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_json"

    def test_oversized_body_413(self, env):
        client, db, _ = env
        events = [ev("page_view", {"path": f"/p{i}"}, seq=i, t_rel_ms=i) for i in range(50)]
        big = body(*events)
        big["padding"] = "x" * (64 * 1024)
        r = client.post("/collect", json=big, headers={"X-CW-Key": "secret-acme"})
        assert r.status_code == 413
        assert db.rows == []

    def test_storage_failure_500(self, env):
        client, db, _ = env
        db.fail = True
        r = client.post("/collect", json=body(ev("page_view")),
                        headers={"X-CW-Key": "secret-acme"})
        assert r.status_code == 500


class TestRateLimit:
    def test_limiter_blocks_after_max_events(self):
        limiter = SidRateLimiter(window_s=60, max_requests=100, max_events=10)
        now = 1000.0
        assert limiter.allow("sid-a", 6, now=now) is True
        assert limiter.allow("sid-a", 6, now=now + 1) is False
        assert limiter.allow("sid-b", 6, now=now + 1) is True

    def test_limiter_window_slides(self):
        limiter = SidRateLimiter(window_s=60, max_requests=100, max_events=10)
        assert limiter.allow("sid-a", 10, now=1000.0) is True
        assert limiter.allow("sid-a", 1, now=1059.0) is False
        assert limiter.allow("sid-a", 10, now=1061.0) is True

    def test_limiter_blocks_after_max_requests(self):
        limiter = SidRateLimiter(window_s=60, max_requests=2, max_events=1000)
        assert limiter.allow("sid-a", 1, now=1000.0) is True
        assert limiter.allow("sid-a", 1, now=1001.0) is True
        assert limiter.allow("sid-a", 1, now=1002.0) is False

    def test_http_429_after_limit(self, tmp_path):
        (tmp_path / "acme.toml").write_text(textwrap.dedent(ACME_TOML))
        manifests = load_manifests([tmp_path / "acme.toml"])
        db = FakeDB()
        app = create_app(manifests, parse_keys("acme=secret-acme"), db,
                         limiter=SidRateLimiter(window_s=60, max_requests=100, max_events=2))
        with TestClient(app) as client:
            r1 = client.post("/collect", json=body(ev("page_view", seq=0), ev("page_view", seq=1)),
                             headers={"X-CW-Key": "secret-acme"})
            assert r1.status_code == 200
            r2 = client.post("/collect", json=body(ev("page_view", seq=2), sid=SID),
                             headers={"X-CW-Key": "secret-acme"})
            assert r2.status_code == 429
            assert r2.json()["error"] == "rate_limited"
            assert len(db.rows) == 1


class TestHealth:
    def test_health_reports_product_count_only(self, env):
        client, _, _ = env
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "products": 2}

    def test_no_openapi_exposed(self, env):
        client, _, _ = env
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/docs").status_code == 404

    def test_read_and_control_routes_absent(self, env):
        client, _, _ = env
        for path in ("/query", "/export", "/sessions", "/settings/stop", "/events"):
            assert client.get(path).status_code == 404
            assert client.post(path, json={}).status_code == 404


class TestParseKeys:
    def test_parses_pairs(self):
        assert parse_keys("a=1, b=2 ") == {"a": "1", "b": "2"}

    def test_empty_entries_skipped(self):
        assert parse_keys("a=1,,") == {"a": "1"}

    def test_malformed_raises(self):
        with pytest.raises(ValueError):
            parse_keys("no-equals-sign")
        with pytest.raises(ValueError):
            parse_keys("=secret")
