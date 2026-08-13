-- Migration 003: full-text search over captured content and events.
--
-- Until now retrieval was vector-only: a 384-dim semantic search was the only
-- way to find anything. That is structurally the wrong retriever for what this
-- tool is asked to recall — exact shell commands, absolute paths, error
-- strings, URLs. Those are lexical matches. This migration adds BM25 so
-- hybrid retrieval (see capman/storage/search.py) can fuse both.
--
-- Full page/document text also lived ONLY in the vector store; SQLite kept a
-- 300-char excerpt. The `documents` table makes SQLite the owner of the text,
-- which is what lets the vector store be swapped out without a data migration.

-- ---------------------------------------------------------------------------
-- Content table: one row per searchable unit of text.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    id         TEXT PRIMARY KEY,   -- page:<urlhash>:<i> | doc:<hash>:<i> | ocr:<eventid> | session:<id> | playbook:<id> | node:<id>
    kind       TEXT NOT NULL,      -- page | doc | ocr | session | playbook | node
    ref_id     TEXT,               -- events.id / sessions.id / playbooks.id / node id
    session_id TEXT,
    ts         REAL NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    uri        TEXT NOT NULL DEFAULT '',  -- url or file path
    body       TEXT NOT NULL,
    -- Durable float32 vector. The ANN index (added in a later migration) is a
    -- rebuildable derivative of this column, never the source of truth.
    embedding  BLOB
);

CREATE INDEX IF NOT EXISTS idx_documents_kind_ts ON documents(kind, ts DESC);
CREATE INDEX IF NOT EXISTS idx_documents_ref     ON documents(ref_id);
CREATE INDEX IF NOT EXISTS idx_documents_session ON documents(session_id);

-- ---------------------------------------------------------------------------
-- BM25 index over document text.
--
-- The tokenizer is the highest-leverage line in this file. Default unicode61
-- shreds `git log --oneline` into git/log/oneline, `/usr/local/bin` into three
-- tokens, and `foo.bar()` into foo/bar. Keeping -_./:@ as token characters
-- preserves flags, absolute paths, dotted identifiers and emails as single
-- searchable tokens, which is exactly what this corpus is made of.
-- ---------------------------------------------------------------------------
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title, uri, body,
    content='documents',
    content_rowid='rowid',
    tokenize="unicode61 remove_diacritics 2 tokenchars '-_./:@'"
);

CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, title, uri, body)
    VALUES (new.rowid, new.title, new.uri, new.body);
END;

CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, uri, body)
    VALUES ('delete', old.rowid, old.title, old.uri, old.body);
END;

CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, uri, body)
    VALUES ('delete', old.rowid, old.title, old.uri, old.body);
    INSERT INTO documents_fts(rowid, title, uri, body)
    VALUES (new.rowid, new.title, new.uri, new.body);
END;

-- ---------------------------------------------------------------------------
-- Searchable projection of event payloads.
--
-- VIRTUAL, not STORED: ALTER TABLE forbids adding a STORED generated column,
-- and external-content FTS5 only ever does `SELECT col FROM events WHERE
-- rowid = ?`, so VIRTUAL is sufficient and costs no extra disk.
-- ---------------------------------------------------------------------------
-- URLs and paths are indexed TWICE: once intact, once with '/' and ':' turned
-- into separators. The tokenizer keeps those characters so that
-- `/etc/nginx/nginx.conf` survives as one token — but that alone makes
-- `https://github.com/foo/bar` a single token too, so searching `github.com`
-- or `nginx.conf` would find nothing. Emitting both forms means whole-URL,
-- host, and path-segment searches all match.
ALTER TABLE events ADD COLUMN search_text TEXT GENERATED ALWAYS AS (
    coalesce(window_title, '')                        || ' ' ||
    coalesce(json_extract(payload, '$.command'), '')   || ' ' ||
    -- Commands embed paths (`vim /etc/nginx/nginx.conf`), so decompose them too
    -- or the file is only findable by its full absolute path.
    replace(coalesce(json_extract(payload, '$.command'), ''), '/', ' ') || ' ' ||
    coalesce(json_extract(payload, '$.url'), '')       || ' ' ||
    replace(replace(coalesce(json_extract(payload, '$.url'), ''), '/', ' '), ':', ' ') || ' ' ||
    coalesce(json_extract(payload, '$.title'), '')     || ' ' ||
    coalesce(json_extract(payload, '$.query'), '')     || ' ' ||
    coalesce(json_extract(payload, '$.path'), '')      || ' ' ||
    replace(coalesce(json_extract(payload, '$.path'), ''), '/', ' ')      || ' ' ||
    coalesce(json_extract(payload, '$.src_path'), '')  || ' ' ||
    replace(coalesce(json_extract(payload, '$.src_path'), ''), '/', ' ')  || ' ' ||
    coalesce(json_extract(payload, '$.dest_path'), '') || ' ' ||
    replace(coalesce(json_extract(payload, '$.dest_path'), ''), '/', ' ') || ' ' ||
    coalesce(json_extract(payload, '$.label'), '')     || ' ' ||
    coalesce(json_extract(payload, '$.text'), '')      || ' ' ||
    coalesce(json_extract(payload, '$.excerpt'), '')
) VIRTUAL;

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    search_text,
    content='events',
    content_rowid='rowid',
    tokenize="unicode61 remove_diacritics 2 tokenchars '-_./:@'"
);

-- High-volume event types with no lexical content are excluded: indexing a
-- 100x100 mouse heatmap grid or a DOM mutation record is pure index bloat.
-- The insert and delete guards MUST use the same predicate, or the FTS index
-- would be told to delete rows it was never given.
CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events
WHEN new.type NOT IN ('mouse_heatmap_tick', 'mouse_scroll', 'dom_mutation')
BEGIN
    INSERT INTO events_fts(rowid, search_text) VALUES (new.rowid, new.search_text);
END;

CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events
WHEN old.type NOT IN ('mouse_heatmap_tick', 'mouse_scroll', 'dom_mutation')
BEGIN
    INSERT INTO events_fts(events_fts, rowid, search_text)
    VALUES ('delete', old.rowid, old.search_text);
END;

-- Only re-index when the searchable text actually changed. Without this guard
-- every assign_session_bulk repair would churn the whole FTS index.
CREATE TRIGGER IF NOT EXISTS events_au AFTER UPDATE ON events
WHEN old.search_text IS NOT new.search_text
     AND new.type NOT IN ('mouse_heatmap_tick', 'mouse_scroll', 'dom_mutation')
BEGIN
    INSERT INTO events_fts(events_fts, rowid, search_text)
    VALUES ('delete', old.rowid, old.search_text);
    INSERT INTO events_fts(rowid, search_text) VALUES (new.rowid, new.search_text);
END;
