"""Postgres access for the analyst worker.

Every operation runs inside a transaction that first sets capman.user_id
(is_local=true) — RLS is FORCEd, so the tenant boundary holds even though a
single pool/role serves all products. asyncpg is imported lazily so unit
tests run without it.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class AnalystDB:
    def __init__(self, dsn: str, min_pool: int = 1, max_pool: int = 3):
        self._dsn = dsn
        self._min_pool = min_pool
        self._max_pool = max_pool
        self._pool: Any = None

    async def connect(self) -> None:
        if self._pool is None:
            import asyncpg
            self._pool = await asyncpg.create_pool(self._dsn, min_size=self._min_pool,
                                                   max_size=self._max_pool)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    class _TenantTx:
        def __init__(self, db: "AnalystDB", tenant: str):
            self._db = db
            self._tenant = tenant
            self._conn = None
            self._tx = None

        async def __aenter__(self):
            if self._db._pool is None:
                raise RuntimeError("db not connected")
            self._conn = await self._db._pool.acquire()
            self._tx = self._conn.transaction()
            await self._tx.start()
            await self._conn.execute("SELECT set_config('capman.user_id', $1, true)", self._tenant)
            return self._conn

        async def __aexit__(self, exc_type, exc, tb):
            await self._tx.commit() if exc_type is None else await self._tx.rollback()
            await self._db._pool.release(self._conn)
            return False

    def tx(self, tenant: str) -> "AnalystDB._TenantTx":
        return AnalystDB._TenantTx(self, tenant)

    async def fetch_unassigned_events(self, tenant: str) -> list[dict]:
        async with self.tx(tenant) as conn:
            rows = await conn.fetch(
                "SELECT id, ts, type, payload FROM events "
                "WHERE sensor_id = 'web' AND session_id IS NULL ORDER BY ts"
            )
        out = []
        for r in rows:
            try:
                payload = json.loads(r["payload"])
            except (json.JSONDecodeError, TypeError):
                continue
            out.append({"id": r["id"], "ts": r["ts"], "name": r["type"], "payload": payload})
        return out

    async def persist_episodes(self, tenant: str, episodes: list[dict]) -> int:
        if not episodes:
            return 0
        async with self.tx(tenant) as conn:
            await conn.executemany(
                "INSERT INTO web_episodes (id, user_id, sid_hash, started_at, ended_at, "
                "signature, coarse_signature, outcome, step_reached, n_events, friction_flags, "
                "cluster_id, analyzed) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,0)",
                [
                    (e["id"], tenant, e["sid_hash"], e["started_at"], e["ended_at"],
                     e["signature"], e["coarse_signature"], e["outcome"], e["step_reached"],
                     e["n_events"], json.dumps(e["friction_flags"]), e["coarse_signature"])
                    for e in episodes
                ],
            )
            for e in episodes:
                await conn.execute(
                    "UPDATE events SET session_id = $1 WHERE id = ANY($2::text[])",
                    e["id"], [r["id"] for r in e["event_rows"]],
                )
        return len(episodes)

    async def fetch_known_signatures(self, tenant: str) -> set[str]:
        async with self.tx(tenant) as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT signature FROM web_episodes WHERE analyzed = 1"
            )
        return {r["signature"] for r in rows}

    async def fetch_clusters_analyzed_this_week(self, tenant: str, now: float | None = None) -> set[str]:
        now = now if now is not None else time.time()
        async with self.tx(tenant) as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT cluster_id FROM web_episodes "
                "WHERE analyzed = 1 AND started_at > $1", now - 7 * 86400,
            )
        return {r["cluster_id"] for r in rows}

    async def fetch_unanalyzed_episodes(self, tenant: str) -> list[dict]:
        async with self.tx(tenant) as conn:
            rows = await conn.fetch(
                "SELECT id, sid_hash, started_at, ended_at, signature, coarse_signature, "
                "outcome, step_reached, n_events, friction_flags, cluster_id "
                "FROM web_episodes WHERE analyzed = 0 ORDER BY started_at"
            )
        return [dict(r) for r in rows]

    async def fetch_episode_events(self, tenant: str, episode_id: str) -> list[dict]:
        async with self.tx(tenant) as conn:
            rows = await conn.fetch(
                "SELECT id, ts, type, payload FROM events WHERE session_id = $1 ORDER BY ts",
                episode_id,
            )
        out = []
        for r in rows:
            payload = json.loads(r["payload"])
            out.append({"name": r["type"], "props": payload.get("props") or {},
                        "t_rel_ms": payload.get("t_rel_ms", 0), "seq": payload.get("seq", 0)})
        return out

    async def mark_analyzed(self, tenant: str, episode_ids: list[str]) -> None:
        if not episode_ids:
            return
        async with self.tx(tenant) as conn:
            await conn.execute(
                "UPDATE web_episodes SET analyzed = 1 WHERE id = ANY($1::text[])", episode_ids
            )

    async def insert_analysis(self, tenant: str, episode_id: str, pass1: dict,
                              cot: dict | None, triples: list | None, model_used: str) -> None:
        async with self.tx(tenant) as conn:
            await conn.execute(
                "INSERT INTO session_analyses (session_id, user_id, problem_statement, "
                "approach_description, methodology_tags, knowledge_applied, knowledge_acquired, "
                "chain_of_thought, triples, confidence, model_used, analyzed_at) "
                "VALUES ($1,$2,$3,$4,$5,'[]','[]',$6,$7,$8,$9,$10) "
                "ON CONFLICT (session_id) DO UPDATE SET "
                "problem_statement = EXCLUDED.problem_statement, "
                "approach_description = EXCLUDED.approach_description, "
                "chain_of_thought = EXCLUDED.chain_of_thought, "
                "triples = EXCLUDED.triples, confidence = EXCLUDED.confidence, "
                "analyzed_at = EXCLUDED.analyzed_at",
                episode_id, tenant,
                pass1.get("intent_statement", ""),
                pass1.get("attempt_path", ""),
                json.dumps([f"outcome:{pass1.get('outcome_assessment', '')[:40]}"]),
                json.dumps(cot) if cot else None,
                json.dumps(triples) if triples else "[]",
                float(pass1.get("confidence", 0.0)),
                model_used, time.time(),
            )

    async def insert_triples(self, tenant: str, episode_id: str, triples: list[dict]) -> int:
        if not triples:
            return 0
        async with self.tx(tenant) as conn:
            await conn.executemany(
                "INSERT INTO knowledge_triples (id, user_id, subject, predicate, object, "
                "confidence, observed_count, first_seen, last_observed, source_session) "
                "VALUES ($1,$2,$3,$4,$5,$6,1,$7,$7,$8)",
                [
                    (str(uuid.uuid4()), tenant, t.get("subject", ""), t.get("predicate", ""),
                     t.get("object", ""), float(t.get("confidence", 1.0)), time.time(), episode_id)
                    for t in triples
                    if t.get("subject") and t.get("predicate") and t.get("object")
                ],
            )
        return len(triples)

    async def upsert_playbook(self, tenant: str, episode_id: str, pb: dict) -> str:
        pb_id = str(uuid.uuid4())
        async with self.tx(tenant) as conn:
            await conn.execute(
                "INSERT INTO playbooks (id, user_id, session_id, title, domain, symptoms, "
                "context_signals, diagnostic_steps, root_cause, fix, verification, "
                "references_json, related_playbooks, reusability_score, created_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'[]','[]',$12,$13)",
                pb_id, tenant, episode_id, pb.get("title", "Untitled friction playbook"),
                pb.get("domain", ""), json.dumps(pb.get("symptoms", [])),
                json.dumps(pb.get("context_signals", [])),
                json.dumps(pb.get("diagnostic_steps", [])), pb.get("root_cause", ""),
                json.dumps(pb.get("fix", [])), json.dumps(pb.get("verification", [])),
                float(pb.get("reusability_score", 0.0)), time.time(),
            )
        return pb_id

    async def upsert_gap(self, tenant: str, concept: str, domain: str,
                         frequency: int, episode_id: str) -> None:
        now = time.time()
        async with self.tx(tenant) as conn:
            await conn.execute(
                "INSERT INTO knowledge_gaps (id, user_id, concept, domain, lookup_count, "
                "query_examples, sessions, first_seen, last_seen, resolved) "
                "VALUES ($1,$2,$3,$4,$5,'[]',$6,$7,$7,0) "
                "ON CONFLICT (user_id, concept) DO UPDATE SET "
                "lookup_count = knowledge_gaps.lookup_count + $5, last_seen = $7",
                str(uuid.uuid4()), tenant, concept[:120], domain, frequency,
                json.dumps([episode_id]), now,
            )

    async def fetch_playbooks_pending_verification(self, tenant: str, cutoff: float) -> list[dict]:
        async with self.tx(tenant) as conn:
            rows = await conn.fetch(
                "SELECT p.id, p.created_at, e.cluster_id FROM playbooks p "
                "JOIN web_episodes e ON e.id = p.session_id "
                "WHERE p.created_at <= $1 AND NOT EXISTS ("
                "  SELECT 1 FROM playbook_verifications v WHERE v.playbook_id = p.id)",
                cutoff,
            )
        return [dict(r) for r in rows]

    async def cluster_rate(self, tenant: str, cluster_id: str,
                           start: float, end: float) -> tuple[int, int]:
        async with self.tx(tenant) as conn:
            row = await conn.fetchrow(
                "SELECT count(*) FILTER (WHERE cluster_id = $1) AS hits, count(*) AS total "
                "FROM web_episodes WHERE started_at >= $2 AND started_at < $3",
                cluster_id, start, end,
            )
        return int(row["hits"]), int(row["total"])

    async def insert_verification(self, tenant: str, playbook_id: str, window_start: float,
                                  window_end: float, baseline_rate: float,
                                  observed_rate: float, verdict: str) -> None:
        async with self.tx(tenant) as conn:
            await conn.execute(
                "INSERT INTO playbook_verifications (id, user_id, playbook_id, window_start, "
                "window_end, baseline_rate, observed_rate, verdict) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                str(uuid.uuid4()), tenant, playbook_id, window_start, window_end,
                baseline_rate, observed_rate, verdict,
            )

    async def resolve_gaps_for_cluster(self, tenant: str, cluster_id: str) -> None:
        async with self.tx(tenant) as conn:
            await conn.execute(
                "UPDATE knowledge_gaps SET resolved = 1 WHERE resolved = 0 AND domain = $1",
                cluster_id,
            )
