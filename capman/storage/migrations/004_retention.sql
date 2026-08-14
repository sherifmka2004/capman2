-- Migration 004: make retention possible without losing the record.
--
-- `events` is append-only with no pruning of any kind. At the desktop sensor
-- set that is 10k-20k events/day — 1.5-2.5 GB/year before screenshots — on a
-- database that is never vacuumed. But README promises "all raw events are
-- immutable", so a blanket TTL would break a stated guarantee.
--
-- The resolution is to prune by *type*, and to roll counts up before deleting
-- so the aggregate record survives even when raw rows do not.

-- Per-session, per-type counts. Written before any prune, so the storage UI
-- and brain map keep their denominators after high-volume noise is dropped.
CREATE TABLE IF NOT EXISTS session_event_stats (
    session_id TEXT NOT NULL,
    type       TEXT NOT NULL,
    count      INTEGER NOT NULL,
    first_ts   REAL,
    last_ts    REAL,
    PRIMARY KEY (session_id, type)
);

CREATE INDEX IF NOT EXISTS idx_session_stats_type ON session_event_stats(type);

-- Audit trail: what was pruned, when, and how much came back.
CREATE TABLE IF NOT EXISTS retention_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at     REAL NOT NULL,
    type       TEXT NOT NULL,
    deleted    INTEGER NOT NULL,
    cutoff_ts  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_retention_runs_ran ON retention_runs(ran_at DESC);

-- Reclaim freed pages incrementally instead of growing forever. auto_vacuum
-- can only be changed on an empty database or through a full VACUUM, so this
-- is the one place it can be set; afterwards incremental_vacuum is cheap.
PRAGMA auto_vacuum = INCREMENTAL;
VACUUM;
