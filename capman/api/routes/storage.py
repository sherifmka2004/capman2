"""GET /storage — disk-usage breakdown for everything capman2 keeps on disk."""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/storage", tags=["storage"])


# ---------------------------------------------------------------------------

def _human(n: int | float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} TB"


def _path_size(p: Path) -> tuple[int, int]:
    """Return (total_bytes, file_count) for a file or directory tree."""
    if not p.exists():
        return 0, 0
    if p.is_file():
        try:
            return p.stat().st_size, 1
        except OSError:
            return 0, 0
    total = 0
    n = 0
    for root, _dirs, files in os.walk(p, onerror=lambda e: None):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
                n += 1
            except OSError:
                pass
    return total, n


def _expand(cfg_path: str, default: str) -> Path:
    return Path(str(cfg_path or default)).expanduser()


def compute_storage(config: dict) -> dict:
    core = config.get("core", {})
    storage = config.get("storage", {})
    sensors = config.get("sensors", {})

    data_dir = _expand(core.get("data_dir"), "~/.capman")
    db_path = _expand(storage.get("sqlite_path"), "~/.capman/timeline.db")
    chroma_path = _expand(storage.get("chroma_path"), "~/.capman/chroma")
    knowledge_dir = _expand(storage.get("knowledge_dir"), "~/.capman/knowledge")
    screenshots_dir = _expand(sensors.get("screenshot", {}).get("save_dir"), "~/.capman/screenshots")
    snapshot_dir = _expand(sensors.get("filesystem", {}).get("snapshot_dir"), "~/.capman/file_snapshots")
    tls_dir = data_dir / "tls"

    total_bytes, total_files = _path_size(data_dir)

    # Timeline DB = the .db plus its WAL/SHM sidecars
    db_bytes = 0
    db_files = 0
    for suffix in ("", "-wal", "-shm", "-journal"):
        b, n = _path_size(Path(str(db_path) + suffix))
        db_bytes += b
        db_files += n

    chroma_bytes, chroma_files = _path_size(chroma_path)
    knowledge_bytes, knowledge_files = _path_size(knowledge_dir)
    screenshots_bytes, screenshots_files = _path_size(screenshots_dir)
    snapshot_bytes, snapshot_files = _path_size(snapshot_dir)
    tls_bytes, tls_files = _path_size(tls_dir)

    # Logs (anything ending in .log directly under the data dir)
    log_bytes = 0
    log_files = 0
    if data_dir.is_dir():
        for f in data_dir.glob("*.log"):
            b, n = _path_size(f)
            log_bytes += b
            log_files += n

    known = db_bytes + chroma_bytes + knowledge_bytes + screenshots_bytes + snapshot_bytes + tls_bytes + log_bytes
    other_bytes = max(total_bytes - known, 0)

    components = [
        {"name": "Timeline DB (SQLite)", "path": str(db_path), "bytes": db_bytes, "files": db_files,
         "human": _human(db_bytes)},
        {"name": "Vector store (legacy ChromaDB — removable)", "path": str(chroma_path),
         "bytes": chroma_bytes, "files": chroma_files, "human": _human(chroma_bytes)},
        {"name": "Screenshots", "path": str(screenshots_dir), "bytes": screenshots_bytes, "files": screenshots_files,
         "human": _human(screenshots_bytes)},
        {"name": "Knowledge graph (markdown)", "path": str(knowledge_dir), "bytes": knowledge_bytes, "files": knowledge_files,
         "human": _human(knowledge_bytes)},
        {"name": "File snapshots (for diffs)", "path": str(snapshot_dir), "bytes": snapshot_bytes, "files": snapshot_files,
         "human": _human(snapshot_bytes)},
        {"name": "TLS certificate", "path": str(tls_dir), "bytes": tls_bytes, "files": tls_files, "human": _human(tls_bytes)},
        {"name": "Logs", "path": str(data_dir), "bytes": log_bytes, "files": log_files, "human": _human(log_bytes)},
        {"name": "Other", "path": str(data_dir), "bytes": other_bytes, "files": max(total_files - (
            db_files + chroma_files + knowledge_files + screenshots_files + snapshot_files + tls_files + log_files), 0),
         "human": _human(other_bytes)},
    ]
    for c in components:
        c["pct"] = round(100.0 * c["bytes"] / total_bytes, 1) if total_bytes else 0.0
    components.sort(key=lambda c: c["bytes"], reverse=True)

    # ---- DB-internal stats ------------------------------------------------
    db_stats: dict = {}
    event_types: list[dict] = []
    span_days = 0.0
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        # Wait for the writer instead of raising `database is locked`.
        conn.execute("PRAGMA busy_timeout=5000")
        for tbl in ("events", "sessions", "session_analyses", "knowledge_triples",
                    "screenshots", "playbooks", "knowledge_gaps"):
            try:
                db_stats[tbl] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            except sqlite3.Error:
                pass
        try:
            for r in conn.execute("SELECT type, COUNT(*) c FROM events GROUP BY type ORDER BY c DESC"):
                event_types.append({"type": r[0], "count": r[1]})
        except sqlite3.Error:
            pass
        try:
            row = conn.execute("SELECT MIN(ts), MAX(ts) FROM events").fetchone()
            if row and row[0] and row[1]:
                span_days = max((row[1] - row[0]) / 86400.0, 0.0)
                db_stats["oldest_event"] = row[0]
                db_stats["newest_event"] = row[1]
        except sqlite3.Error:
            pass
        try:
            pc = conn.execute("PRAGMA page_count").fetchone()[0]
            ps = conn.execute("PRAGMA page_size").fetchone()[0]
            db_stats["sqlite_internal_bytes"] = int(pc) * int(ps)
        except sqlite3.Error:
            pass
        conn.close()
    except sqlite3.Error:
        pass

    est_per_day = (total_bytes / span_days) if span_days >= 0.5 else None

    return {
        "data_dir": str(data_dir),
        "total_bytes": total_bytes,
        "total_human": _human(total_bytes),
        "total_files": total_files,
        "components": components,
        "db": db_stats,
        "event_types": event_types,
        "span_days": round(span_days, 2),
        "estimated_bytes_per_day": int(est_per_day) if est_per_day else None,
        "estimated_per_day_human": _human(est_per_day) if est_per_day else None,
        "estimated_per_month_human": _human(est_per_day * 30) if est_per_day else None,
        "generated_at": time.time(),
    }


