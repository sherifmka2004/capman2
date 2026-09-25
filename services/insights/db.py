"""Postgres access for the insights viewer — read-only queries over the
same web-behavior tenant tables the analyst worker writes to.

Reuses the analyst worker's transaction pattern: every query runs inside a
transaction that first sets capman.user_id (is_local=true), so RLS (FORCEd
on every tenant table) holds even though a single pool/role serves the
request. asyncpg is imported lazily so this module can be exercised in
tests without the dependency installed.
"""
from __future__ import annotations

import json
from typing import Any


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class InsightsDB:
    def __init__(self, dsn: str, min_pool: int = 1, max_pool: int = 3):
        self._dsn = dsn
        self._min_pool = min_pool
        self._max_pool = max_pool
        self._pool: Any = None

    async def connect(self) -> None:
        if self._pool is None:
            import asyncpg
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=self._min_pool, max_size=self._max_pool
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    class _TenantTx:
        def __init__(self, db: "InsightsDB", tenant: str):
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
            await self._conn.execute(
                "SELECT set_config('capman.user_id', $1, true)", self._tenant
            )
            return self._conn

        async def __aexit__(self, exc_type, exc, tb):
            await self._tx.commit() if exc_type is None else await self._tx.rollback()
            await self._db._pool.release(self._conn)
            return False

    def tx(self, tenant: str) -> "InsightsDB._TenantTx":
        return InsightsDB._TenantTx(self, tenant)

    async def summary(self, tenant: str) -> dict:
        async with self.tx(tenant) as conn:
            episodes = await conn.fetchval("SELECT count(*) FROM web_episodes")
            analyzed = await conn.fetchval(
                "SELECT count(*) FROM web_episodes WHERE analyzed = 1"
            )
            unassigned = await conn.fetchval(
                "SELECT count(*) FROM events WHERE session_id IS NULL"
            )
            playbooks = await conn.fetchval("SELECT count(*) FROM playbooks")
            gaps = await conn.fetchval("SELECT count(*) FROM knowledge_gaps")
            by_outcome = await conn.fetch(
                "SELECT coalesce(nullif(outcome,''),'unknown') AS outcome, count(*) AS n "
                "FROM web_episodes GROUP BY 1 ORDER BY 2 DESC"
            )
            latest = await conn.fetchval("SELECT max(started_at) FROM web_episodes")
        return {
            "episodes": episodes or 0,
            "analyzed": analyzed or 0,
            "unassigned_events": unassigned or 0,
            "playbooks": playbooks or 0,
            "gaps": gaps or 0,
            "latest_episode_at": latest,
            "by_outcome": [{"outcome": r["outcome"], "count": r["n"]} for r in by_outcome],
        }

    async def episodes(self, tenant: str, limit: int = 100) -> list[dict]:
        async with self.tx(tenant) as conn:
            rows = await conn.fetch(
                """
                SELECT e.id, e.started_at, e.ended_at, e.outcome, e.step_reached,
                       e.n_events, e.friction_flags, e.analyzed,
                       sa.problem_statement, sa.approach_description,
                       sa.confidence, sa.analyzed_at, sa.methodology_tags
                FROM web_episodes e
                LEFT JOIN session_analyses sa ON sa.session_id = e.id
                ORDER BY e.started_at DESC
                LIMIT $1
                """,
                limit,
            )
        out = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "started_at": r["started_at"],
                    "ended_at": r["ended_at"],
                    "outcome": r["outcome"] or "",
                    "step_reached": r["step_reached"] or "",
                    "n_events": r["n_events"] or 0,
                    "friction_flags": _loads(r["friction_flags"], []),
                    "analyzed": bool(r["analyzed"]),
                    "problem_statement": r["problem_statement"] or "",
                    "approach_description": r["approach_description"] or "",
                    "confidence": r["confidence"] or 0.0,
                    "analyzed_at": r["analyzed_at"],
                    "methodology_tags": _loads(r["methodology_tags"], []),
                }
            )
        return out

    async def playbooks(self, tenant: str, limit: int = 100) -> list[dict]:
        async with self.tx(tenant) as conn:
            total = await conn.fetchval("SELECT count(*) FROM playbooks")
            rows = await conn.fetch(
                "SELECT id, title, domain, symptoms, context_signals, diagnostic_steps, "
                "root_cause, fix, verification, references_json, reusability_score, created_at "
                "FROM playbooks ORDER BY created_at DESC LIMIT $1",
                limit,
            )
        out = []
        for r in rows:
            steps = _loads(r["diagnostic_steps"], [])
            out.append(
                {
                    "id": r["id"],
                    "title": r["title"],
                    "domain": r["domain"] or "",
                    "symptoms": _loads(r["symptoms"], []),
                    "context_signals": _loads(r["context_signals"], []),
                    "diagnostic_steps": steps,
                    "diagnostic_step_count": len(steps),
                    "root_cause": r["root_cause"] or "",
                    "fix": _loads(r["fix"], []),
                    "verification": _loads(r["verification"], []),
                    "references": _loads(r["references_json"], []),
                    "reusability_score": r["reusability_score"] or 0.0,
                    "created_at": r["created_at"],
                }
            )
        return {"total": total or 0, "playbooks": out}

    async def episodes_for_email(self, tenant: str, email: str, limit: int = 200) -> list[dict]:
        """Episodes for one identified visitor — joins web_visitor_identity
        (sid_hash -> email, written only by the collector's opt-in
        POST /identify) to web_episodes (sid_hash) to session_analyses
        (episode id). Only ever finds episodes captured *after* the visitor
        was identified in that tab — there is no reliable way to retro-link
        older anonymous episodes (no stored IP, no persistent id)."""
        async with self.tx(tenant) as conn:
            rows = await conn.fetch(
                """
                SELECT e.id, e.started_at, e.ended_at, e.outcome, e.step_reached,
                       e.n_events, e.friction_flags, e.analyzed,
                       sa.problem_statement, sa.approach_description,
                       sa.confidence, sa.analyzed_at, sa.methodology_tags,
                       i.first_seen AS identity_first_seen, i.last_seen AS identity_last_seen
                FROM web_visitor_identity i
                JOIN web_episodes e ON e.sid_hash = i.sid_hash
                LEFT JOIN session_analyses sa ON sa.session_id = e.id
                WHERE i.email = $1
                ORDER BY e.started_at DESC
                LIMIT $2
                """,
                email.strip().lower(),
                limit,
            )
        out = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "started_at": r["started_at"],
                    "ended_at": r["ended_at"],
                    "outcome": r["outcome"] or "",
                    "step_reached": r["step_reached"] or "",
                    "n_events": r["n_events"] or 0,
                    "friction_flags": _loads(r["friction_flags"], []),
                    "analyzed": bool(r["analyzed"]),
                    "problem_statement": r["problem_statement"] or "",
                    "approach_description": r["approach_description"] or "",
                    "confidence": r["confidence"] or 0.0,
                    "analyzed_at": r["analyzed_at"],
                    "methodology_tags": _loads(r["methodology_tags"], []),
                    "identity_first_seen": r["identity_first_seen"],
                    "identity_last_seen": r["identity_last_seen"],
                }
            )
        return out

    async def gaps(self, tenant: str, limit: int = 100) -> list[dict]:
        async with self.tx(tenant) as conn:
            total = await conn.fetchval("SELECT count(*) FROM knowledge_gaps")
            rows = await conn.fetch(
                "SELECT id, concept, domain, lookup_count, query_examples, sessions, "
                "first_seen, last_seen, resolved FROM knowledge_gaps "
                "ORDER BY lookup_count DESC LIMIT $1",
                limit,
            )
        out = []
        for r in rows:
            sess = _loads(r["sessions"], [])
            out.append(
                {
                    "id": r["id"],
                    "concept": r["concept"],
                    "domain": r["domain"] or "",
                    "lookup_count": r["lookup_count"] or 0,
                    "examples": _loads(r["query_examples"], []),
                    "session_count": len(sess),
                    "first_seen": r["first_seen"],
                    "last_seen": r["last_seen"],
                    "resolved": bool(r["resolved"]),
                }
            )
        return {"total": total or 0, "gaps": out}
