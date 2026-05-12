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

-- Troubleshooting playbooks (Pass 4 — debugging sessions only)
-- The CORE differentiator: replicable problem-solving methodology
CREATE TABLE IF NOT EXISTS playbooks (
    id                 TEXT PRIMARY KEY,
    session_id         TEXT REFERENCES sessions(id),
    title              TEXT NOT NULL,
    domain             TEXT DEFAULT '',
    symptoms           TEXT DEFAULT '[]',     -- JSON array of trigger phrases
    context_signals    TEXT DEFAULT '[]',     -- JSON array of "this applies when..." cues
    diagnostic_steps   TEXT DEFAULT '[]',     -- JSON array of {action, rationale, expected_signal, tool}
    root_cause         TEXT DEFAULT '',
    fix                TEXT DEFAULT '[]',     -- JSON array of action steps
    verification       TEXT DEFAULT '[]',     -- JSON array of validation steps
    references_json    TEXT DEFAULT '[]',     -- JSON array of URLs/docs
    related_playbooks  TEXT DEFAULT '[]',
    reusability_score  REAL DEFAULT 0.0,
    created_at         REAL
);
CREATE INDEX IF NOT EXISTS idx_playbooks_domain  ON playbooks(domain);
CREATE INDEX IF NOT EXISTS idx_playbooks_created ON playbooks(created_at);

-- Knowledge gaps — concepts the user repeatedly looks up
-- Used to build a personal "expertise vs. unmastered" profile
CREATE TABLE IF NOT EXISTS knowledge_gaps (
    id              TEXT PRIMARY KEY,
    concept         TEXT NOT NULL UNIQUE,
    domain          TEXT DEFAULT '',
    lookup_count    INTEGER DEFAULT 1,
    query_examples  TEXT DEFAULT '[]',
    sessions        TEXT DEFAULT '[]',
    first_seen      REAL,
    last_seen       REAL,
    resolved        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_gaps_count   ON knowledge_gaps(lookup_count DESC);
CREATE INDEX IF NOT EXISTS idx_gaps_concept ON knowledge_gaps(concept);

-- Schema version tracking for migrations
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
