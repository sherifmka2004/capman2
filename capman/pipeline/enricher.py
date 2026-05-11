"""
Enricher — runs OCR on screenshots and normalizes event metadata after session close.
Mutates screenshot events in-place by filling ocr_text.
"""
from __future__ import annotations

import logging

from capman.events import Event, EventType, Session
from capman.pipeline.ocr import OCREngine

logger = logging.getLogger(__name__)


class Enricher:
    def __init__(self, config: dict):
        self._ocr = OCREngine(config)

    def enrich_session(self, session: Session) -> Session:
        """Run post-session enrichment. Returns the same session (mutated)."""
        self._run_ocr_on_screenshots(session)
        return session

    def _run_ocr_on_screenshots(self, session: Session) -> None:
        for event in session.events:
            if event.type != EventType.SCREENSHOT:
                continue
            path = event.payload.get("path", "")
            if not path or event.payload.get("ocr_text"):
                continue
            try:
                text = self._ocr.extract(path)
                event.payload["ocr_text"] = text
                logger.debug("OCR extracted %d chars from %s", len(text), path)
            except Exception as e:
                logger.warning("OCR enrichment failed for %s: %s", path, e)
                event.payload["ocr_text"] = ""
