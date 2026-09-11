"""Unit tests for the troubleshooting playbook + knowledge gap features."""
import time
import pytest
from pathlib import Path

from capman.events import (
    TroubleshootingPlaybook, DiagnosticStep, KnowledgeGap,
    SessionAnalysis, ChainOfThought,
)
from capman.knowledge.playbooks import save_playbook_markdown, _slug
from capman.knowledge.gaps import (
    _normalize, _infer_domain,
    update_gaps_from_analysis, update_gaps_from_search_queries,
)
from capman.storage.timeline import TimelineDB


# ---------------------------------------------------------------------------
# Playbook serializer
# ---------------------------------------------------------------------------

def _sample_playbook() -> TroubleshootingPlaybook:
    return TroubleshootingPlaybook(
        session_id="sess-test",
        title="L3VPN traffic interruption diagnosis on Huawei NCE",
        domain="networking",
        symptoms=["L3VPN traffic stops flowing", "Customer reports outage on PE-X"],
        context_signals=["Huawei NCE-IP controller", "BGP/MPLS L3VPN deployment"],
        diagnostic_steps=[
            DiagnosticStep(sequence=1, action="Check BGP session state on PE",
                           rationale="Rules out control-plane failure",
                           expected_signal="BGP must be Established",
                           tool="display bgp peer"),
            DiagnosticStep(sequence=2, action="Verify MPLS LSP integrity",
                           rationale="LSP collapse → blackholes traffic",
                           expected_signal="LSP must show 'Up'",
                           tool="display mpls lsp"),
        ],
        root_cause="MPLS LSP collapse due to interface flap",
        fix=["Re-enable interface", "Trigger LSP re-signaling"],
        verification=["Run 'display mpls lsp' to confirm LSP up", "Ping CE from CE"],
        references=["https://support.huawei.com/.../l3vpn-troubleshooting"],
        reusability_score=0.92,
    )


def test_playbook_markdown_created(tmp_path):
    pb = _sample_playbook()
    path = save_playbook_markdown(pb, tmp_path)
    assert path.exists()
    assert "playbooks" in str(path)
    assert "networking" in str(path)
    content = path.read_text()
    assert "L3VPN traffic interruption" in content
    assert "BGP session state" in content
    assert "MPLS LSP" in content
    assert "## Diagnostic Steps" in content
    assert "## Fix" in content
    assert "## Verification" in content


def test_playbook_frontmatter_has_metadata(tmp_path):
    pb = _sample_playbook()
    path = save_playbook_markdown(pb, tmp_path)
    content = path.read_text()
    assert content.startswith("---")
    assert "node_type: troubleshooting_playbook" in content
    assert 'domain: "networking"' in content
    assert "reusability_score: 0.92" in content


def test_playbook_diagnostic_steps_in_order(tmp_path):
    pb = _sample_playbook()
    path = save_playbook_markdown(pb, tmp_path)
    content = path.read_text()
    bgp_pos = content.index("BGP session state")
    lsp_pos = content.index("MPLS LSP integrity")
    assert bgp_pos < lsp_pos


def test_slug_handles_unicode_and_spaces():
    assert _slug("Hello, World!  Foo Bar") == "hello-world-foo-bar"
    assert _slug("L3VPN — Issue #123") == "l3vpn-issue-123"
    assert len(_slug("a" * 200)) <= 60


# ---------------------------------------------------------------------------
# Knowledge gap normalization & domain inference
# ---------------------------------------------------------------------------

def test_normalize_strips_articles():
    assert _normalize("How to use suppressHydrationWarning") == "use suppresshydrationwarning"
    assert _normalize("What is L3VPN") == "l3vpn"
    assert _normalize("THE proper way") == "proper way"


def test_normalize_collapses_whitespace():
    assert _normalize("  multiple   spaces  ") == "multiple spaces"


def test_infer_domain_react():
    assert _infer_domain("React hydration mismatch") == "react"
    assert _infer_domain("Next.js routing") == "react"


def test_infer_domain_networking():
    assert _infer_domain("Huawei BGP L3VPN troubleshooting") == "networking"
    assert _infer_domain("MPLS configuration") == "networking"


def test_infer_domain_kubernetes():
    assert _infer_domain("kubectl logs failing") == "kubernetes"
    assert _infer_domain("helm chart deployment") == "kubernetes"


def test_infer_domain_empty_for_unknown():
    assert _infer_domain("random unrelated text") == ""


# ---------------------------------------------------------------------------
# Gap aggregation against a real DB
# ---------------------------------------------------------------------------

@pytest.fixture
async def db(tmp_path):
    d = TimelineDB(str(tmp_path / "g.db"))
    await d.migrate()
    yield d
    await d.close()


async def test_gap_inserted_from_analysis(db):
    cot = ChainOfThought(
        session_id="sess-1",
        problem_type="debugging",
        knowledge_gaps_revealed=["suppressHydrationWarning prop", "React hydration lifecycle"],
    )
    analysis = SessionAnalysis(session_id="sess-1", chain_of_thought=cot)
    n = await update_gaps_from_analysis(db, analysis)
    assert n == 2
    gaps = await db.get_top_knowledge_gaps()
    assert len(gaps) == 2
    concepts = {g["concept"] for g in gaps}
    assert any("suppress" in c for c in concepts)


