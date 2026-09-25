"""Web-behavior collector — a write-only ingestion endpoint.

Deliberately NOT capman.api.server: that app has no authentication and exposes
reads/controls. This service exposes exactly three routes (POST /collect,
POST /identify, GET /health), authenticates writes with a per-product shared
secret, and can only ever INSERT under the selected tenant's RLS key.

/collect and /identify are two different trust boundaries wearing the same
auth: /collect can only ever write manifest-validated, no-free-text events
(see capman/web/manifest.py) — PII cannot enter it by construction. /identify
is the one deliberate exception: it accepts an email and links it to a visit
id, but only into the separate web_visitor_identity table (see
deploy/postgres/init/003_web_identity.sql), never into events/web_episodes.
A product only ever gets identity linking if its own frontend chooses to call
/identify — /collect's no-PII guarantee holds either way.

Environment:
  CAPMAN_WEB_MANIFESTS  comma-separated paths to product manifest TOMLs
  CAPMAN_WEB_KEYS       per-product secrets: "<key>=<secret>,<key2>=<secret2>"
  CAPMAN_PG_DSN         Postgres DSN (role must be a member of capman_app)
  PORT                  bind port (default 8080)
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response

from capman.web.manifest import ProductManifest, load_manifests, tenant_id
from db import CollectorDB
from identity import IdentityError, validate_identify
from validate import MAX_BODY_BYTES, BatchError, validate_batch

MAX_IDENTIFY_BODY_BYTES = 2 * 1024

logger = logging.getLogger(__name__)

RATE_WINDOW_S = 60
RATE_MAX_REQUESTS = 60
RATE_MAX_EVENTS = 600


def parse_keys(raw: str) -> dict[str, str]:
    """Parse "<product>=<secret>,<product2>=<secret2>" into a dict."""
    keys: dict[str, str] = {}
    for pair in (raw or "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"malformed CAPMAN_WEB_KEYS entry: {pair!r}")
        product, secret = pair.split("=", 1)
        product, secret = product.strip(), secret.strip()
        if not product or not secret:
            raise ValueError(f"malformed CAPMAN_WEB_KEYS entry: {pair!r}")
        keys[product] = secret
    return keys


class SidRateLimiter:
    """In-memory sliding window per visit id. Single-instance by design;
    a multi-replica deployment tolerates it (limits become per-replica)."""

    def __init__(self, window_s: float = RATE_WINDOW_S,
                 max_requests: int = RATE_MAX_REQUESTS,
                 max_events: int = RATE_MAX_EVENTS):
        self._window_s = window_s
        self._max_requests = max_requests
        self._max_events = max_events
        self._hits: dict[str, deque[tuple[float, int]]] = {}

    def allow(self, sid: str, n_events: int, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        window = self._hits.setdefault(sid, deque())
        while window and now - window[0][0] > self._window_s:
            window.popleft()
        requests = len(window)
        events = sum(n for _, n in window)
        if requests >= self._max_requests or events + n_events > self._max_events:
            return False
        window.append((now, n_events))
        if len(self._hits) > 10_000:
            for stale in [s for s, w in self._hits.items() if not w or now - w[-1][0] > self._window_s]:
                self._hits.pop(stale, None)
        return True


def _json_error(status: int, code: str, message: str) -> Response:
    return Response(
        content=json.dumps({"error": code, "detail": message}),
        status_code=status,
        media_type="application/json",
    )


def _resolve_product(keys: dict[str, str], header_value: str | None) -> str | None:
    if not header_value:
        return None
    for product, secret in keys.items():
        if hmac.compare_digest(header_value, secret):
            return product
    return None


def create_app(manifests: dict[str, ProductManifest], keys: dict[str, str], db: Any,
               limiter: SidRateLimiter | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if hasattr(db, "connect"):
            await db.connect()
        yield
        if hasattr(db, "close"):
            await db.close()

    app = FastAPI(title="capman-web-collector", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    if limiter is None:
        limiter = SidRateLimiter()

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "products": len(manifests)}

    @app.post("/collect")
    async def collect(request: Request) -> Response:
        product = _resolve_product(keys, request.headers.get("x-cw-key"))
        if product is None or product not in manifests:
            return _json_error(401, "unauthorized", "invalid or missing X-CW-Key")

        body_bytes = await request.body()
        if len(body_bytes) > MAX_BODY_BYTES:
            return _json_error(413, "body_too_large", f"body exceeds {MAX_BODY_BYTES} bytes")

        try:
            body = json.loads(body_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _json_error(400, "invalid_json", "body is not valid JSON")

        manifest = manifests[product]
        try:
            events = validate_batch(manifest, body)
        except BatchError as exc:
            return _json_error(400, exc.code, exc.message)

        sid = events[0].sid
        if not limiter.allow(sid, len(events)):
            return _json_error(429, "rate_limited", "too many events for this visit")

        try:
            n = await db.insert_events(tenant_id(manifest), product, events)
        except Exception:
            logger.exception("insert failed for product=%s", product)
            return _json_error(500, "storage_error", "could not persist batch")

        return Response(
            content=json.dumps({"accepted": n}),
            status_code=200,
            media_type="application/json",
        )

    @app.post("/identify")
    async def identify(request: Request) -> Response:
        product = _resolve_product(keys, request.headers.get("x-cw-key"))
        if product is None or product not in manifests:
            return _json_error(401, "unauthorized", "invalid or missing X-CW-Key")

        body_bytes = await request.body()
        if len(body_bytes) > MAX_IDENTIFY_BODY_BYTES:
            return _json_error(413, "body_too_large", f"body exceeds {MAX_IDENTIFY_BODY_BYTES} bytes")

        try:
            body = json.loads(body_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _json_error(400, "invalid_json", "body is not valid JSON")

        try:
            ident = validate_identify(body)
        except IdentityError as exc:
            return _json_error(400, exc.code, exc.message)

        if not limiter.allow(f"identify:{ident.sid_hash}", 1):
            return _json_error(429, "rate_limited", "too many identify calls for this visit")

        try:
            await db.upsert_identity(tenant_id(manifests[product]), ident.sid_hash, ident.email, ident.ts)
        except Exception:
            logger.exception("identity upsert failed for product=%s", product)
            return _json_error(500, "storage_error", "could not persist identity")

        return Response(status_code=204)

    return app


def build_app_from_env() -> FastAPI:
    manifest_paths = [p for p in os.environ.get("CAPMAN_WEB_MANIFESTS", "").split(",") if p.strip()]
    if not manifest_paths:
        raise RuntimeError("CAPMAN_WEB_MANIFESTS must list at least one manifest path")
    manifests = load_manifests(manifest_paths)
    keys = parse_keys(os.environ.get("CAPMAN_WEB_KEYS", ""))
    missing = [k for k in manifests if k not in keys]
    if missing:
        logger.warning("products without a CAPMAN_WEB_KEYS secret (ingestion disabled): %s", missing)
    dsn = os.environ.get("CAPMAN_PG_DSN", "")
    if not dsn:
        raise RuntimeError("CAPMAN_PG_DSN must be set")
    db = CollectorDB(dsn)
    return create_app(manifests, keys, db)


app = build_app_from_env() if os.environ.get("CAPMAN_WEB_MANIFESTS") else None
