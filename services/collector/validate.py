"""Batch validation for the web-behavior collector — the privacy boundary.

Nothing reaches the database unless it passed through validate_batch():
event names must be declared in the product manifest (or be reserved friction
events), prop keys must be declared, string values must satisfy the
short_string allowlist and 64-char cap, numerics must respect declared bounds.
Free text is physically unable to enter the store.

Validation is all-or-nothing: one violating event rejects the whole batch
with nothing written.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from capman.web.manifest import ManifestError, ProductManifest, validate_event

MAX_BATCH_EVENTS = 50
MAX_BODY_BYTES = 64 * 1024
SID_RE = re.compile(r"^[0-9a-fA-F\-]{8,64}$")


class BatchError(ValueError):
    """A batch (or one event in it) violates the contract. Carries an error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class CollectedEvent:
    sid: str
    name: str
    props: dict[str, Any]
    seq: int
    t_rel_ms: int
    ts: float


def _require_int(value: Any, field_name: str, event_idx: int, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BatchError("invalid_event", f"events[{event_idx}]: '{field_name}' must be an int")
    if value < minimum:
        raise BatchError("invalid_event", f"events[{event_idx}]: '{field_name}' must be >= {minimum}")
    return value


def validate_batch(manifest: ProductManifest, body: Any, recv_ts: float | None = None) -> list[CollectedEvent]:
    """Validate one beacon payload; return normalized events with reconstructed timestamps.

    Body shape: {"sid": str, "events": [{"seq": int, "t_rel_ms": int, "name": str, "props": dict}]}.
    Absolute ts is reconstructed as recv_ts - (max_t_rel_ms - t_rel_ms)/1000 so
    intra-batch timing (and therefore idle detection) survives batching.
    """
    if recv_ts is None:
        recv_ts = time.time()

    if not isinstance(body, dict):
        raise BatchError("invalid_body", "body must be a JSON object")

    sid = body.get("sid")
    if not isinstance(sid, str) or not SID_RE.match(sid):
        raise BatchError("invalid_sid", "sid must be a 8-64 char hex/dash visit id")

    raw_events = body.get("events")
    if not isinstance(raw_events, list) or not raw_events:
        raise BatchError("invalid_body", "events must be a non-empty array")
    if len(raw_events) > MAX_BATCH_EVENTS:
        raise BatchError("batch_too_large", f"batch has {len(raw_events)} events (max {MAX_BATCH_EVENTS})")

    parsed: list[CollectedEvent] = []
    for i, raw in enumerate(raw_events):
        if not isinstance(raw, dict):
            raise BatchError("invalid_event", f"events[{i}] must be an object")
        name = raw.get("name")
        if not isinstance(name, str):
            raise BatchError("invalid_event", f"events[{i}]: 'name' must be a string")
        seq = _require_int(raw.get("seq"), "seq", i)
        t_rel_ms = _require_int(raw.get("t_rel_ms"), "t_rel_ms", i)
        props = raw.get("props", {})
        if not isinstance(props, dict):
            raise BatchError("invalid_event", f"events[{i}]: 'props' must be an object")
        try:
            normalized = validate_event(manifest, name, props)
        except ManifestError as exc:
            raise BatchError("schema_violation", f"events[{i}]: {exc}") from None
        parsed.append(CollectedEvent(sid=sid, name=name, props=normalized,
                                     seq=seq, t_rel_ms=t_rel_ms, ts=recv_ts))

    max_rel = max(e.t_rel_ms for e in parsed)
    for e in parsed:
        e.ts = recv_ts - (max_rel - e.t_rel_ms) / 1000.0

    return parsed
