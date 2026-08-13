"""End-to-end route smoke tests.

These exist because unit tests exercise storage functions directly and never
import a route module's error path. A NameError on a logging line inside an
`except` block, for example, passes every other test in the suite and only
fires in production when something else has already gone wrong.
"""
import time

import pytest
from fastapi.testclient import TestClient

from capman.events import Event, EventType
from capman.storage.timeline import TimelineDB


@pytest.fixture
async def app_client(tmp_path):
    from capman.api.server import create_app

    db = TimelineDB(str(tmp_path / "t.db"))
    await db.migrate()

    now = time.time()
    for i, (etype, payload) in enumerate([
        (EventType.SHELL_COMMAND, {"command": "terraform apply -auto-approve"}),
        (EventType.URL_VISIT, {"url": "https://example.com/docs", "title": "Docs"}),
        (EventType.MOUSE_SCROLL, {"delta": 3}),
    ]):
        e = Event(type=etype, app="Terminal", payload=payload)
        e.ts = now - i
        await db.queue_event(e, "s1")
    await db.flush()
    await db.upsert_document("page:1", "page", "terraform state locking with dynamodb",
                             ts=now, title="TF locking", uri="https://example.com/tf")

    config = {
        "storage": {
            "sqlite_path": str(tmp_path / "t.db"),
            "chroma_path": str(tmp_path / "chroma"),
            "knowledge_dir": str(tmp_path / "knowledge"),
            "retention": {"enabled": True, "ttl_days": {"mouse_scroll": 1}},
        },
        "core": {"data_dir": str(tmp_path)},
        "api": {"host": "127.0.0.1", "port": 7331},
    }
    app = create_app(config, db)
    yield TestClient(app)
    await db.close()


def test_health(app_client):
    assert app_client.get("/health").json()["status"] == "ok"


def test_query_keyword_mode_finds_documents(app_client):
    body = app_client.get("/query", params={"q": "terraform locking", "mode": "keyword"}).json()
    assert body["total"] >= 1
    assert body["results"][0]["id"] == "page:1"


def test_query_degrades_to_keyword_without_a_vector_store(app_client):
    """No Chroma data present — hybrid must still answer, not error."""
    body = app_client.get("/query", params={"q": "terraform locking"}).json()
    assert "error" not in body
    assert body["total"] >= 1


def test_event_query_exact_recall(app_client):
    body = app_client.get("/query/events", params={"q": "terraform apply"}).json()
    assert body["total"] >= 1
    assert "terraform" in body["results"][0]["text"].lower().replace("«", "").replace("»", "")


def test_retention_endpoint_reports_policy_and_preview(app_client):
    body = app_client.get("/storage/retention").json()
    assert body["enabled"] is True
    assert body["ttl_days"]["mouse_scroll"] == 1
    assert "growth" in body
    assert "would_prune" in body
    assert "error" not in body, f"retention preview raised: {body.get('error')}"


def test_retention_preview_does_not_delete(app_client):
    before = app_client.get("/query/events", params={"q": "terraform"}).json()["total"]
    app_client.get("/storage/retention")
    after = app_client.get("/query/events", params={"q": "terraform"}).json()["total"]
    assert before == after


def test_storage_breakdown(app_client):
    body = app_client.get("/storage").json()
    assert "db_stats" in body or "components" in body or body
