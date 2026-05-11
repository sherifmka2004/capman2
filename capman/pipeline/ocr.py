"""OCR abstraction: Apple Vision on macOS, Tesseract elsewhere."""
from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


class OCREngine:
    def __init__(self, config: dict | None = None):
        self.backend = self._detect_backend(config or {})

    def _detect_backend(self, config: dict) -> str:
        configured = config.get("platform", {}).get("ocr_backend", "")
        if configured:
            return configured
        if sys.platform == "darwin":
            try:
                import Vision  # type: ignore  # noqa
                return "apple_vision"
            except ImportError:
                pass
        return "tesseract"

    def extract(self, image_path: str) -> str:
        """Extract text from image. Returns empty string on failure."""
        if self.backend == "apple_vision":
            from capman.platform.macos import MacOSAdapter
            adapter = MacOSAdapter({})
            return adapter.ocr_image(image_path)
        return self._tesseract(image_path)

    def _tesseract(self, image_path: str) -> str:
        try:
            import pytesseract
            from PIL import Image
            return pytesseract.image_to_string(Image.open(image_path)).strip()
        except Exception as e:
            logger.debug("Tesseract OCR failed for %s: %s", image_path, e)
            return ""
