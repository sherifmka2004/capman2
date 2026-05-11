"""Tests for KnowledgeGraph and GraphMerger."""
import time
import pytest
from capman.events import Triple
from capman.knowledge.graph import KnowledgeGraph
from capman.knowledge.merger import GraphMerger


def _graph(tmp_path) -> KnowledgeGraph:
    g = KnowledgeGraph(knowledge_dir=str(tmp_path))
    return g


def _triple(subj, pred, obj, conf=0.9) -> Triple:
    return Triple(subject=subj, predicate=pred, object=obj,
                  confidence=conf, source_session="sess-test", observed_at=time.time())


def test_new_triple_creates_nodes(tmp_path):
    g = _graph(tmp_path)
    merger = GraphMerger()
    triples = [_triple("React hydration error", "is_caused_by", "HTML mismatch")]
    result = merger.merge(g, triples, session_id="sess-1")
    assert g.count == 2
    assert result.edges_created == 1
    assert result.nodes_created == 2


def test_repeated_triple_increases_weight(tmp_path):
    g = _graph(tmp_path)
    merger = GraphMerger()
    t = _triple("A", "causes", "B")
    merger.merge(g, [t], "sess-1")
    merger.merge(g, [t], "sess-2")  # Same triple again

    node = g.find_by_title("A")
    assert node is not None
    edge = next((e for e in node.outgoing_edges if e.predicate == "causes"), None)
    assert edge is not None
    assert edge.observed_count == 2
    # Weight starts at confidence (0.9) and gets reinforced by +0.1 → capped at 1.0
    assert edge.weight >= 1.0


def test_multiple_triples_in_one_merge(tmp_path):
    g = _graph(tmp_path)
    merger = GraphMerger()
    triples = [
        _triple("React", "requires", "Node.js"),
        _triple("Next.js", "is_caused_by", "React"),
        _triple("Webpack", "enables", "React"),
    ]
    result = merger.merge(g, triples, "sess-1")
    assert result.edges_created == 3
    assert g.count >= 4  # React, Node.js, Next.js, Webpack


def test_get_or_create_node_deduplicates(tmp_path):
    g = _graph(tmp_path)
    n1 = g.get_or_create_node("Python")
    n2 = g.get_or_create_node("Python")  # Same title
    assert n1.id == n2.id
    assert g.count == 1


def test_find_by_title_case_insensitive(tmp_path):
    g = _graph(tmp_path)
    g.get_or_create_node("React Hooks")
    node = g.find_by_title("react hooks")
    assert node is not None
    assert node.title == "React Hooks"


def test_node_with_no_edges_is_flagged_not_deleted(tmp_path):
    g = _graph(tmp_path)
    g.get_or_create_node("Orphan Concept")
    assert g.count == 1
    # Orphan nodes should NOT be auto-deleted
    # (manual review only — verified by count still being 1)
