"""Persistent knowledge graph backed by a JSON file in knowledge_dir."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, field
from pathlib import Path

from capman.events import ChainOfThought, KnowledgeEdge, KnowledgeNode

logger = logging.getLogger(__name__)

_GRAPH_FILE = "graph.json"


class KnowledgeGraph:
    def __init__(self, knowledge_dir: str = "~/.capman/knowledge") -> None:
        self._dir = Path(knowledge_dir).expanduser()
        self.nodes: dict[str, KnowledgeNode] = {}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        path = self._dir / _GRAPH_FILE
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for nd in data.get("nodes", []):
                edges = [
                    KnowledgeEdge(
                        predicate=e.get("predicate", ""),
                        target_id=e.get("target_id", ""),
                        weight=e.get("weight", 1.0),
                        observed_count=e.get("observed_count", 1),
                        last_observed=e.get("last_observed", time.time()),
                    )
                    for e in nd.get("outgoing_edges", [])
                ]
                node = KnowledgeNode(
                    id=nd["id"],
                    title=nd.get("title", ""),
                    node_type=nd.get("node_type", ""),
                    summary=nd.get("summary", ""),
                    tags=nd.get("tags", []),
                    first_seen=nd.get("first_seen", time.time()),
                    last_updated=nd.get("last_updated", time.time()),
                    source_sessions=nd.get("source_sessions", []),
                    outgoing_edges=edges,
                    obsidian_path=nd.get("obsidian_path", ""),
                )
                self.nodes[node.id] = node
        except Exception as e:
            logger.error("Failed to load knowledge graph: %s", e)

    def save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / _GRAPH_FILE
        try:
            data = {"nodes": [self._node_to_dict(n) for n in self.nodes.values()]}
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.error("Failed to save knowledge graph: %s", e)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    @property
    def count(self) -> int:
        """Number of nodes currently in the graph."""
        return len(self.nodes)

    def find_by_title(self, title: str) -> KnowledgeNode | None:
        """Look a node up by title, case-insensitively.

        Concept titles arrive from LLM output, where capitalisation is not
        stable between runs — "React Hooks" and "react hooks" are the same
        concept and must resolve to the same node.
        """
        target = (title or "").strip().lower()
        if not target:
            return None
        for node in self.nodes.values():
            if (node.title or "").strip().lower() == target:
                return node
        return None

    def get_or_create_node(self, title: str, node_type: str = "concept",
                           session_id: str = "") -> KnowledgeNode:
        """Return the node for `title`, creating it if absent.

        Identity is the slug of the title, so repeated mentions of a concept
        converge on one node instead of accumulating near-duplicates.
        """
        node_id = _slug(title)
        node = self.nodes.get(node_id)
        if node is None:
            node = KnowledgeNode(
                id=node_id,
                title=title,
                node_type=node_type,
                source_sessions=[session_id] if session_id else [],
            )
            self.nodes[node_id] = node
            return node

        node.last_updated = time.time()
        if session_id and session_id not in node.source_sessions:
            node.source_sessions.append(session_id)
        return node

    def add_chain_of_thought(self, cot: ChainOfThought) -> None:
        """Upsert a concept node for each knowledge gap revealed in the CoT."""
        for gap_text in cot.knowledge_gaps_revealed:
            gap_text = gap_text.strip()
            if not gap_text:
                continue
            node_id = _slug(gap_text)
            if node_id in self.nodes:
                node = self.nodes[node_id]
                node.last_updated = time.time()
                if cot.session_id and cot.session_id not in node.source_sessions:
                    node.source_sessions.append(cot.session_id)
            else:
                self.nodes[node_id] = KnowledgeNode(
                    id=node_id,
                    title=gap_text,
                    node_type="concept",
                    summary=f"Knowledge gap revealed during session {cot.session_id}",
                    tags=["gap"],
                    source_sessions=[cot.session_id] if cot.session_id else [],
                )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _node_to_dict(n: KnowledgeNode) -> dict:
        return {
            "id": n.id,
            "title": n.title,
            "node_type": n.node_type,
            "summary": n.summary,
            "tags": n.tags,
            "first_seen": n.first_seen,
            "last_updated": n.last_updated,
            "source_sessions": n.source_sessions,
            "obsidian_path": n.obsidian_path,
            "outgoing_edges": [
                {
                    "predicate": e.predicate,
                    "target_id": e.target_id,
                    "weight": e.weight,
                    "observed_count": e.observed_count,
                    "last_observed": e.last_observed,
                }
                for e in n.outgoing_edges
            ],
        }


def _slug(text: str) -> str:
    """Stable node ID from a concept title."""
    import re
    return re.sub(r"[^a-z0-9_]+", "_", text.lower().strip())[:80]
