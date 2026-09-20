"""LLM budget policy — choose which episodes the analyzer actually reads.

Everything else is counted, not read. Rules (from the plan):
  1. one representative per coarse cluster with >= CLUSTER_MIN occurrences
     whose cluster has not been analyzed this week;
  2. every episode carrying a friction flag, capped per day;
  3. every never-before-seen exact signature, capped per day.
"""
from __future__ import annotations

from collections import Counter

CLUSTER_MIN = 3
FRICTION_DAILY_CAP = 20
NOVEL_DAILY_CAP = 10


def select_episodes(
    episodes: list[dict],
    known_signatures: set[str],
    clusters_analyzed_this_week: set[str],
) -> tuple[list[dict], dict]:
    """Return (selected episodes, stats). Each episode dict needs at least
    id, signature, coarse_signature, friction_flags. Selection is deterministic:
    episodes are considered in the order given."""
    selected: dict[str, dict] = {}
    reasons: Counter = Counter()

    cluster_counts = Counter(e["coarse_signature"] for e in episodes)
    selected_signatures: set[str] = set()
    selected_clusters: set[str] = set()

    friction_budget = FRICTION_DAILY_CAP
    novel_budget = NOVEL_DAILY_CAP

    for ep in episodes:
        if ep["id"] in selected:
            continue

        if ep["friction_flags"] and friction_budget > 0:
            selected[ep["id"]] = ep
            selected_signatures.add(ep["signature"])
            selected_clusters.add(ep["coarse_signature"])
            friction_budget -= 1
            reasons["friction"] += 1
            continue

        if (ep["signature"] not in known_signatures
                and ep["signature"] not in selected_signatures
                and novel_budget > 0):
            selected[ep["id"]] = ep
            selected_signatures.add(ep["signature"])
            selected_clusters.add(ep["coarse_signature"])
            novel_budget -= 1
            reasons["novel"] += 1
            continue

        coarse = ep["coarse_signature"]
        if (cluster_counts[coarse] >= CLUSTER_MIN
                and coarse not in clusters_analyzed_this_week
                and coarse not in selected_clusters):
            selected[ep["id"]] = ep
            selected_signatures.add(ep["signature"])
            selected_clusters.add(coarse)
            reasons["cluster_representative"] += 1

    stats = {
        "total_episodes": len(episodes),
        "selected": len(selected),
        "by_reason": dict(reasons),
        "skipped": len(episodes) - len(selected),
    }
    return list(selected.values()), stats
