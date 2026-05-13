"""
Content extractors — pull the text the user is seeing out of a document.

Strategy + Chain-of-Responsibility:
  - `AppModelExtractor`  reads the actual document model via the OS accessibility /
    automation layer (highest fidelity; per-app, best-effort — falls back to None
    until a platform adapter implements `get_document_visible_text`).
  - `OcrScreenExtractor` screenshots the screen and OCRs it — universal, lower
    fidelity, but literally "what the user sees".
  - `ContentExtractionChain` tries available extractors in order and returns the
    first non-empty result.

`build_extraction_chain(config, adapter)` assembles the chain from config.
"""
from __future__ import annotations

import logging
import os
import tempfile
from abc import ABC, abstractmethod

from capman.document.model import DocumentView, ExtractedContent

logger = logging.getLogger(__name__)


class ContentExtractor(ABC):
    name: str = "extractor"

    @abstractmethod
    def available(self) -> bool:
        """Whether this extractor can be used at all in the current environment."""

    @abstractmethod
    def extract(self, view: DocumentView) -> ExtractedContent | None:
        """Return the text for `view`, or None if it couldn't be obtained."""


class AppModelExtractor(ContentExtractor):
    """Read the document's own text via the platform adapter (AppleScript / AX /
    AT-SPI / UIAutomation). The adapter exposes an optional
    `get_document_visible_text(app, window_title)` hook; if it isn't implemented
    for this OS/app, this extractor simply yields nothing and the chain moves on."""

    name = "app_model"

    def __init__(self, adapter, enabled: bool = True):
        self._adapter = adapter
        self._enabled = bool(enabled)

    def available(self) -> bool:
        return self._enabled and callable(getattr(self._adapter, "get_document_visible_text", None))

    def extract(self, view: DocumentView) -> ExtractedContent | None:
        try:
            text = self._adapter.get_document_visible_text(view.app, view.window_title)
        except Exception as e:  # adapter hook is best-effort
            logger.debug("app_model extract failed for %s: %s", view.app, e)
            return None
        if not text or not str(text).strip():
            return None
        return ExtractedContent(text=str(text).strip(), source="app_model", item_label=view.item_label)


class OcrScreenExtractor(ContentExtractor):
    """Capture the screen and OCR it. Universal fallback — works wherever `mss`
    and an OCR backend are available."""

    name = "ocr"

    def __init__(self, ocr_engine, enabled: bool = True):
        self._ocr = ocr_engine
        self._enabled = bool(enabled)

    def available(self) -> bool:
        if not self._enabled:
            return False
        try:
            import mss  # noqa: F401
        except ImportError:
            return False
        return True

    def extract(self, view: DocumentView) -> ExtractedContent | None:
        path = None
        try:
            import mss
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                path = f.name
            with mss.mss() as sct:
                sct.shot(output=path)
            text = (self._ocr.extract(path) or "").strip()
        except Exception as e:
            logger.debug("ocr extract failed: %s", e)
            return None
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        if not text:
            return None
        return ExtractedContent(text=text, source="ocr", item_label=view.item_label)


class ContentExtractionChain:
    """Tries each available extractor in order; returns the first result."""

    def __init__(self, extractors: list[ContentExtractor]):
        self._extractors = list(extractors)

    @property
    def usable(self) -> bool:
        return any(e.available() for e in self._extractors)

    def names(self) -> list[str]:
        return [e.name for e in self._extractors if e.available()]

    def extract(self, view: DocumentView) -> ExtractedContent | None:
        for ex in self._extractors:
            try:
                if not ex.available():
                    continue
                result = ex.extract(view)
            except Exception as e:
                logger.debug("extractor %s raised: %s", ex.name, e)
                continue
            if result and result.text.strip():
                return result
        return None


def build_extraction_chain(config: dict, adapter) -> ContentExtractionChain:
    docs_cfg = config.get("sensors", {}).get("documents", {})
    use_app_model = bool(docs_cfg.get("content_use_app_model", True))
    use_ocr = bool(docs_cfg.get("content_use_ocr_fallback", True))
    order = docs_cfg.get("content_extractor_order", ["app_model", "ocr"])

    from capman.pipeline.ocr import OCREngine
    by_name = {
        "app_model": lambda: AppModelExtractor(adapter, enabled=use_app_model),
        "ocr": lambda: OcrScreenExtractor(OCREngine(config), enabled=use_ocr),
    }
    extractors: list[ContentExtractor] = []
    for name in order:
        factory = by_name.get(name)
        if factory:
            extractors.append(factory())
    if not extractors:  # defensive: at least try OCR
        extractors.append(OcrScreenExtractor(OCREngine(config), enabled=use_ocr))
    return ContentExtractionChain(extractors)
