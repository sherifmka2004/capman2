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


#: Leading interrogatives and articles carry no signal for concept identity —
#: "How to use X", "What is X" and "X" are all the same gap.
_LEADING_NOISE = (
    "how do i", "how to", "how does", "how can i", "what is", "what are",
    "what does", "why is", "why does", "when to", "where is", "the", "a", "an",
)

#: Keyword → domain. Deliberately small and explicit rather than learned: it
#: only has to be good enough to group gaps in the UI.
_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "react": ("react", "next.js", "nextjs", "hydration", "jsx", "hooks", "redux"),
    "networking": ("bgp", "mpls", "l3vpn", "vpn", "huawei", "ospf", "router",
                   "subnet", "dns", "tcp", "vlan"),
    "kubernetes": ("kubernetes", "k8s", "kubectl", "helm", "pod", "ingress",
                   "kubelet", "namespace"),
    "database": ("postgres", "postgresql", "sqlite", "mysql", "sql", "index",
                 "query plan", "transaction"),
    "python": ("python", "asyncio", "pytest", "pip", "venv", "django", "flask"),
    "git": ("git", "rebase", "merge conflict", "bisect", "cherry-pick"),
}


def _normalize(text: str) -> str:
    """Canonical form of a gap phrase: lowercased, de-noised, whitespace-collapsed.

    Used for identity, so "How to use X", "what is X" and "X" converge on one
    gap instead of three.
    """
    out = " ".join((text or "").lower().split())
    changed = True
    while changed:
        changed = False
        for prefix in _LEADING_NOISE:
            if out.startswith(prefix + " "):
                out = out[len(prefix) + 1:].lstrip()
                changed = True
    return out.strip().rstrip("?.,;:")


def _infer_domain(text: str) -> str:
    """Best-effort domain label for a gap. Empty string when nothing matches."""
    lowered = (text or "").lower()
    best, best_pos = "", len(lowered) + 1
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        for kw in keywords:
            pos = lowered.find(kw)
            if pos != -1 and pos < best_pos:
                best, best_pos = domain, pos
    return best


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
    text = text[:80]
    # Capitalise the first word for display — but never when the first token is
    # a camelCase identifier, or `suppressHydrationWarning` becomes
    # `SuppressHydrationWarning`, which is a different (and wrong) symbol.
    first = text.split(" ", 1)[0] if text else ""
    looks_like_identifier = any(c.isupper() for c in first[1:]) or "_" in first or "." in first
    if text and not looks_like_identifier:
        text = text[0].upper() + text[1:]
    return text or "unknown concept"


async def update_gaps_from_analysis(db, analysis: SessionAnalysis) -> int:
    """Write gaps the LLM revealed. Returns how many were recorded."""
    cot = analysis.chain_of_thought
    if not cot or not cot.knowledge_gaps_revealed:
        return 0

    now = time.time()
    written = 0
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
            sessions=[analysis.session_id],
            first_seen=now,
            last_seen=now,
        )
        await db.upsert_knowledge_gap(gap)
        written += 1
    return written


#: Below this, a query is navigational noise ("a", "ok", "cd") rather than a
#: question worth recording as a gap.
MIN_QUERY_CHARS = 3


async def update_gaps_from_search_queries(db, session_id: str, queries: list[str]) -> int:
    """Treat repeated search queries as evidence of a knowledge gap.

    Returns how many were recorded.
    """
    if not queries:
        return 0

    now = time.time()
    written = 0
    for query in queries:
        query = query.strip()
        if len(query) < MIN_QUERY_CHARS:
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
        written += 1
    return written
