"""POST /events — receives events from the browser extension."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Any

from capman.events import Event, EventType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/events", tags=["events"])


class EventPayload(BaseModel):
    type: str
    app: str = "Browser"
    window_title: str = ""
    payload: dict[str, Any] = {}
    sensor_id: str = "browser_relay"


@router.post("")
async def receive_event(body: EventPayload, request: Request):
    try:
        etype = EventType(body.type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown event type: {body.type}")

    event = Event(
        type=etype,
        app=body.app,
        window_title=body.window_title,
        payload=body.payload,
        sensor_id=body.sensor_id,
    )

    from capman.sensors.browser_relay import get_relay_queue
    q = get_relay_queue()
    if q is not None:
        try:
            q.put_nowait(event)
        except Exception:
            logger.warning("Relay queue full, browser event dropped")
    else:
        # Fallback: write directly to DB if pipeline not running
        db = getattr(request.app.state, "db", None)
        if db:
            await db.insert_event(event)

    return {"status": "ok", "event_id": event.id}