@router.get("")
async def get_storage(request: Request):
    config = request.app.state.config or {}
    return compute_storage(config)


@router.get("/retention")
async def get_retention(request: Request):
    """Current retention policy, what it would prune right now, and growth rate.

    Always a dry run — nothing is deleted by looking at this.
    """
    config = request.app.state.config or {}
    db = request.app.state.db
    from capman.storage.retention import estimate_growth, prune_events, resolve_ttls

    policy = resolve_ttls(config)
    cfg = config.get("storage", {}).get("retention", {})
    out: dict = {
        "enabled": bool(cfg.get("enabled", True)),
        "protect_analyzed_sessions": bool(cfg.get("protect_analyzed_sessions", True)),
        "ttl_days": policy,
        "check_interval_hours": int(cfg.get("check_interval_hours", 24)),
    }
    if db is None:
        return out

    try:
        out["growth"] = await estimate_growth(db)
        pending = await prune_events(db, config, dry_run=True)
        out["would_prune"] = pending
        out["would_prune_total"] = sum(pending.values())
    except Exception as e:
        logger.warning("Retention preview failed: %s", e)
        out["error"] = str(e)

    try:
        async with db._db.execute(
            "SELECT ran_at, type, deleted FROM retention_runs ORDER BY ran_at DESC LIMIT 20"
        ) as cur:
            out["recent_runs"] = [dict(r) for r in await cur.fetchall()]
    except Exception:
        out["recent_runs"] = []

    return out
