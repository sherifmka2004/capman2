# Storage workload

What actually runs against `~/.capman/timeline.db`, why each query is shaped the
way it is, and what keeps it fast. Read this before changing the schema, adding
an index, or rewriting a query in `capman/api/routes/`.

The guard for everything here is `tests/integration/test_query_plans.py`. If you
change a query or an index, that test tells you whether you broke a plan.

## Shape of the load

capman2 is **write-heavy and read-light**. One user, one machine, no
concurrency to speak of — but a continuous trickle of appends.

| | Rate | Notes |
|---|---|---|
| Event appends | 10k–20k/day with the full desktop sensor set | Bursty: keyboard flushes every 500 ms, mouse coalesces, screenshots every 30 s |
| Session closes | tens/day | Triggers analysis, markdown writes, embeddings |
| Reads | a handful/minute at most | Only when the user opens the UI or asks a question |

A headless install (`config/headless.toml` — shell + filesystem + browser only)
runs ~300 events/day, roughly 50× lighter. Don't size anything off a headless
sample; the desktop default is the real workload.

**Consequence:** optimise writes first. Read latency has enormous headroom;
write amplification does not. Every index on `events` is paid on every append.

## Write path

Events do **not** write through. `PipelineRunner._process_event` calls
`TimelineDB.queue_event()`, which buffers and flushes on whichever comes first:

- `FLUSH_THRESHOLD` = 200 buffered events
- `FLUSH_INTERVAL_S` = 2 s
- an explicit `flush()` — at session close, on drain, on shutdown, and before
  any read accessor that must not see stale state

Session membership is resolved *before* the insert (`_detector.ingest()` runs
first), so a row is written once already carrying its `session_id`.
`assign_session_bulk` is now a repair path only, guarded by
`WHERE session_id IS NULL` so it can never clobber a real assignment.

> Why this matters: the original code committed once per event *and* rewrote
> every row of a session afterwards to set `session_id`. Measured over 2,000
> events that cost 296 ms and 4 MB of WAL churn; the current path costs 10.5 ms
> and 1.7 MB. The 4 MB figure matched the stranded WAL found on a real install.

### Pragmas (`TimelineDB.connect`)

| Pragma | Value | Why |
|---|---|---|
| `journal_mode` | WAL | Concurrent readers while writing |
| `synchronous` | NORMAL | Durable across app crashes; only risks the last transaction on power loss. The right trade for passive telemetry, worth ~10–50× on commit-heavy work |
| `busy_timeout` | 5000 | `capman storage` and `reindex` open their own connections; without this they raise `database is locked` instead of waiting |
| `foreign_keys` | ON | `session_analyses` / `playbooks` reference `sessions` |
| `temp_store` | MEMORY | Sorts and GROUP BY spills stay off disk |
| `cache_size` | −32000 (32 MB) | |
| `mmap_size` | 256 MB | |

`close()` runs `wal_checkpoint(TRUNCATE)` and `optimize`. Skipping the
checkpoint is how a WAL ends up larger than the database it belongs to.

## Read path — query by query

### Relational, by recency
| Caller | Query | Plan |
|---|---|---|
| `routes/sessions.py` | `sessions ORDER BY started_at DESC LIMIT/OFFSET` | `SCAN sessions USING INDEX idx_sessions_started` |
| `routes/chat.py`, `routes/brain.py` | `sessions JOIN session_analyses ORDER BY started_at DESC` | same index drives the join's outer loop |
| `routes/chat.py` | `events WHERE type = ? ORDER BY ts DESC LIMIT ?` | `SEARCH events USING INDEX idx_events_type` |
| `routes/sessions.py` | `events WHERE session_id = ? AND type IN (...)` | `idx_events_session_type`, then a sort |

`SCAN t USING INDEX i` is not a table scan — it is an ordered index walk that
satisfies `ORDER BY` for free. The thing to avoid is `USE TEMP B-TREE`.

### Aggregations
| Caller | Query | Plan |
|---|---|---|
| `routes/chat.py` | 24h `GROUP BY type` | skip-scan of covering `idx_events_type` as `ANY(type) AND ts>?` |
| `routes/chat.py` | per-day `MIN/MAX(ts)` | `idx_events_ts`, then sort for the computed GROUP BY |
| `routes/storage.py` | `COUNT(*)` × 7 tables, `PRAGMA page_count` | separate read-only connection |

The per-day span query is the slowest in the system (~57 ms over 20k rows)
because `GROUP BY date(ts,'unixepoch','localtime')` groups on a computed
expression and no index can satisfy it. It runs once per chat request, so this
is accepted. If it ever matters, add a stored generated `local_day` column and
index that — do not reintroduce the per-day subquery loop.

> That loop is what this replaced: seven separate `MIN/MAX` queries, one per
> day, with bounds computed via `time.mktime(time.strptime(day, "%Y-%m-%d"))`.
> `strptime` leaves `tm_isdst = -1`, so across a DST transition the day
> boundaries shifted an hour and idle time landed on the wrong day.

### Two sorts we accept
1. `WHERE session_id = ? AND type IN (...) ORDER BY ts` — an `IN` list makes the
   planner merge several index ranges, so the sort is unavoidable.
2. `GROUP BY date(...)` — computed expression, as above.

Both are marked `sort_ok=True` in the plan guard.

## Indexes, and one that was rejected

Every index in `migrations/002_indexes.sql` was chosen from measured
`EXPLAIN QUERY PLAN` output, not from guessing.

`idx_triples_spo` is UNIQUE, which does double duty: it serves the upsert
lookup *and* lets `upsert_triple` be a single `ON CONFLICT DO UPDATE`
statement instead of a read-then-write that could race and produce duplicates.
The migration folds any pre-existing duplicates together (summing
`observed_count`, keeping the strongest confidence and widest time span) before
building the index, so it applies cleanly to databases that already raced.

**Rejected: `events(ts, type)`.** It looks obviously right for the 24h rollup.
Measured over 20k rows with `ANALYZE`, the planner never chose it — it prefers
skip-scanning the covering `idx_events_type`, and the per-day query prefers
`idx_events_ts`. Dropping it left every plan byte-identical and was marginally
faster. On a table taking 20k appends a day, an unused index is pure cost.
`test_no_unused_indexes_on_events` exists to stop it coming back.

## Rules of thumb

1. **Measure before adding an index.** `EXPLAIN QUERY PLAN`, on a populated
   database, after `ANALYZE`. An empty table tells you nothing.
2. **Adding an index to `events` needs justification.** It is the hot path.
3. **Never commit per row.** Use `queue_event`, or batch with `executemany` and
   one commit.
4. **Reads that must be fresh call `flush()` first.** The buffer is not visible
   to raw `db._db.execute` callers.
5. **New queries get a case in `test_query_plans.py`.** That is the only thing
   standing between a refactor and a silent full scan.
6. **Schema changes go in a migration**, never by editing an old one — see
   `capman/storage/migrations/`.
