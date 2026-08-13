"""Tiered retention for the event timeline.

The problem: `events` is append-only and nothing ever pruned it. With the full
desktop sensor set that is 10k-20k events/day — mouse heatmap ticks, scroll
bursts, DOM mutations, keystroke blocks — which is 1.5-2.5 GB/year before
screenshots, on a database that is never vacuumed.

The constraint: README promises "all raw events are immutable — re-analysis is
always possible without data loss", and that promise is worth keeping for the
events that carry meaning.

The resolution is to treat the two halves of the stream differently:

* **High-signal, keep forever** — shell commands, URLs, searches, code diffs,
  file operations, page and document text. This is the material analysis and
  recall are built from. It is also comparatively tiny.
* **High-volume, low-signal, expires** — heatmap ticks, scroll bursts, DOM
  mutations, raw clicks, keystroke blocks, window focus churn. These matter as
  *activity signal* in aggregate, not as individual rows.

Before anything is deleted its counts are rolled up into
`session_event_stats`, so "how active was I in this session" survives even
though the individual rows do not. Sessions that produced a playbook are
protected outright.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

#: Deleted in chunks so a large prune never takes a multi-second write lock
#: that would stall the sensor pipeline behind it.
DELETE_CHUNK = 5_000

#: Types with no per-row value once aggregated. 0 or missing = keep forever.
DEFAULT_TTL_DAYS: dict[str, int] = {
    "dom_mutation": 7,
    "mouse_heatmap_tick": 14,
    "mouse_scroll": 14,
    "mouse_click": 30,
    "keystroke": 30,
    "screenshot": 30,
    "window_focus": 90,
    "window_blur": 90,
    "idle_start": 90,
    "idle_end": 90,
}

#: Never pruned regardless of configuration — the analysable record.
PROTECTED_TYPES = frozenset({
    "shell_command", "url_visit", "search_query", "code_diff", "page_text",
    "doc_content", "file_open", "file_save", "file_delete", "file_rename",
    "error_detected", "ai_conversation", "session_start", "session_end",
})


def resolve_ttls(config: dict) -> dict[str, int]:
    """Merge configured TTLs over the defaults, dropping protected types."""
    cfg = config.get("storage", {}).get("retention", {})
    ttls = dict(DEFAULT_TTL_DAYS)
    ttls.update({k: int(v) for k, v in (cfg.get("ttl_days") or {}).items()})

    resolved = {}
    for etype, days in ttls.items():
        if days <= 0:
            continue
        if etype in PROTECTED_TYPES:
            logger.warning(
                "Ignoring retention for protected event type %r — high-signal "
                "events are never pruned", etype
            )
            continue
        resolved[etype] = days
    return resolved


async def rollup_session_stats(db, before_ts: float, types: list[str]) -> int:
    """Aggregate per-session counts for rows about to expire.

    Runs before any delete. Idempotent: re-running over the same window
    recomputes the same totals rather than double-counting, because the
    aggregate is taken from surviving rows and merged with what is already
    recorded via MAX/MIN.
    """
    if not types:
        return 0
    placeholders = ",".join("?" * len(types))
    await db._db.execute(
        f"""INSERT INTO session_event_stats (session_id, type, count, first_ts, last_ts)
            SELECT session_id, type, COUNT(*), MIN(ts), MAX(ts)
            FROM events
            WHERE ts < ? AND type IN ({placeholders}) AND session_id IS NOT NULL
            GROUP BY session_id, type
            ON CONFLICT(session_id, type) DO UPDATE SET
                count    = MAX(count, excluded.count),
                first_ts = MIN(COALESCE(first_ts, excluded.first_ts), excluded.first_ts),
                last_ts  = MAX(COALESCE(last_ts,  excluded.last_ts),  excluded.last_ts)""",
        (before_ts, *types),
    )
    await db._db.commit()
    return db._db.total_changes


async def _protected_session_ids(db) -> list[str]:
    """Sessions that produced a playbook — their raw events are never pruned."""
    async with db._db.execute("SELECT DISTINCT session_id FROM playbooks WHERE session_id IS NOT NULL") as cur:
        return [r[0] for r in await cur.fetchall()]


async def prune_events(
    db,
    config: dict,
    *,
    now: float | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Apply retention. Returns {event_type: rows_deleted}.

    `dry_run` reports what would be deleted without touching anything, which
    is what the storage UI shows before the user opts in.
    """
    now = now or time.time()
    ttls = resolve_ttls(config)
    if not ttls:
        return {}

    cfg = config.get("storage", {}).get("retention", {})
    protect_analyzed = bool(cfg.get("protect_analyzed_sessions", True))
    protected = await _protected_session_ids(db) if protect_analyzed else []

    if not dry_run:
        # Roll up everything in one pass before deleting anything, so a crash
        # part-way through the deletes cannot lose the aggregate.
        oldest_cutoff = now - max(ttls.values()) * 86400
        newest_cutoff = now - min(ttls.values()) * 86400
        await rollup_session_stats(db, max(oldest_cutoff, newest_cutoff), list(ttls))

    deleted: dict[str, int] = {}
    for etype, days in sorted(ttls.items()):
        cutoff = now - days * 86400
        params: list = [etype, cutoff]
        guard = ""
        if protected:
            guard = f" AND (session_id IS NULL OR session_id NOT IN ({','.join('?' * len(protected))}))"
            params.extend(protected)

        if dry_run:
            async with db._db.execute(
                f"SELECT COUNT(*) FROM events WHERE type = ? AND ts < ?{guard}", params
            ) as cur:
                n = (await cur.fetchone())[0]
            if n:
                deleted[etype] = n
            continue

        total = 0
        while True:
            cur = await db._db.execute(
                f"""DELETE FROM events WHERE rowid IN (
                        SELECT rowid FROM events
                        WHERE type = ? AND ts < ?{guard}
                        LIMIT {DELETE_CHUNK})""",
                params,
            )
            await db._db.commit()
            if not cur.rowcount:
                break
            total += cur.rowcount
            if cur.rowcount < DELETE_CHUNK:
                break

        if total:
            deleted[etype] = total
            await db._db.execute(
                "INSERT INTO retention_runs (ran_at, type, deleted, cutoff_ts) VALUES (?, ?, ?, ?)",
                (now, etype, total, cutoff),
            )
            await db._db.commit()

    if deleted and not dry_run:
        reclaimed = await reclaim_free_pages(db)
        logger.info("Retention pruned %d events (%s), reclaimed %.1f MB",
                    sum(deleted.values()), deleted, reclaimed / 1e6)

    return deleted


