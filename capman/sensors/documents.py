"""
DocumentSensor — captures structured navigation in presentations, word processors,
spreadsheets, notes apps, and PDF viewers.

Polls every 2s. Detects changes in:
  - Which slide is active (PowerPoint, Keynote, LibreOffice Impress)
  - Which page is active (Word, Pages, LibreOffice Writer, PDFs)
  - Which sheet is active (Excel, Numbers, LibreOffice Calc)
  - Which note is open (Apple Notes, OneNote, Obsidian)

Emits DOC_SLIDE_CHANGE, DOC_PAGE_CHANGE, DOC_SHEET_CHANGE, DOC_NOTE_OPEN events.
Includes dwell time (how long the user stayed on the previous slide/page).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import ClassVar

from capman.events import Event, EventType, DocState
from capman.sensors.base import BaseSensor

logger = logging.getLogger(__name__)


def _nav_direction(prev: int, curr: int) -> str:
    if prev == 0:
        return "first"
    if curr == prev + 1:
        return "forward"
    if curr == prev - 1:
        return "backward"
    return "jump"


class DocumentSensor(BaseSensor):
    sensor_id: ClassVar[str] = "documents"
    platform_support: ClassVar[set[str]] = {"*"}

    async def run(self) -> None:
        from capman.platform.base import get_platform_adapter
        adapter = get_platform_adapter(self.config)
        poll_s = self.config.get("sensors", {}).get("documents", {}).get("poll_interval_s", 2.0)

        prev_state: DocState | None = None
        prev_state_ts: float = time.time()

        while not self._stop_event.is_set():
            try:
                app, title = adapter.get_active_window()
                if app:
                    state = await asyncio.get_event_loop().run_in_executor(
                        None, adapter.get_document_state, app, title
                    )
                    if state:
                        event = self._diff_to_event(state, prev_state, prev_state_ts, app, title)
                        if event:
                            await self.emit(event)
                            prev_state = state
                            prev_state_ts = time.time()
                    elif prev_state is not None:
                        # Moved away from a document app — reset tracking
                        prev_state = None
            except Exception as e:
                logger.debug("DocumentSensor poll error: %s", e)

            await asyncio.sleep(poll_s)

    def _diff_to_event(
        self,
        state: DocState,
        prev: DocState | None,
        prev_ts: float,
        app: str,
        title: str,
    ) -> Event | None:
        now = time.time()
        dwell = round(now - prev_ts, 1)

        # First time seeing any document
        if prev is None:
            return self._make_open_event(state, app, title)

        # Document changed entirely
        if state.doc_name and state.doc_name != prev.doc_name:
            return self._make_open_event(state, app, title)

        # Slide changed
        if state.doc_type == "presentation" and state.current_slide != prev.current_slide:
            if state.current_slide == 0:
                return None
            direction = _nav_direction(prev.current_slide, state.current_slide)
            state.dwell_s = dwell
            state.prev_slide = prev.current_slide
            state.nav_direction = direction
            return Event(
                type=EventType.DOC_SLIDE_CHANGE,
                app=app,
                window_title=title,
                payload=self._state_to_payload(state),
                sensor_id=self.sensor_id,
            )

        # Page changed
        if state.doc_type in ("document", "pdf") and state.current_page != prev.current_page:
            if state.current_page == 0:
                return None
            direction = _nav_direction(prev.current_page, state.current_page)
            state.dwell_s = dwell
            state.nav_direction = direction
            return Event(
                type=EventType.DOC_PAGE_CHANGE,
                app=app,
                window_title=title,
                payload=self._state_to_payload(state),
                sensor_id=self.sensor_id,
            )

        # Sheet changed
        if state.doc_type == "spreadsheet" and state.sheet_name != prev.sheet_name:
            state.prev_sheet = prev.sheet_name
            state.dwell_s = dwell
            return Event(
                type=EventType.DOC_SHEET_CHANGE,
                app=app,
                window_title=title,
                payload=self._state_to_payload(state),
                sensor_id=self.sensor_id,
            )

        # Note changed
        if state.doc_type == "notes" and state.note_title != prev.note_title:
            return Event(
                type=EventType.DOC_NOTE_OPEN,
                app=app,
                window_title=title,
                payload=self._state_to_payload(state),
                sensor_id=self.sensor_id,
            )

        return None  # No change detected

    def _make_open_event(self, state: DocState, app: str, title: str) -> Event:
        return Event(
            type=EventType.DOC_OPEN,
            app=app,
            window_title=title,
            payload=self._state_to_payload(state),
            sensor_id=self.sensor_id,
        )

    @staticmethod
    def _state_to_payload(state: DocState) -> dict:
        p: dict = {
            "doc_type": state.doc_type,
            "doc_name": state.doc_name,
            "doc_path": state.doc_path,
            "app": state.app,
        }
        if state.doc_type == "presentation":
            p.update({
                "current_slide": state.current_slide,
                "total_slides": state.total_slides,
                "slide_title": state.slide_title,
                "prev_slide": state.prev_slide,
                "dwell_s": state.dwell_s,
                "nav_direction": state.nav_direction,
            })
        elif state.doc_type in ("document", "pdf"):
            p.update({
                "current_page": state.current_page,
                "total_pages": state.total_pages,
                "section_heading": state.section_heading,
                "dwell_s": state.dwell_s,
                "nav_direction": state.nav_direction,
            })
        elif state.doc_type == "spreadsheet":
            p.update({
                "sheet_name": state.sheet_name,
                "prev_sheet": state.prev_sheet,
                "sheet_index": state.sheet_index,
                "dwell_s": state.dwell_s,
            })
        elif state.doc_type == "notes":
            p.update({
                "note_title": state.note_title,
                "notebook": state.notebook,
            })
        return p
