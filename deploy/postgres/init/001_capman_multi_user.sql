-- capman2 multi-user init — runs once on first Postgres boot.
-- Idempotent: safe to re-run for role/config drift.

-- ── Base roles (least privilege) ───────────────────────────────────────────
-- app_readwrite: per-user data access. Every capman user is a MEMBER of this
-- role. Row-Level Security is enabled on the tenant tables and policed by the
-- session variable capman.user_id, so a member only ever sees their own rows.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'capman_app') THEN
    CREATE ROLE capman_app NOLOGIN;
  END IF;
END
$$;

-- ── Schema ownership ───────────────────────────────────────────────────────
-- Everything is owned by the admin (capman_admin / POSTGRES_USER at init).
-- We keep table-creation here so the app role never needs DDL.

-- ── Tables (capman schema shape) ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,      -- tenant key used by RLS
    started_at     REAL NOT NULL,
    ended_at       REAL,
    dominant_app   TEXT DEFAULT '',
    primary_domain TEXT DEFAULT '',
    event_count    INTEGER DEFAULT 0,
    analyzed       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_started
    ON sessions(user_id, started_at DESC);

CREATE TABLE IF NOT EXISTS events (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    session_id   TEXT,
    type         TEXT NOT NULL,
    ts           REAL NOT NULL,
    app          TEXT DEFAULT '',
    window_title TEXT DEFAULT '',
    payload      TEXT NOT NULL DEFAULT '{}',
    sensor_id    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_user_ts      ON events(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_user_session ON events(user_id, session_id);
CREATE INDEX IF NOT EXISTS idx_events_user_type    ON events(user_id, type, ts);
-- FK after both exist
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_constraint WHERE conname='events_session_fk') THEN
    ALTER TABLE events ADD CONSTRAINT events_session_fk
      FOREIGN KEY (session_id) REFERENCES sessions(id);
  END IF;
END
$$;

CREATE TABLE IF NOT EXISTS session_analyses (
    session_id           TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL,
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

CREATE TABLE IF NOT EXISTS knowledge_triples (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    subject        TEXT NOT NULL,
    predicate      TEXT NOT NULL,
    object         TEXT NOT NULL,
    confidence     REAL DEFAULT 1.0,
    observed_count INTEGER DEFAULT 1,
    first_seen     REAL,
    last_observed  REAL,
    source_session TEXT
);
CREATE INDEX IF NOT EXISTS idx_triples_user ON knowledge_triples(user_id, subject);

CREATE TABLE IF NOT EXISTS screenshots (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    event_id   TEXT,
    path       TEXT NOT NULL,
    ts         REAL NOT NULL,
    ocr_text   TEXT,
    session_id TEXT
);

CREATE TABLE IF NOT EXISTS playbooks (
    id                 TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL,
    session_id         TEXT,
    title              TEXT NOT NULL,
    domain             TEXT DEFAULT '',
    symptoms           TEXT DEFAULT '[]',
    context_signals    TEXT DEFAULT '[]',
    diagnostic_steps   TEXT DEFAULT '[]',
    root_cause         TEXT DEFAULT '',
    fix                TEXT DEFAULT '[]',
    verification       TEXT DEFAULT '[]',
    references_json    TEXT DEFAULT '[]',
    related_playbooks  TEXT DEFAULT '[]',
    reusability_score  REAL DEFAULT 0.0,
    created_at         REAL
);

CREATE TABLE IF NOT EXISTS knowledge_gaps (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    concept         TEXT NOT NULL,
    domain          TEXT DEFAULT '',
    lookup_count    INTEGER DEFAULT 1,
    query_examples  TEXT DEFAULT '[]',
    sessions        TEXT DEFAULT '[]',
    first_seen      REAL,
    last_seen       REAL,
    resolved        INTEGER DEFAULT 0,
    UNIQUE(user_id, concept)
);

-- ── Row-Level Security ─────────────────────────────────────────────────────
-- ENABLE activates filtering; FORCE additionally subjects even the table owner
-- to the policy. FORCE alone does NOT enable RLS — both are required. Without
-- ENABLE, every user sees every row and cross-tenant writes are allowed.
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'sessions','events','session_analyses','knowledge_triples',
    'screenshots','playbooks','knowledge_gaps'
  ]
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE  ROW LEVEL SECURITY', t);
  END LOOP;
END
$$;

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'sessions','events','session_analyses','knowledge_triples',
    'screenshots','playbooks','knowledge_gaps'
  ]
  LOOP
    -- Drop old policies idempotently, then create tenant-by-id filtering.
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I
         USING  (user_id = current_setting(''capman.user_id'', true))
         WITH CHECK (user_id = current_setting(''capman.user_id'', true))',
      t);
  END LOOP;
END
$$;

-- ── Grant policy to the app role ───────────────────────────────────────────
GRANT USAGE ON SCHEMA public          TO capman_app;
GRANT SELECT, INSERT, UPDATE, DELETE
      ON sessions, events, session_analyses,
         knowledge_triples, screenshots, playbooks, knowledge_gaps
      TO capman_app;