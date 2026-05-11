-- Raw immutable event timeline. Append-only.
CREATE TABLE IF NOT EXISTS events (
    id           TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    ts           REAL NOT NULL,
    app          TEXT DEFAULT '',
    window_title TEXT DEFAULT '',
    payload      TEXT NOT NULL DEFAULT '{}',
    sensor_id    TEXT DEFAULT '',
    session_id   TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts      ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_type    ON events(type, ts);

-- Detected problem-solving sessions
CREATE TABLE IF NOT EXISTS sessions (
    id             TEXT PRIMARY KEY,
    started_at     REAL NOT NULL,
    ended_at       REAL,
    dominant_app   TEXT DEFAULT '',
    primary_domain TEXT DEFAULT '',
    event_count    INTEGER DEFAULT 0,
    analyzed       INTEGER DEFAULT 0  -- 0=pending, 1=done, 2=skipped
);

-- LLM analysis results per session
CREATE TABLE IF NOT EXISTS session_analyses (
    session_id           TEXT PRIMARY KEY REFERENCES sessions(id),
    problem_statement    TEXT DEFAULT '',
    approach_description TEXT DEFAULT '',
    methodology_tags     TEXT DEFAULT '[]',
    knowledge_applied    TEXT DEFAULT '[]',
    knowledge_acquired   TEXT DEFAULT '[]',
    chain_of_thought     TEXT,
    triples              TEXT DEFAULT '[]',
    confidence           REAL DEFAULT 0.0,
    model_used           TEXT DEFAULT '',
    analyzed_at          REAL
);

-- Knowledge graph triple index (markdown files are the human-readable copy)
CREATE TABLE IF NOT EXISTS knowledge_triples (
    id             TEXT PRIMARY KEY,
    subject        TEXT NOT NULL,
    predicate      TEXT NOT NULL,
    object         TEXT NOT NULL,
    confidence     REAL DEFAULT 1.0,
    observed_count INTEGER DEFAULT 1,
    first_seen     REAL,
    last_observed  REAL,
    source_session TEXT
);
CREATE INDEX IF NOT EXISTS idx_triples_subject   ON knowledge_triples(subject);
CREATE INDEX IF NOT EXISTS idx_triples_predicate ON knowledge_triples(predicate);
CREATE INDEX IF NOT EXISTS idx_triples_object    ON knowledge_triples(object);

-- Screenshot file metadata
CREATE TABLE IF NOT EXISTS screenshots (
    id         TEXT PRIMARY KEY,
    event_id   TEXT REFERENCES events(id),
    path       TEXT NOT NULL,
    ts         REAL NOT NULL,
    ocr_text   TEXT,
    session_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_screenshots_ts ON screenshots(ts);

-- Schema version tracking for migrations
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
