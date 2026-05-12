"""FastAPI local server — browser extension relay + query interface + chat UI."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from capman.api.routes import events, sessions, query, knowledge, chat, context


def create_app(config: dict, db=None) -> FastAPI:
    app = FastAPI(title="capman2 Local API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    app.state.config = config
    app.state.db = db

    app.include_router(events.router)
    app.include_router(sessions.router)
    app.include_router(query.router)
    app.include_router(knowledge.router)
    app.include_router(chat.router)
    app.include_router(context.router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "capman2"}

    NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}

    @app.get("/chat", response_class=HTMLResponse)
    async def chat_ui():
        from capman.api.chat_ui import CHAT_HTML
        return HTMLResponse(content=CHAT_HTML, headers=NO_CACHE)

    @app.get("/", response_class=HTMLResponse)
    async def root():
        from capman.api.chat_ui import CHAT_HTML
        return HTMLResponse(content=CHAT_HTML, headers=NO_CACHE)

    return app
