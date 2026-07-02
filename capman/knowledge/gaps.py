"""Persist and update knowledge gap records from session analysis."""
from __future__ import annotations

import re
import time

from capman.events import KnowledgeGap, SessionAnalysis

# Strip common verbose suffixes the LLM appends after the core concept.
# e.g. "LightGBM capabilities (had to search rather than knowing from memory)"
#   → "LightGBM capabilities"
_SUFFIX_RE = re.compile(
    r"\s*[\(\[].*?[\)\]]"          # trailing parenthetical/bracketed annotation
    r"|\s*—.*$"                    # em-dash clause
    r"|\s*,\s*(requiring|needed|without|rather than|suggesting).*$",
    re.IGNORECASE,
)

# Map verbose phrases to short canonical names (extend as needed)
_CANONICAL = {
    "user did not know": "",  # drop—the concept follows after
    "user was uncertain": "",
    "user needed to": "",
    "user initially": "",
    "reliance on": "",
}


def _normalize_concept(text: str) -> str:
    """Extract a short (≤8-word) canonical concept name from a verbose gap sentence."""
    text = text.strip()
    # Strip trailing parenthetical / em-dash explanations
    text = _SUFFIX_RE.sub("", text).strip().rstrip(".,;")
    # Remove common leading noise phrases
    for prefix, replacement in _CANONICAL.items():
        lower = text.lower()
        if lower.startswith(prefix):
            text = (replacement + text[len(prefix):]).strip()
    # Capitalise first word, truncate to 80 chars
    text = text[:80]
    if text:
        text = text[0].upper() + text[1:]
    return text or "unknown concept"


async def update_gaps_from_analysis(db, analysis: SessionAnalysis) -> None:
    """Write gaps extracted by the LLM (knowledge_gaps_revealed on chain-of-thought)."""
    cot = analysis.chain_of_thought
    if not cot or not cot.knowledge_gaps_revealed:
        return

    now = time.time()
    for raw_concept in cot.knowledge_gaps_revealed:
        raw_concept = raw_concept.strip()
        if not raw_concept:
            continue
        concept = _normalize_concept(raw_concept)
        gap = KnowledgeGap(
            concept=concept,
            query_examples=[raw_concept] if raw_concept != concept else [],
            domain=analysis.chain_of_thought.problem_type if cot else "",
            lookup_count=1,
            query_examples=[],
            sessions=[analysis.session_id],
            first_seen=now,
            last_seen=now,
        )
        await db.upsert_knowledge_gap(gap)


async def update_gaps_from_search_queries(db, session_id: str, queries: list[str]) -> None:
    """Treat repeated search queries as evidence of a knowledge gap."""
    if not queries:
        return

    now = time.time()
    for query in queries:
        query = query.strip()
        if not query:
            continue
        concept = _normalize_concept(query)
        gap = KnowledgeGap(
            concept=concept,
            domain="",
            lookup_count=1,
            query_examples=[query],
            sessions=[session_id],
            first_seen=now,
            last_seen=now,
        )
        await db.upsert_knowledge_gap(gap)
