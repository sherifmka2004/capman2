"""Validation for POST /identify — sid<->email linking.

Deliberately separate from validate.py: that module is the event pipeline's
privacy boundary (manifest-declared, no-free-text). This one is the opposite
by design — it accepts exactly one free-text field (email) because that is
its entire purpose — but it writes to a dedicated side table
(web_visitor_identity), never into events/web_episodes, so the event
pipeline's no-PII guarantee holds regardless of whether a product ever
calls this endpoint.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass

SID_RE = re.compile(r"^[0-9a-fA-F\-]{8,64}$")
# Deliberately simple: a syntactic sanity check, not full RFC 5322. Good
# enough to reject garbage without becoming its own maintenance burden.
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,24}$")
MAX_EMAIL_LEN = 254  # RFC 5321 practical limit


class IdentityError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Identity:
    sid_hash: str
    email: str
    ts: float


def sid_hash(sid: str) -> str:
    """Must match services/analyst/segment.py's sid_hash() exactly — both
    sides hash the same raw sid so web_visitor_identity joins to
    web_episodes on sid_hash."""
    return hashlib.sha256(sid.encode()).hexdigest()[:16]


def validate_identify(body: object, recv_ts: float | None = None) -> Identity:
    """Body shape: {"sid": str, "email": str}."""
    if recv_ts is None:
        recv_ts = time.time()

    if not isinstance(body, dict):
        raise IdentityError("invalid_body", "body must be a JSON object")

    sid = body.get("sid")
    if not isinstance(sid, str) or not SID_RE.match(sid):
        raise IdentityError("invalid_sid", "sid must be a 8-64 char hex/dash visit id")

    email = body.get("email")
    if not isinstance(email, str) or len(email) > MAX_EMAIL_LEN or not EMAIL_RE.match(email):
        raise IdentityError("invalid_email", "email must be a syntactically valid address")

    return Identity(sid_hash=sid_hash(sid), email=email.strip().lower(), ts=recv_ts)
