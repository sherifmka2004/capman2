"""Playbook verification — did the shipped fix actually move the metric?

For each playbook whose 14-day post-creation window has elapsed and that has
no verification row yet: recompute the cluster's episode rate in the 14 days
before vs after creation, record the verdict, and mark the linked
knowledge gaps resolved when the rate dropped with sufficient volume.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

VERIFICATION_WINDOW_DAYS = 14
MIN_VOLUME = 20
IMPROVED_RATIO = 0.7
REGRESSED_RATIO = 1.3


def classify(baseline_rate: float, observed_rate: float,
             base_total: int, obs_total: int) -> str:
    if base_total < MIN_VOLUME or obs_total < MIN_VOLUME:
        return "insufficient_volume"
    if baseline_rate <= 0:
        return "unchanged" if observed_rate <= 0 else "regressed"
    if observed_rate <= baseline_rate * IMPROVED_RATIO:
        return "improved"
    if observed_rate >= baseline_rate * REGRESSED_RATIO:
        return "regressed"
    return "unchanged"


async def verify_playbooks(tenant: str, db, now: float | None = None) -> list[dict]:
    now = now if now is not None else time.time()
    window_s = VERIFICATION_WINDOW_DAYS * 86400.0
    cutoff = now - window_s
    pending = await db.fetch_playbooks_pending_verification(tenant, cutoff)

    results = []
    for pb in pending:
        created = float(pb["created_at"])
        cluster_id = pb["cluster_id"]
        base_hits, base_total = await db.cluster_rate(tenant, cluster_id, created - window_s, created)
        obs_hits, obs_total = await db.cluster_rate(tenant, cluster_id, created, created + window_s)
        baseline_rate = base_hits / base_total if base_total else 0.0
        observed_rate = obs_hits / obs_total if obs_total else 0.0
        verdict = classify(baseline_rate, observed_rate, base_total, obs_total)

        await db.insert_verification(tenant, pb["id"], created, created + window_s,
                                     baseline_rate, observed_rate, verdict)
        if verdict == "improved":
            await db.resolve_gaps_for_cluster(tenant, cluster_id)
        results.append({"playbook_id": pb["id"], "verdict": verdict,
                        "baseline_rate": baseline_rate, "observed_rate": observed_rate})
        logger.info("Verified playbook %s: %s (%.3f -> %.3f)",
                    pb["id"], verdict, baseline_rate, observed_rate)
    return results
