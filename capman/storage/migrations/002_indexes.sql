-- Migration 002: indexes for the queries the API and pipeline actually run.
--
-- Every index below was chosen from EXPLAIN QUERY PLAN against a real database,
-- not guessed. Before this migration each of these queries was a full table
-- SCAN followed by USE TEMP B-TREE FOR ORDER BY.
-- See docs/STORAGE_WORKLOAD.md for the query-by-query mapping.

-- routes/sessions.py (session list), routes/chat.py (recent sessions),
-- routes/brain.py (recent analyses) all sort sessions by recency.
CREATE INDEX IF NOT EXISTS idx_sessions_started
    ON sessions(started_at DESC);

-- The analysis queue: WHERE analyzed = 0 AND ended_at IS NOT NULL ORDER BY started_at.
-- Partial index — pending sessions are a small minority of the table.
CREATE INDEX IF NOT EXISTS idx_sessions_pending
    ON sessions(analyzed, started_at) WHERE ended_at IS NOT NULL;

-- routes/chat.py and routes/brain.py rank triples by confidence.
CREATE INDEX IF NOT EXISTS idx_triples_conf
    ON knowledge_triples(confidence DESC, last_observed DESC);

-- upsert_triple() looks up an exact (subject, predicate, object). A UNIQUE index
-- both serves that lookup and lets the read-then-write become a real
-- ON CONFLICT upsert, removing a check-then-act race.
--
-- That same race could already have produced duplicates, which would make the
-- UNIQUE index fail to build, so fold them together first: keep the earliest
-- row, sum the observation counts, take the strongest confidence and the widest
-- time span. No knowledge is lost.
UPDATE knowledge_triples AS t SET
    observed_count = (SELECT SUM(d.observed_count) FROM knowledge_triples d
                      WHERE d.subject=t.subject AND d.predicate=t.predicate AND d.object=t.object),
    confidence     = (SELECT MAX(d.confidence) FROM knowledge_triples d
                      WHERE d.subject=t.subject AND d.predicate=t.predicate AND d.object=t.object),
    first_seen     = (SELECT MIN(d.first_seen) FROM knowledge_triples d
                      WHERE d.subject=t.subject AND d.predicate=t.predicate AND d.object=t.object),
    last_observed  = (SELECT MAX(d.last_observed) FROM knowledge_triples d
                      WHERE d.subject=t.subject AND d.predicate=t.predicate AND d.object=t.object)
WHERE t.rowid IN (
    SELECT MIN(rowid) FROM knowledge_triples GROUP BY subject, predicate, object HAVING COUNT(*) > 1
);

DELETE FROM knowledge_triples WHERE rowid NOT IN (
    SELECT MIN(rowid) FROM knowledge_triples GROUP BY subject, predicate, object
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_triples_spo
    ON knowledge_triples(subject, predicate, object);

-- NOTE: an index on events(ts, type) was trialled here for the 24h activity
-- rollup and deliberately NOT kept. Measured over 20k rows with ANALYZE, the
-- planner never chose it: it skip-scans the existing covering idx_events_type
-- as `ANY(type) AND ts>?` instead, and the per-day span query prefers
-- idx_events_ts. Dropping it left every plan identical and was marginally
-- faster. An unused index is pure write-path cost, and events is the hot path.

-- routes/sessions.py detail view: WHERE session_id = ? AND type IN (...) ORDER BY ts.
CREATE INDEX IF NOT EXISTS idx_events_session_type
    ON events(session_id, type, ts);
