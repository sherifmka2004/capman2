-- capman2 web-identity init — runs after 001+002 on first Postgres boot.
-- Idempotent: safe to re-run for drift (also applies to existing volumes via
-- psql -f, since docker-entrypoint-initdb.d only runs on first boot).
--
-- Deliberately NOT part of 002_web_behavior.sql's event/episode tables.
-- The web-behavior pipeline (manifest, events, web_episodes) is a hard
-- privacy boundary: no free-text prop type exists, so PII cannot enter it
-- by construction (see capman/web/manifest.py). Linking a visitor's email
-- to their anonymous episodes is a deliberate, separate, opt-in capability
-- — it only ever happens when a product's own frontend calls POST /identify
-- with an authenticated user's email, and it lives in its own table so the
-- event pipeline's no-PII guarantee stays true regardless of whether any
-- product uses identify() at all.
--
-- sid_hash here must be computed the same way as web_episodes.sid_hash
-- (sha256(raw sid)[:16] — see services/analyst/segment.py's sid_hash() and
-- services/collector/identity.py's copy of it) so the two tables join.

CREATE TABLE IF NOT EXISTS web_visitor_identity (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,      -- tenant key used by RLS
    sid_hash    TEXT NOT NULL,      -- same hash as web_episodes.sid_hash
    email       TEXT NOT NULL,
    first_seen  REAL NOT NULL,
    last_seen   REAL NOT NULL,
    UNIQUE(user_id, sid_hash)
);
CREATE INDEX IF NOT EXISTS idx_web_visitor_identity_user_email
    ON web_visitor_identity(user_id, email);

-- ── Row-Level Security ─────────────────────────────────────────────────────
-- Same boundary as 001/002: ENABLE activates filtering, FORCE subjects even
-- the table owner to the policy. Both are required.
ALTER TABLE web_visitor_identity ENABLE ROW LEVEL SECURITY;
ALTER TABLE web_visitor_identity FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON web_visitor_identity;
CREATE POLICY tenant_isolation ON web_visitor_identity
    USING  (user_id = current_setting('capman.user_id', true))
    WITH CHECK (user_id = current_setting('capman.user_id', true));

-- ── Grant policy to the app role ───────────────────────────────────────────
GRANT USAGE ON SCHEMA public TO capman_app;
GRANT SELECT, INSERT, UPDATE ON web_visitor_identity TO capman_app;