async def reclaim_free_pages(db, max_seconds: float = 10.0) -> int:
    """Return freed pages to the OS. Returns bytes reclaimed.

    Deleting rows only moves pages onto the freelist — the file does not
    shrink. A single `incremental_vacuum(1000)` reclaims 1000 pages (~4 MB),
    which after a large prune leaves almost everything still on disk and makes
    retention look like it did nothing.

    So drain the freelist in slices, bounded by a wall-clock budget rather than
    a fixed page count: a full VACUUM would hold a write lock for seconds and
    stall the sensor pipeline, while stopping early just leaves the remainder
    for the next pass.
    """
    async with db._db.execute("PRAGMA page_size") as cur:
        page_size = (await cur.fetchone())[0]
    async with db._db.execute("PRAGMA freelist_count") as cur:
        initial = (await cur.fetchone())[0]
    if not initial:
        return 0

    deadline = time.monotonic() + max_seconds
    previous = initial
    try:
        while time.monotonic() < deadline:
            await db._db.execute("PRAGMA incremental_vacuum(2000)")
            await db._db.commit()
            async with db._db.execute("PRAGMA freelist_count") as cur:
                remaining = (await cur.fetchone())[0]
            # Stop when drained, or when a slice made no progress — which is
            # what happens if auto_vacuum is not INCREMENTAL on this database.
            if remaining == 0 or remaining >= previous:
                break
            previous = remaining
    except Exception as e:
        logger.warning("Incremental vacuum failed: %s", e)

    async with db._db.execute("PRAGMA freelist_count") as cur:
        final = (await cur.fetchone())[0]
    try:
        await db._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        await db._db.commit()
    except Exception:
        pass
    return max(0, initial - final) * page_size


async def estimate_growth(db) -> dict:
    """Bytes/day and a projection, for the storage UI."""
    async with db._db.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM events") as cur:
        lo, hi, count = await cur.fetchone()
    if not lo or not hi or hi <= lo:
        return {"events": count or 0, "events_per_day": 0.0, "span_days": 0.0}

    span_days = (hi - lo) / 86400.0
    async with db._db.execute("PRAGMA page_count") as cur:
        pages = (await cur.fetchone())[0]
    async with db._db.execute("PRAGMA page_size") as cur:
        page_size = (await cur.fetchone())[0]
    total_bytes = pages * page_size

    return {
        "events": count,
        "span_days": round(span_days, 2),
        "events_per_day": round(count / span_days, 1) if span_days else 0.0,
        "bytes": total_bytes,
        "bytes_per_day": round(total_bytes / span_days) if span_days else 0,
        "projected_bytes_per_year": round(total_bytes / span_days * 365) if span_days else 0,
    }
