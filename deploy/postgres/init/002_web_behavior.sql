-- capman2 web-behavior init — runs after 001 on first Postgres boot.
-- Idempotent: safe to re-run for drift (also applies to existing volumes via
-- psql -f, since docker-entrypoint-initdb.d only runs on first boot).
--
-- Generic, product-independent tables for the web-behavior pipeline.
-- Tenants are products: user_id = 'product:<key>' (see bootstrap.sh add-product).

CREATE TABLE IF NOT EXISTS web_episodes (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,      -- tenant key used by RLS
    sid_hash         TEXT,               -- hashed per-visit id; never the raw sid
    started_at       REAL NOT NULL,
    ended_at         REAL,
    signature        TEXT,               -- exact run-length-encoded event sequence hash
    coarse_signature TEXT,               -- first3+last3 hash for fuzzy clustering
    outcome          TEXT DEFAULT '',    -- succeeded | abandoned_at_<step> | errored | bounced
    step_reached     TEXT DEFAULT '',
    n_events         INTEGER DEFAULT 0,
    friction_flags   TEXT DEFAULT '[]',  -- JSON array of derived flags
    cluster_id       TEXT,
    analyzed         INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_web_episodes_user_signature ON web_episodes(user_id, signature);
CREATE INDEX IF NOT EXISTS idx_web_episodes_user_started   ON web_episodes(user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_web_episodes_user_analyzed  ON web_episodes(user_id, analyzed);

CREATE TABLE IF NOT EXISTS playbook_verifications (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    playbook_id    TEXT NOT NULL,
    window_start   REAL,
    window_end     REAL,
    baseline_rate  REAL,
    observed_rate  REAL,
    verdict        TEXT DEFAULT ''       -- improved | unchanged | regressed | insufficient_volume
);
CREATE INDEX IF NOT EXISTS idx_playbook_verifications_user_playbook
    ON playbook_verifications(user_id, playbook_id);

-- ── Row-Level Security ─────────────────────────────────────────────────────
-- Same boundary as 001: ENABLE activates filtering, FORCE subjects even the
-- table owner to the policy. Both are required.
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['web_episodes','playbook_verifications']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE  ROW LEVEL SECURITY', t);
  END LOOP;
END
$$;

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['web_episodes','playbook_verifications']
  LOOP
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
GRANT USAGE ON SCHEMA public TO capman_app;
GRANT SELECT, INSERT, UPDATE, DELETE
      ON web_episodes, playbook_verifications
      TO capman_app;
