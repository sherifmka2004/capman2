"""Episode segmentation for web-behavior events.

Reuses capman.pipeline.session.SessionDetector as a library — with ONE
detector instance per visit id (sid). The shipped detector is a single-subject
state machine; sharing one instance across visitors would merge two people
into one episode. Episodes are also hard-split at sid boundaries.

Input rows (from the events table):
    {"id": str, "ts": float, "name": str, "payload": {"sid","seq","t_rel_ms","props"}}
Output episodes:
    {"id", "sid_hash", "started_at", "ended_at", "names", "events", "outcome",
     "step_reached", "n_events", "friction_flags"}
"""
from __future__ import annotations

import hashlib
import uuid

from capman.events import Event
from capman.pipeline.session import SessionDetector
from capman.web.friction import derive_friction
from capman.web.manifest import ProductManifest, eval_success_when


def sid_hash(sid: str) -> str:
    return hashlib.sha256(sid.encode()).hexdigest()[:16]


def _detector_for(manifest: ProductManifest) -> SessionDetector:
    return SessionDetector({"pipeline": {"session": {
        "idle_threshold_s": manifest.idle_threshold_s,
        "cool_period_s": manifest.cool_period_s,
        "hard_break_s": manifest.hard_break_s,
        "min_session_events": manifest.min_events,
    }}})


def _to_event(row: dict, product_key: str) -> Event:
    payload = row.get("payload") or {}
    return Event(
        type=row["name"],  # runtime string; web names are not desktop EventTypes
        app=product_key,
        window_title="",
        payload={"props": payload.get("props") or {}, "seq": payload.get("seq", 0),
                 "t_rel_ms": payload.get("t_rel_ms", 0)},
        sensor_id="web",
        id=row["id"],
        ts=row["ts"],
    )


def label_outcome(manifest: ProductManifest, events: list[dict],
                  step_reached: str, friction_flags: list[str]) -> str:
    for expr in manifest.success_when:
        if eval_success_when(expr, events):
            return "succeeded"
    if "error_shown" in friction_flags:
        return "errored"
    if not step_reached or (manifest.steps and step_reached == manifest.steps[0]):
        return "bounced"
    return f"abandoned_at_{step_reached}"


def furthest_step(manifest: ProductManifest, names: list[str]) -> str:
    best_idx = -1
    best = ""
    for name in names:
        spec = manifest.events.get(name)
        if spec is None or not spec.step:
            continue
        try:
            idx = manifest.steps.index(spec.step)
        except ValueError:
            continue
        if idx > best_idx:
            best_idx, best = idx, spec.step
    return best


def segment_events(rows: list[dict], manifest: ProductManifest, product_key: str) -> list[dict]:
    """Group raw event rows into episodes. Deterministic given the same rows."""
    by_sid: dict[str, list[dict]] = {}
    for row in rows:
        sid = (row.get("payload") or {}).get("sid", "")
        if not sid:
            continue
        by_sid.setdefault(sid, []).append(row)

    event_steps = {name: spec.step for name, spec in manifest.events.items()}
    episodes: list[dict] = []

    for sid in sorted(by_sid):
        sid_rows = sorted(by_sid[sid], key=lambda r: (r["ts"], (r.get("payload") or {}).get("seq", 0)))
        gap_s = manifest.idle_threshold_s + manifest.cool_period_s
        detector = _detector_for(manifest)
        sessions = []
        prev_ts: float | None = None
        for row in sid_rows:
            # Historical replay of check_timeouts(): the live detector only
            # cools down on insignificant events or wall-clock timeouts, and
            # every web event is significant — so idle splits must be applied
            # here, exactly where the real-time loop would have flushed.
            if prev_ts is not None and row["ts"] - prev_ts > gap_s:
                flushed = detector.flush()
                if flushed is not None:
                    sessions.append(flushed)
                detector = _detector_for(manifest)
            prev_ts = row["ts"]
            completed, _ = detector.ingest(_to_event(row, product_key))
            if completed is not None:
                sessions.append(completed)
        final = detector.flush()
        if final is not None:
            sessions.append(final)

        for session in sessions:
            if len(session.events) < manifest.min_events:
                continue
            member_rows = [r for r in sid_rows if r["id"] in {e.id for e in session.events}]
            names = [r["name"] for r in member_rows]
            simple = [
                {"name": r["name"], "props": (r.get("payload") or {}).get("props") or {},
                 "t_rel_ms": (r.get("payload") or {}).get("t_rel_ms", 0),
                 "seq": (r.get("payload") or {}).get("seq", 0)}
                for r in member_rows
            ]
            flags = derive_friction(simple, event_steps, manifest.steps)
            step_reached = furthest_step(manifest, names)
            episodes.append({
                "id": str(uuid.uuid4()),
                "sid_hash": sid_hash(sid),
                "started_at": session.started_at,
                "ended_at": member_rows[-1]["ts"],
                "names": names,
                "event_rows": member_rows,
                "events": simple,
                "outcome": label_outcome(manifest, simple, step_reached, flags),
                "step_reached": step_reached,
                "n_events": len(member_rows),
                "friction_flags": flags,
            })

    episodes.sort(key=lambda e: e["started_at"])
    return episodes
