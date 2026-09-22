"""capman2 web-behavior insights — a read-only dashboard over the shared
capman-pg tenant tables that services/analyst writes to.

Deliberately separate from services/collector (write path, public, no
browsing) and services/analyst (cron, no HTTP). This is the browse path:
one product-scoped tenant, key-gated, GET-only.
"""
from __future__ import annotations

import hmac
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from db import InsightsDB
from ui import INSIGHTS_HTML, LOGIN_HTML

logger = logging.getLogger("capman.insights")
logging.basicConfig(level=logging.INFO)

TENANT = os.environ.get("CAPMAN_TENANT_ID", "product:nuralto")
COOKIE_NAME = "cw_insights_key"

app = FastAPI()
db: InsightsDB | None = None


def _access_key() -> str:
    return os.environ.get("CAPMAN_INSIGHTS_KEY", "")


def _valid_key(candidate: str | None) -> bool:
    expected = _access_key()
    if not expected or not candidate:
        return False
    return hmac.compare_digest(candidate, expected)


@app.on_event("startup")
async def startup() -> None:
    global db
    dsn = os.environ["CAPMAN_PG_DSN"]
    if not _access_key():
        logger.warning("CAPMAN_INSIGHTS_KEY is unset — the dashboard is unreachable until it is set")
    db = InsightsDB(dsn)
    await db.connect()


@app.on_event("shutdown")
async def shutdown() -> None:
    if db is not None:
        await db.close()


@app.get("/health")
async def health():
    return {"status": "ok"}


def _authed(request: Request) -> bool:
    return _valid_key(request.cookies.get(COOKIE_NAME))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    key_param = request.query_params.get("key")
    if key_param and _valid_key(key_param):
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie(
            COOKIE_NAME, key_param, httponly=True, secure=True,
            samesite="lax", max_age=60 * 60 * 24 * 30,
        )
        return resp
    if _authed(request):
        html = INSIGHTS_HTML.replace("__TENANT__", TENANT)
        return HTMLResponse(html)
    error = "" if not key_param else '<div class="err">Invalid key.</div>'
    return HTMLResponse(LOGIN_HTML.replace("__ERROR__", error), status_code=401)


def _guard(request: Request) -> JSONResponse | None:
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return None


@app.get("/api/summary")
async def api_summary(request: Request):
    if (deny := _guard(request)) is not None:
        return deny
    return await db.summary(TENANT)


@app.get("/api/episodes")
async def api_episodes(request: Request, limit: int = 100):
    if (deny := _guard(request)) is not None:
        return deny
    limit = max(1, min(limit, 500))
    return await db.episodes(TENANT, limit)


@app.get("/api/playbooks")
async def api_playbooks(request: Request, limit: int = 100):
    if (deny := _guard(request)) is not None:
        return deny
    limit = max(1, min(limit, 500))
    return await db.playbooks(TENANT, limit)


@app.get("/api/gaps")
async def api_gaps(request: Request, limit: int = 100):
    if (deny := _guard(request)) is not None:
        return deny
    limit = max(1, min(limit, 500))
    return await db.gaps(TENANT, limit)
