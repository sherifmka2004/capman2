"""Tests for the portable, derived-only knowledge vault."""
from __future__ import annotations

import time

from capman.events import DiagnosticStep, SessionAnalysis, TroubleshootingPlaybook
from capman.knowledge.graph import KnowledgeGraph
from capman.knowledge.merger import GraphMerger
from capman.events import Triple
from capman.knowledge.vault import CuratedKnowledgeVault, export_derived_vault
from capman.storage.timeline import TimelineDB


def test_session_page_is_okf_shaped_redacted_and_evidence_linked(tmp_path):
    vault = CuratedKnowledgeVault(tmp_path / "vault")
    page = vault.write_session(SessionAnalysis(
        session_id="session-1", problem_statement="Fix API key leak for alice@example.com",
        approach_description="Removed token=ghp_abcdefghijklmnop from /home/alice/config",
        methodology_tags=["debugging"], analyzed_at=time.time(),
    ))
    assert page is not None
    text = page.read_text(encoding="utf-8")
    assert "type: session" in text
    assert "resource:" in text
    assert "capman://session/session-1" in text
    assert "alice@example.com" not in text
    assert "ghp_abcdefghijklmnop" not in text
    assert (tmp_path / "vault" / "index.md").exists()


def test_concept_pages_retain_source_sessions(tmp_path):
    graph = KnowledgeGraph(str(tmp_path / "graph"))
    GraphMerger().merge(graph, [Triple(subject="OAuth", predicate="requires", object="PKCE")], "session-2")
    vault = CuratedKnowledgeVault(tmp_path / "vault")
    assert vault.write_concepts(graph) == 2
    text = (tmp_path / "vault" / "concepts" / "oauth.md").read_text(encoding="utf-8")
    assert "capman://session/session-2" in text
    assert "type: concept" in text


async def test_rebuild_reads_only_derived_tables(tmp_path):
    db = TimelineDB(str(tmp_path / "timeline.db"))
    await db.migrate()
    now = time.time()
    await db._db.execute(
        "INSERT INTO sessions (id, started_at, event_count) VALUES ('s1', ?, 1)", (now,)
    )
    await db._db.commit()
    await db.save_analysis(SessionAnalysis(
        session_id="s1", problem_statement="Diagnose cache miss", approach_description="Checked headers",
        methodology_tags=["observe"], analyzed_at=now,
    ))
    await db.upsert_document("page:private", "page", "DO_NOT_EXPORT", ts=now, title="private")
    counts = await export_derived_vault(db, tmp_path / "vault", knowledge_dir=tmp_path / "graph")
    combined = "\n".join(p.read_text(encoding="utf-8") for p in (tmp_path / "vault").rglob("*.md"))
    assert counts["sessions"] == 1
    assert "Diagnose cache miss" in combined
    assert "DO_NOT_EXPORT" not in combined
    await db.close()
