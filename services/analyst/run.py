"""Analyst worker entry point — one analysis cycle per invocation.

Deployed as a Railway cron (every 15 min). Pipeline per tenant:
  fetch unassigned events → segment into episodes → signature → persist →
  select (budget policy) → LLM passes → persist analyses/triples/playbooks/gaps →
  verify matured playbooks.

Usage:
  python run.py --manifests path/a.toml,path/b.toml [--dsn DSN] [--tenant product:acme]
                [--dry-run] [--config /path/to/capman/config.toml]

--dry-run reports what a cycle WOULD do — episode counts, selection, and the
number of LLM calls a day's data would generate — without writing or calling.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from capman.pipeline.web_analyzer import WebEpisodeAnalyzer
from capman.web.manifest import ProductManifest, load_manifests, tenant_id

from analyst_db import AnalystDB
from segment import segment_events
from selection import select_episodes
from signature import coarse_signature, signature
from verify import verify_playbooks

logger = logging.getLogger("analyst")

GAP_MIN_FREQUENCY = 3


def _sign_episodes(episodes: list[dict]) -> None:
    for ep in episodes:
        ep["signature"] = signature(ep["names"])
        ep["coarse_signature"] = coarse_signature(ep["names"])


def estimate_llm_calls(selected: list[dict]) -> dict:
    return {
        "pass1_haiku": len(selected),
        "pass2_sonnet_max": len(selected),
        "pass3_haiku_max": len(selected),
        "pass4_sonnet_max": len(selected),
        "note": "pass2-4 are gated on severity/frequency decided at runtime; max shown",
    }


async def run_cycle(tenant: str, manifest: ProductManifest, db: AnalystDB,
                    analyzer: WebEpisodeAnalyzer, dry_run: bool = False,
                    now: float | None = None) -> dict:
    now = now if now is not None else time.time()
    stats: dict = {"tenant": tenant, "new_episodes": 0, "analyzed": 0}

    rows = await db.fetch_unassigned_events(tenant)
    stats["unassigned_events"] = len(rows)
    fresh = segment_events(rows, manifest, manifest.key)
    _sign_episodes(fresh)
    stats["new_episodes"] = len(fresh)

    known = await db.fetch_known_signatures(tenant)
    clusters_week = await db.fetch_clusters_analyzed_this_week(tenant, now)

    if dry_run:
        candidates = fresh
    else:
        await db.persist_episodes(tenant, fresh)
        candidates = []
        for stored in await db.fetch_unanalyzed_episodes(tenant):
            events = await db.fetch_episode_events(tenant, stored["id"])
            stored["events"] = events
            stored["names"] = [e["name"] for e in events]
            stored["friction_flags"] = json.loads(stored.get("friction_flags") or "[]")
            candidates.append(stored)

    selected, sel_stats = select_episodes(candidates, known, clusters_week)
    stats["selection"] = sel_stats
    stats["llm_calls_estimate"] = estimate_llm_calls(selected)

    if dry_run:
        return stats

    freq = Counter(c["coarse_signature"] for c in candidates)
    analyzed_ids = []
    for ep in selected:
        cluster = ep["coarse_signature"]
        f = freq.get(cluster, 1)
        outcome_dist = dict(Counter(
            c["outcome"] for c in candidates if c["coarse_signature"] == cluster
        ))
        result = await analyzer.analyze_episode(ep, cluster_frequency=f,
                                                outcome_distribution=outcome_dist)
        pass1 = result.get("pass1")
        if pass1:
            await db.insert_analysis(tenant, ep["id"], pass1,
                                     result.get("chain_of_thought"),
                                     result.get("triples"),
                                     getattr(analyzer, "_pass1_model", ""))
            if result.get("triples"):
                await db.insert_triples(tenant, ep["id"], result["triples"])
            if result.get("playbook"):
                await db.upsert_playbook(tenant, ep["id"], result["playbook"])
            failing = ep["outcome"].startswith("abandoned_at") or ep["outcome"] == "errored"
            if failing and f >= GAP_MIN_FREQUENCY:
                concept = pass1.get("intent_statement") or ep["outcome"]
                await db.upsert_gap(tenant, concept, cluster, f, ep["id"])
            analyzed_ids.append(ep["id"])
        elif getattr(analyzer, "_backend", "") == "none":
            # No LLM configured at all: mark analyzed so cycles stay cheap;
            # an LLM *error* instead leaves analyzed=0 for the next retry.
            analyzed_ids.append(ep["id"])
        else:
            stats["llm_failures"] = stats.get("llm_failures", 0) + 1
        elif getattr(analyzer, "_backend", "") == "none":
            # No LLM configured at all: mark analyzed so cycles stay cheap;
            # an LLM *error* instead leaves analyzed=0 for the next retry.
            analyzed_ids.append(ep["id"])
        else:
            stats["llm_failures"] = stats.get("llm_failures", 0) + 1

    await db.mark_analyzed(tenant, analyzed_ids)
    stats["analyzed"] = len(analyzed_ids)
    stats["verifications"] = await verify_playbooks(tenant, db, now)
    return stats


async def amain(args: argparse.Namespace) -> int:
    manifests = load_manifests([p for p in args.manifests.split(",") if p.strip()])
    dsn = args.dsn or os.environ.get("CAPMAN_PG_DSN", "")
    if not dsn:
        print("error: --dsn or CAPMAN_PG_DSN required", file=sys.stderr)
        return 2

    config: dict = {}
    if args.config:
        import tomllib
        with open(args.config, "rb") as f:
            config = tomllib.load(f)

    db = AnalystDB(dsn)
    await db.connect()
    try:
        for key, manifest in manifests.items():
            tenant = tenant_id(manifest)
            if args.tenant and tenant != args.tenant:
                continue
            analyzer = WebEpisodeAnalyzer(config, manifest)
            stats = await run_cycle(tenant, manifest, db, analyzer,
                                    dry_run=args.dry_run)
            print(json.dumps(stats, indent=2, default=str))
    finally:
        await db.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="capman web-behavior analyst worker")
    parser.add_argument("--manifests", required=True,
                        help="comma-separated product manifest TOML paths")
    parser.add_argument("--dsn", default="", help="Postgres DSN (or CAPMAN_PG_DSN env)")
    parser.add_argument("--tenant", default="",
                        help="restrict to one tenant, e.g. product:acme (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report counts and LLM call estimate without writing or calling")
    parser.add_argument("--config", default="",
                        help="optional capman config TOML for LLM model overrides")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
