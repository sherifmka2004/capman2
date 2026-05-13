"""
DocumentContentTracker — decides *which* slides/pages/sheets the user actually
read and emits `DOC_CONTENT` events with their text.

Algorithm (deferred capture):
  1. The DocumentSensor calls `note_navigation(state, app, title)` every time the
     user lands on a new unit (slide N / page N / sheet X / note Y).
  2. We cancel any in-flight capture for the previous unit and schedule a new one
     to fire `policy.dwell_threshold_s` seconds later.
  3. If the user moves on before the timer fires, the capture is cancelled —
     so a quick scroll-through extracts nothing (no OCR, no event). Cancel-on-nav
     *is* the "scrolled past vs looked at" discriminator.
  4. When the timer fires and the user is *still* on that unit, we run the
     extractor chain, dedupe by content hash, cap per document, and emit a
     `DOC_CONTENT` event via the supplied async callback.

Everything that touches the OS (screenshot/OCR/AppleScript) runs in a thread-pool
executor, so the event loop is never blocked.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Awaitable, Callable

from capman.document.attention import AttentionPolicy, DwellAttentionPolicy
from capman.document.extractors import ContentExtractionChain, build_extraction_chain
from capman.document.model import DocumentView, ExtractedContent
from capman.events import Event, EventType

logger = logging.getLogger(__name__)

EmitCallback = Callable[[Event], Awaitable[None]]


class DocumentContentTracker:
    def __init__(
        self,
        emit: EmitCallback,
        chain: ContentExtractionChain,
        policy: AttentionPolicy,
        *,
        max_items_per_doc: int = 80,
        max_chars: int = 8000,
    ):
        self._emit = emit
        self._chain = chain
        self._policy = policy
        self._max_items_per_doc = max(1, int(max_items_per_doc))
        self._max_chars = max(200, int(max_chars))

        self._current: DocumentView | None = None
        self._pending: asyncio.Task | None = None
        self._seen_counts: dict[tuple, int] = {}          # view.key -> times navigated to
        self._emitted_hashes: set[tuple] = set()          # (view.key, content_hash)
        self._emitted_per_doc: dict[str, int] = {}        # doc id -> count emitted
        self._capped_logged: set[str] = set()
        # metrics
        self.captured = 0
        self.skipped_quick = 0
        self.skipped_dup = 0

    @property
    def enabled(self) -> bool:
        return self._chain.usable

    # -- called by DocumentSensor -------------------------------------------

    def note_navigation(self, state, app: str, window_title: str) -> None:
        """User landed on a (possibly) new document unit."""
        view = DocumentView.from_doc_state(state, app, window_title)
        if view is None:
            return
        if self._current is not None and view.key == self._current.key:
            return  # same unit (e.g. a redundant poll) — nothing to do

        # New unit → cancel the previous unit's pending capture.
        self._cancel_pending()
        count = self._seen_counts.get(view.key, 0) + 1
        self._seen_counts[view.key] = count
        view = view.with_revisit(count)
        self._current = view

        doc_id = self._doc_id(view)
        if self._emitted_per_doc.get(doc_id, 0) >= self._max_items_per_doc:
            if doc_id not in self._capped_logged:
                logger.info("DocumentContentTracker: per-doc cap (%d) reached for %s — "
                            "not capturing further units", self._max_items_per_doc, doc_id)
                self._capped_logged.add(doc_id)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # not in an event loop (shouldn't happen in the sensor)
        self._pending = loop.create_task(self._deferred_capture(view))

    def note_doc_closed(self) -> None:
        """User moved away from any document app."""
        self._cancel_pending()
        self._current = None

    def stop(self) -> None:
        self._cancel_pending()

    # -- internals -----------------------------------------------------------

    def _cancel_pending(self) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()
        self._pending = None

    async def _deferred_capture(self, view: DocumentView) -> None:
        try:
            await asyncio.sleep(self._policy.dwell_threshold_s)
        except asyncio.CancelledError:
            return
        # Still on this unit?
        if self._current is None or self._current.key != view.key:
            self.skipped_quick += 1
            return
        dwell = time.time() - view.arrived_at
        if not self._policy.worth_capturing(view, dwell):
            self.skipped_quick += 1
            return

        try:
            loop = asyncio.get_running_loop()
            content: ExtractedContent | None = await loop.run_in_executor(
                None, self._chain.extract, view
            )
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.debug("DocumentContentTracker extraction error: %s", e)
            return
        if content is None or not content.text.strip():
            return

        text = content.text.strip()
        if len(text) > self._max_chars:
            text = text[: self._max_chars]
        content_hash = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:16]
        dedup_key = (view.key, content_hash)
        if dedup_key in self._emitted_hashes:
            self.skipped_dup += 1
            return

        # Re-check we're still on it (extraction may have taken a moment).
        if self._current is None or self._current.key != view.key:
            self.skipped_quick += 1
            return

        self._emitted_hashes.add(dedup_key)
        doc_id = self._doc_id(view)
        self._emitted_per_doc[doc_id] = self._emitted_per_doc.get(doc_id, 0) + 1
        self.captured += 1

        label = content.item_label or view.item_label
        payload = {
            "doc_type": view.doc_type,
            "doc_name": view.doc_name,
            "doc_path": view.doc_path,
            "app": view.app,
            "item_kind": view.item_kind,
            "item_index": view.item_index,
            "item_label": label,
            "text": text,
            "text_chars": len(text),
            "dwell_s": round(time.time() - view.arrived_at, 1),
            "revisit_count": view.revisit_count,
            "source": content.source,
            "content_hash": content_hash,
        }
        event = Event(
            type=EventType.DOC_CONTENT,
            app=view.app,
            window_title=view.window_title,
            payload=payload,
            sensor_id="documents",
        )
        try:
            await self._emit(event)
        except Exception as e:
            logger.debug("DocumentContentTracker emit failed: %s", e)

    @staticmethod
    def _doc_id(view: DocumentView) -> str:
        return view.doc_path or view.doc_name or view.app


def build_content_tracker(config: dict, emit: EmitCallback, adapter) -> DocumentContentTracker:
    docs_cfg = config.get("sensors", {}).get("documents", {})
    policy = DwellAttentionPolicy(
        min_attention_s=float(docs_cfg.get("content_min_attention_s", 4.0)),
        revisit_threshold=int(docs_cfg.get("content_revisit_threshold", 2)),
    )
    chain = build_extraction_chain(config, adapter)
    return DocumentContentTracker(
        emit, chain, policy,
        max_items_per_doc=int(docs_cfg.get("content_max_items_per_doc", 80)),
        max_chars=int(docs_cfg.get("content_max_chars", 8000)),
    )
