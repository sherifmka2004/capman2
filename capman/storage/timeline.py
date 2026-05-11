"""
Async SQLite adapter for the raw event timeline.
Append-only for events — analysis results and session metadata are updated in place.
"""
from __future__ import annotations

import json
import dataclasses
from pathlib import Path

import aiosqlite

from capman.events import (
    Event, EventType, Session, SessionAnalysis, Triple,
    ChainOfThought, CognitiveStep, DecisionPoint,
)


class TimelineDB:
    def __init__(self, db_path: str):
        self._path = str(Path(db_path).expanduser())
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")

    async def migrate(self) -> None:
        if self._db is None:
            await self.connect()

        schema_sql = (Path(__file__).parent / "schema.sql").read_text()
        await self._db.executescript(schema_sql)

        # Check if already versioned
        async with self._db.execute("SELECT COUNT(*) FROM schema_version") as cur:
            row = await cur.fetchone()
            if row[0] == 0:
                await self._db.execute("INSERT INTO schema_version VALUES (1)")

        await self._db.commit()

    async def insert_event(self, event: Event) -> None:
        await self._db.execute(
            """INSERT OR IGNORE INTO events
               (id, type, ts, app, window_title, payload, sensor_id, session_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.id,
                event.type.value,
                event.ts,
                event.app,
                event.window_title,
                json.dumps(event.payload),
                event.sensor_id,
                None,
            ),
        )
        await self._db.commit()

    async def insert_events_bulk(self, events: list[Event]) -> None:
        rows = [
            (e.id, e.type.value, e.ts, e.app, e.window_title,
             json.dumps(e.payload), e.sensor_id, None)
            for e in events
        ]
        await self._db.executemany(
            """INSERT OR IGNORE INTO events
               (id, type, ts, app, window_title, payload, sensor_id, session_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        await self._db.commit()

    async def assign_session(self, event_id: str, session_id: str) -> None:
        await self._db.execute(
            "UPDATE events SET session_id = ? WHERE id = ?",
            (session_id, event_id),
        )
        await self._db.commit()

    async def assign_session_bulk(self, event_ids: list[str], session_id: str) -> None:
        await self._db.executemany(
            "UPDATE events SET session_id = ? WHERE id = ?",
            [(session_id, eid) for eid in event_ids],
        )
        await self._db.commit()

    async def upsert_session(self, session: Session) -> None:
        await self._db.execute(
            """INSERT INTO sessions
               (id, started_at, ended_at, dominant_app, primary_domain, event_count, analyzed)
               VALUES (?, ?, ?, ?, ?, ?, 0)
               ON CONFLICT(id) DO UPDATE SET
                 ended_at = excluded.ended_at,
                 dominant_app = excluded.dominant_app,
                 primary_domain = excluded.primary_domain,
                 event_count = excluded.event_count""",
            (
                session.id,
                session.started_at,
                session.ended_at,
                session.dominant_app,
                session.primary_domain,
                len(session.events),
            ),
        )
        await self._db.commit()

    async def save_analysis(self, analysis: SessionAnalysis) -> None:
        cot_json = None
        if analysis.chain_of_thought:
            cot_json = json.dumps(dataclasses.asdict(analysis.chain_of_thought))

        triples_json = json.dumps([dataclasses.asdict(t) for t in analysis.triples])

        await self._db.execute(
            """INSERT INTO session_analyses
               (session_id, problem_statement, approach_description, methodology_tags,
                knowledge_applied, knowledge_acquired, chain_of_thought, triples,
                confidence, model_used, analyzed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                 problem_statement = excluded.problem_statement,
                 approach_description = excluded.approach_description,
                 methodology_tags = excluded.methodology_tags,
                 knowledge_applied = excluded.knowledge_applied,
                 knowledge_acquired = excluded.knowledge_acquired,
                 chain_of_thought = excluded.chain_of_thought,
                 triples = excluded.triples,
                 confidence = excluded.confidence,
                 model_used = excluded.model_used,
                 analyzed_at = excluded.analyzed_at""",
            (
                analysis.session_id,
                analysis.problem_statement,
                analysis.approach_description,
                json.dumps(analysis.methodology_tags),
                json.dumps(analysis.knowledge_applied),
                json.dumps(analysis.knowledge_acquired),
                cot_json,
                triples_json,
                analysis.confidence,
                analysis.model_used,
                analysis.analyzed_at,
            ),
        )
        await self._db.execute(
            "UPDATE sessions SET analyzed = 1 WHERE id = ?",
            (analysis.session_id,),
        )
        await self._db.commit()

    async def save_triple(self, triple: Triple) -> None:
        await self._db.execute(
            """INSERT INTO knowledge_triples
               (id, subject, predicate, object, confidence, observed_count,
                first_seen, last_observed, source_session)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 observed_count = observed_count + 1,
                 last_observed = excluded.last_observed""",
            (
                triple.id,
                triple.subject,
                triple.predicate,
                triple.object,
                triple.confidence,
                triple.observed_at,
                triple.observed_at,
                triple.source_session,
            ),
        )
        await self._db.commit()

    async def get_events_since(self, since_ts: float, limit: int = 10000) -> list[Event]:
        async with self._db.execute(
            "SELECT * FROM events WHERE ts >= ? ORDER BY ts LIMIT ?",
            (since_ts, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_event(r) for r in rows]

    async def get_session_events(self, session_id: str) -> list[Event]:
        async with self._db.execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY ts",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_event(r) for r in rows]

    async def get_unanalyzed_sessions(self) -> list[dict]:
        async with self._db.execute(
            """SELECT id, started_at, ended_at, dominant_app, primary_domain, event_count
               FROM sessions WHERE analyzed = 0 AND ended_at IS NOT NULL
               ORDER BY started_at""",
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_event_count(self) -> int:
        async with self._db.execute("SELECT COUNT(*) FROM events") as cur:
            row = await cur.fetchone()
        return row[0]

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @staticmethod
    def _row_to_event(row) -> Event:
        return Event(
            id=row["id"],
            type=EventType(row["type"]),
            ts=row["ts"],
            app=row["app"] or "",
            window_title=row["window_title"] or "",
            payload=json.loads(row["payload"]),
            sensor_id=row["sensor_id"] or "",
        )
