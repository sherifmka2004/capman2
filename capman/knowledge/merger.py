"""Merge LLM-extracted triples into a KnowledgeGraph."""
from __future__ import annotations

import logging
import re
import time

from capman.events import KnowledgeEdge, KnowledgeNode, Triple
from capman.knowledge.graph import KnowledgeGraph, _slug

logger = logging.getLogger(__name__)

# Common stopwords to drop from entity names before slugging
_STOPWORDS = frozenset(
    "a an the this that these those is are was were be been being "
    "have has had do does did will would could should may might "
    "of in on at to for with by from about".split()
)

# Common aliases: normalize variant surface forms to a canonical label
_ALIASES: dict[str, str] = {
    "railway cli": "railway",
    "railway command-line": "railway",
    "railway command line": "railway",
    "github oauth": "github oauth",
    "pkce cookie": "pkce",
    "apify actor": "apify actor",
    "google chrome": "chrome",
    "chromium": "chrome",
}


def _normalize_entity(text: str) -> str:
    """Lowercase, strip stopwords, apply alias map for stable cross-session slugs."""
    t = text.lower().strip()
    # Apply alias map first (longest-match)
    for alias, canonical in sorted(_ALIASES.items(), key=lambda x: -len(x[0])):
        if alias in t:
            t = t.replace(alias, canonical)
    # Strip stopwords from edges only (preserve interior words)
    words = t.split()
    if len(words) > 2:
        words = [w for w in words if w not in _STOPWORDS or len(words) <= 2]
    return " ".join(words)


class GraphMerger:
    def merge(self, graph: KnowledgeGraph, triples: list[Triple], session_id: str = "") -> None:
        for triple in triples:
            subj_id = _slug(_normalize_entity(triple.subject))
            obj_id = _slug(_normalize_entity(triple.object))

            subj_norm = _normalize_entity(triple.subject)
            obj_norm = _normalize_entity(triple.object)
            self._upsert_node(graph, subj_id, subj_norm, session_id)
            self._upsert_node(graph, obj_id, obj_norm, session_id)
            self._upsert_edge(graph, subj_id, obj_id, triple.predicate)

    @staticmethod
    def _upsert_node(graph: KnowledgeGraph, node_id: str, title: str, session_id: str) -> None:
        if node_id in graph.nodes:
            node = graph.nodes[node_id]
            node.last_updated = time.time()
            if session_id and session_id not in node.source_sessions:
                node.source_sessions.append(session_id)
        else:
            graph.nodes[node_id] = KnowledgeNode(
                id=node_id,
                title=title,
                node_type="concept",
                source_sessions=[session_id] if session_id else [],
            )

    @staticmethod
    def _upsert_edge(graph: KnowledgeGraph, subj_id: str, obj_id: str, predicate: str) -> None:
        node = graph.nodes.get(subj_id)
        if node is None:
            return
        for edge in node.outgoing_edges:
            if edge.target_id == obj_id and edge.predicate == predicate:
                edge.observed_count += 1
                edge.weight = min(edge.weight + 0.1, 5.0)
                edge.last_observed = time.time()
                return
        node.outgoing_edges.append(
            KnowledgeEdge(predicate=predicate, target_id=obj_id)
        )
