"""PlatformAdapter ABC — OS-specific implementations sit behind this interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from capman.events import DocState


class PlatformAdapter(ABC):
    @abstractmethod
    def get_active_window(self) -> tuple[str, str]:
        """Return (app_name, window_title) for the currently focused window."""

    @abstractmethod
    def ocr_image(self, image_path: str) -> str:
        """Extract text from image at path. Returns empty string on failure."""

    def get_document_state(self, app: str, window_title: str) -> "DocState | None":
        """
        Return structured document state for the given app/window, or None if
        this app is not a recognized document application.
        Default implementation uses window-title parsing (works on all platforms).
        Override in platform subclasses for richer data.
        """
        return parse_doc_state_from_title(app, window_title)

    def get_element_at(self, x: int, y: int) -> dict | None:
        """
        Best-effort accessibility-tree lookup for the UI element under screen
        coordinate (x, y). Returns a small dict
        ``{role, label, value?, app?, bbox?}`` or None when no element can be
        resolved (off-screen, no AX permission, or platform doesn't implement).

        Used by `MouseSensor` to turn a raw click coordinate into "clicked the
        Run-all button in PyCharm". Implementations MUST be fast (< 30 ms
        target) and MUST NOT raise — return None on any failure. The sensor
        wraps the call in a hard timeout regardless.

        Default returns None so the sensor degrades gracefully on platforms
        without an implementation or when optional deps (PyObjC / pyatspi /
        uiautomation) aren't installed.
        """
        return None

    def get_document_visible_text(self, app: str, window_title: str) -> str | None:
        """
        Optional best-effort hook used by `capman.document.AppModelExtractor` to
        pull the *text the user is currently looking at* (current slide body,
        page paragraphs, visible cell range, note body) directly from the app's
        own model — AppleScript on macOS, AT-SPI / accessibility on Linux,
        UIAutomation on Windows.

        Default returns None: the document-content tracker silently falls back
        to OCR. Subclasses override per-platform / per-app as adapters land.
        """
        return None

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable platform name."""


# ---------------------------------------------------------------------------
# App classification registry
# ---------------------------------------------------------------------------

#: Maps lowercase app-name fragments → doc_type
APP_DOC_TYPES: dict[str, str] = {
    # Presentations
    "powerpoint":        "presentation",
    "powerpnt":          "presentation",   # Windows process name POWERPNT.EXE
    "keynote":           "presentation",
    "impress":           "presentation",
    "libreoffice impress": "presentation",
    # Word processors
    "word":              "document",
    "pages":             "document",
    "writer":            "document",
    "libreoffice writer": "document",
    "wordpad":           "document",
    "textedit":          "document",
    "gedit":             "document",
    "kate":              "document",
    "sublime text":      "document",
    # Spreadsheets
    "excel":             "spreadsheet",
    "numbers":           "spreadsheet",
    "calc":              "spreadsheet",
    "libreoffice calc":  "spreadsheet",
    # Notes
    "notes":             "notes",
    "onenote":           "notes",
    "notion":            "notes",
    "obsidian":          "notes",
    "evernote":          "notes",
    "bear":              "notes",
    "apple notes":       "notes",
    "standard notes":    "notes",
    # PDF viewers
    "preview":           "pdf",
    "acrobat":           "pdf",
    "evince":            "pdf",
    "okular":            "pdf",
    "zathura":           "pdf",
    "foxit":             "pdf",
    "pdf":               "pdf",
}


def classify_app(app: str) -> str | None:
    """Return doc_type for app name, or None if not a document app."""
    app_lower = app.lower()
    for fragment, doc_type in APP_DOC_TYPES.items():
        if fragment in app_lower:
            return doc_type
    return None


def parse_doc_state_from_title(app: str, window_title: str) -> "DocState | None":
    """
    Best-effort document state extraction from window title only.
    Used as cross-platform fallback when OS-specific APIs aren't available.
    """
    import re
    from capman.events import DocState

    doc_type = classify_app(app)
    if not doc_type:
        return None

    state = DocState(doc_type=doc_type, app=app)

    # Extract filename from title (common patterns: "Filename - App", "App - Filename")
    title = window_title.strip()
    for sep in [" — ", " - ", " – "]:
        parts = title.split(sep)
        if len(parts) >= 2:
            # Usually filename is the first part
            candidate = parts[0].strip()
            if candidate and not any(
                kw in candidate.lower() for kw in ["microsoft", "libreoffice", "apple"]
            ):
                state.doc_name = candidate
                break
    if not state.doc_name:
        state.doc_name = title

    # Slide number: "Slide X of Y", "X/Y", "(X/Y)"
    slide_match = re.search(r"[Ss]lide\s+(\d+)\s+of\s+(\d+)", title)
    if not slide_match:
        slide_match = re.search(r"\((\d+)/(\d+)\)", title)
    if slide_match and doc_type == "presentation":
        state.current_slide = int(slide_match.group(1))
        state.total_slides = int(slide_match.group(2))

    # Page number: "Page X of Y", "Page X", "[X/Y]"
    page_match = re.search(r"[Pp]age\s+(\d+)\s+of\s+(\d+)", title)
    if not page_match:
        page_match = re.search(r"\[(\d+)/(\d+)\]", title)
    if page_match and doc_type in ("document", "pdf"):
        state.current_page = int(page_match.group(1))
        state.total_pages = int(page_match.group(2))

    return state


def get_platform_adapter(config: dict) -> PlatformAdapter:
    """Factory: returns the right adapter for the current OS."""
    import sys
    if sys.platform == "darwin":
        from capman.platform.macos import MacOSAdapter
        return MacOSAdapter(config)
    elif sys.platform == "win32":
        from capman.platform.windows import WindowsAdapter
        return WindowsAdapter(config)
    else:
        from capman.platform.linux import LinuxAdapter
        return LinuxAdapter(config)
