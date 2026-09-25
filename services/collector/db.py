"""Postgres writer for the collector.

One pool, many tenants: Row-Level Security is FORCEd, so isolation comes from
setting capman.user_id per transaction (set_config with is_local=true), not
from per-product roles. The configured role only needs membership in capman_app.

asyncpg is imported lazily so the validation tests run without it installed.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

_INSERT_EVENTS = """
INSERT INTO events (id, user_id, session_id, type, ts, app, window_title, payload, sensor_id)
VALUES ($1, $2, NULL, $3, $4, $5, '', $6, 'web')
"""

_UPSERT_IDENTITY = """
INSERT INTO web_visitor_identity (id, user_id, sid_hash, email, first_seen, last_seen)
VALUES ($1, $2, $3, $4, $5, $5)
ON CONFLICT (user_id, sid_hash) DO UPDATE
  SET email = EXCLUDED.email, last_seen = EXCLUDED.last_seen
"""


class CollectorDB:
    def __init__(self, dsn: str, min_pool: int = 1, max_pool: int = 5):
        self._dsn = dsn
        self._min_pool = min_pool
        self._max_pool = max_pool
        self._pool: Any = None

    async def connect(self) -> None:
        if self._pool is not None:
            return
        import asyncpg
        self._pool = await asyncpg.create_pool(
            self._dsn, min_size=self._min_pool, max_size=self._max_pool
        )
        logger.info("collector db pool ready")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def insert_events(self, tenant: str, product_key: str, events: list) -> int:
        """Insert validated CollectedEvent rows under the tenant's RLS key."""
        if self._pool is None:
            raise RuntimeError("db not connected")
        rows = [
            (
                str(uuid.uuid4()),
                tenant,
                e.name,
                e.ts,
                product_key,
                json.dumps(
                    {"sid": e.sid, "seq": e.seq, "t_rel_ms": e.t_rel_ms, "props": e.props},
                    separators=(",", ":"),
                ),
            )
            for e in events
        ]
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('capman.user_id', $1, true)", tenant)
                await conn.executemany(_INSERT_EVENTS, rows)
        return len(rows)

    async def upsert_identity(self, tenant: str, sid_hash: str, email: str, ts: float) -> None:
        """Link one visit's sid_hash to an email under the tenant's RLS key.
        Idempotent: re-identifying the same sid_hash just refreshes email/last_seen."""
        if self._pool is None:
            raise RuntimeError("db not connected")
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('capman.user_id', $1, true)", tenant)
                await conn.execute(_UPSERT_IDENTITY, str(uuid.uuid4()), tenant, sid_hash, email, ts)