async def test_gap_count_increments_on_repeat(db):
    cot = ChainOfThought(
        session_id="sess-1",
        knowledge_gaps_revealed=["React hydration"],
    )
    analysis = SessionAnalysis(session_id="sess-1", chain_of_thought=cot)
    await update_gaps_from_analysis(db, analysis)

    # Same concept, second session → count increases
    analysis.session_id = "sess-2"
    cot.session_id = "sess-2"
    await update_gaps_from_analysis(db, analysis)

    gaps = await db.get_top_knowledge_gaps()
    assert len(gaps) == 1
    assert gaps[0]["lookup_count"] == 2


async def test_gap_from_search_queries(db):
    queries = ["how to fix react hydration", "what is suppressHydrationWarning"]
    n = await update_gaps_from_search_queries(db, "sess-x", queries)
    assert n == 2
    gaps = await db.get_top_knowledge_gaps()
    concepts = {g["concept"] for g in gaps}
    assert any("hydration" in c for c in concepts)


async def test_short_queries_skipped(db):
    n = await update_gaps_from_search_queries(db, "sess-x", ["a", "  ", "ok"])
    assert n == 0


# ---------------------------------------------------------------------------
# DB playbook round-trip
# ---------------------------------------------------------------------------

async def test_playbook_save_and_retrieve(db):
    # Need a session row first (FK)
    from capman.events import Session, Event, EventType
    s = Session(dominant_app="terminal", ended_at=time.time())
    s.id = "sess-pb"
    s.events = [Event(type=EventType.SHELL_COMMAND)]
    await db.upsert_session(s)

    pb = _sample_playbook()
    pb.session_id = "sess-pb"
    await db.save_playbook(pb)

    rows = await db.get_playbooks()
    assert len(rows) == 1
    assert rows[0]["title"] == pb.title
    assert rows[0]["domain"] == "networking"


async def test_playbook_filter_by_domain(db):
    from capman.events import Session, Event, EventType
    for sid in ["sess-a", "sess-b"]:
        s = Session(dominant_app="x", ended_at=time.time())
        s.id = sid
        s.events = [Event(type=EventType.SHELL_COMMAND)]
        await db.upsert_session(s)

    pb1 = _sample_playbook()
    pb1.session_id = "sess-a"
    pb1.domain = "networking"
    await db.save_playbook(pb1)

    pb2 = _sample_playbook()
    pb2.session_id = "sess-b"
    pb2.domain = "react"
    pb2.title = "React hydration fix"
    await db.save_playbook(pb2)

    net = await db.get_playbooks(domain="networking")
    assert len(net) == 1
    assert net[0]["domain"] == "networking"

    react = await db.get_playbooks(domain="react")
    assert len(react) == 1
    assert react[0]["domain"] == "react"


async def test_playbook_sort_modes(db):
    from capman.events import Session, Event, EventType
    specs = [
        ("sess-old", "Zeta cache warmup", 1000.0, 0.9),
        ("sess-mid", "Alpha retry loop", 2000.0, 0.2),
        ("sess-new", "Mu timeout tuning", 3000.0, 0.5),
    ]
    for sid, title, created, score in specs:
        s = Session(dominant_app="x", ended_at=time.time())
        s.id = sid
        s.events = [Event(type=EventType.SHELL_COMMAND)]
        await db.upsert_session(s)
        pb = _sample_playbook()
        pb.session_id, pb.title, pb.created_at, pb.reusability_score = sid, title, created, score
        await db.save_playbook(pb)

    recent = [p["title"] for p in await db.get_playbooks(sort="recent")]
    assert recent == ["Mu timeout tuning", "Alpha retry loop", "Zeta cache warmup"]

    reuse = [p["title"] for p in await db.get_playbooks(sort="reusability")]
    assert reuse[0] == "Zeta cache warmup" and reuse[-1] == "Alpha retry loop"

    az = [p["title"] for p in await db.get_playbooks(sort="title")]
    assert az == ["Alpha retry loop", "Mu timeout tuning", "Zeta cache warmup"]

    # unknown sort falls back to recent, never raises
    assert [p["title"] for p in await db.get_playbooks(sort="bogus")] == recent


async def test_gap_sort_modes(db):
    await update_gaps_from_search_queries(db, "s1", ["what is mmap syscall semantics"])
    await update_gaps_from_search_queries(db, "s2", ["what is mmap syscall semantics"])  # freq 2
    await update_gaps_from_search_queries(db, "s3", ["how does io_uring submission work"])

    # bump io_uring's last_seen so it is the most-recent but still freq 1
    await db._db.execute(
        "UPDATE knowledge_gaps SET last_seen = 9e9 WHERE concept LIKE '%io_uring%'"
    )
    await db._db.commit()

    by_freq = [g["concept"] for g in await db.get_top_knowledge_gaps(sort="frequency")]
    assert "mmap" in by_freq[0]

    by_recent = [g["concept"] for g in await db.get_top_knowledge_gaps(sort="recent")]
    assert "io_uring" in by_recent[0]

    assert [g["concept"] for g in await db.get_top_knowledge_gaps(sort="bogus")] == by_freq
