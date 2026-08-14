"""Merge LLM-extracted triples into a KnowledgeGraph."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

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


#: Repeated observation of the same relation reinforces it by this much.
EDGE_REINFORCEMENT = 0.1
#: Edge weights saturate here — a relation seen fifty times is not fifty times
#: truer than one seen twice, and an unbounded weight would swamp ranking.
MAX_EDGE_WEIGHT = 1.0


@dataclass
class MergeResult:
    """What a merge actually changed. Returned so callers can log or test it."""
    nodes_created: int = 0
    nodes_updated: int = 0
    edges_created: int = 0
    edges_reinforced: int = 0

    @property
    def total_changes(self) -> int:
        return self.nodes_created + self.nodes_updated + self.edges_created + self.edges_reinforced


class GraphMerger:
    def merge(self, graph: KnowledgeGraph, triples: list[Triple],
              session_id: str = "") -> MergeResult:
        result = MergeResult()
        for triple in triples:
            subj_norm = _normalize_entity(triple.subject)
            obj_norm = _normalize_entity(triple.object)
            subj_id = _slug(subj_norm)
            obj_id = _slug(obj_norm)

            for node_id, title in ((subj_id, subj_norm), (obj_id, obj_norm)):
                if self._upsert_node(graph, node_id, title, session_id):
                    result.nodes_created += 1
                else:
                    result.nodes_updated += 1

            if self._upsert_edge(graph, subj_id, obj_id, triple.predicate,
                                 getattr(triple, "confidence", 1.0)):
                result.edges_created += 1
            else:
                result.edges_reinforced += 1
        return result

    @staticmethod
    def _upsert_node(graph: KnowledgeGraph, node_id: str, title: str,
                     session_id: str) -> bool:
        """Returns True when a new node was created."""
        if node_id in graph.nodes:
            node = graph.nodes[node_id]
            node.last_updated = time.time()
            if session_id and session_id not in node.source_sessions:
                node.source_sessions.append(session_id)
            return False

        graph.nodes[node_id] = KnowledgeNode(
            id=node_id,
            title=title,
            node_type="concept",
            source_sessions=[session_id] if session_id else [],
        )
        return True

    @staticmethod
    def _upsert_edge(graph: KnowledgeGraph, subj_id: str, obj_id: str,
                     predicate: str, confidence: float = 1.0) -> bool:
        """Returns True when a new edge was created.

        A new edge starts at the triple's confidence rather than at 1.0, so a
        hesitant extraction does not enter the graph as strong as a certain
        one; repeat observations then reinforce it toward the ceiling.
        """
        node = graph.nodes.get(subj_id)
        if node is None:
            return False
        for edge in node.outgoing_edges:
            if edge.target_id == obj_id and edge.predicate == predicate:
                edge.observed_count += 1
                edge.weight = min(edge.weight + EDGE_REINFORCEMENT, MAX_EDGE_WEIGHT)
                edge.last_observed = time.time()
                return False
        node.outgoing_edges.append(
            KnowledgeEdge(predicate=predicate, target_id=obj_id,
                          weight=min(confidence, MAX_EDGE_WEIGHT))
        )
        return True
