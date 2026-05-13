"""macOS platform adapter using NSWorkspace and Apple Vision OCR."""
from __future__ import annotations

import logging

from capman.platform.base import PlatformAdapter

logger = logging.getLogger(__name__)


class MacOSAdapter(PlatformAdapter):
    def __init__(self, config: dict):
        self._config = config

    @property
    def name(self) -> str:
        return "macos"

    def get_element_at(self, x: int, y: int) -> dict | None:
        """Resolve the AX element under (x, y) via
        ``AXUIElementCopyElementAtPosition``. Best-effort; returns None when
        PyObjC isn't available or accessibility permissions weren't granted."""
        try:
            # ApplicationServices ships these symbols on macOS 10.x+.
            from ApplicationServices import (  # type: ignore
                AXUIElementCreateSystemWide,
                AXUIElementCopyElementAtPosition,
                AXUIElementCopyAttributeValue,
            )
        except Exception:
            return None

        try:
            sysw = AXUIElementCreateSystemWide()
            err, elem = AXUIElementCopyElementAtPosition(sysw, float(x), float(y), None)
            if err or elem is None:
                return None

            def _attr(name: str):
                try:
                    e, val = AXUIElementCopyAttributeValue(elem, name, None)
                    return val if not e else None
                except Exception:
                    return None

            role = _attr("AXRole") or ""
            label = (
                _attr("AXTitle")
                or _attr("AXDescription")
                or _attr("AXValue")
                or ""
            )
            value = _attr("AXValue") or ""
            return {
                "role": str(role),
                "label": str(label)[:200],
                "value": str(value)[:200] if value and value != label else "",
                "app": "",  # filled in by sensor (already knows active app)
            }
        except Exception as e:
            logger.debug("get_element_at failed: %s", e)
            return None

    def get_active_window(self) -> tuple[str, str]:
        try:
            from AppKit import NSWorkspace  # type: ignore
            app = NSWorkspace.sharedWorkspace().activeApplication()
            app_name = app.get("NSApplicationName", "")
            # Window title requires Accessibility API — use subprocess fallback
            title = self._get_window_title_via_applescript(app_name)
            return app_name, title
        except Exception as e:
            logger.debug("get_active_window (NSWorkspace) failed: %s", e)
            return self._get_active_window_fallback()

    def _get_window_title_via_applescript(self, app_name: str) -> str:
        import subprocess
        script = f'tell application "System Events" to tell process "{app_name}" to get name of front window'
        try:
            result = subprocess.check_output(
                ["osascript", "-e", script], text=True, timeout=1
            ).strip()
            return result
        except Exception:
            return ""

    def _get_active_window_fallback(self) -> tuple[str, str]:
        import subprocess
        script = (
            'tell application "System Events" to get name of first process '
            'whose frontmost is true'
        )
        try:
            app = subprocess.check_output(
                ["osascript", "-e", script], text=True, timeout=1
            ).strip()
            return app, ""
        except Exception:
            return "", ""

    def ocr_image(self, image_path: str) -> str:
        """Try Apple Vision first, fall back to Tesseract."""
        text = self._ocr_apple_vision(image_path)
        if text:
            return text
        return self._ocr_tesseract(image_path)

    def _ocr_apple_vision(self, image_path: str) -> str:
        try:
            import Vision  # type: ignore
            import Quartz  # type: ignore
            url = Quartz.CFURLCreateFromFileSystemRepresentation(
                None, image_path.encode(), len(image_path), False
            )
            handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
            request = Vision.VNRecognizeTextRequest.alloc().init()
            request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
            handler.performRequests_error_([request], None)
            results = []
            for obs in request.results():
                candidate = obs.topCandidates_(1)[0]
                results.append(candidate.string())
            return "\n".join(results)
        except Exception as e:
            logger.debug("Apple Vision OCR failed: %s", e)
            return ""

    def _ocr_tesseract(self, image_path: str) -> str:
        try:
            import pytesseract
            from PIL import Image
            return pytesseract.image_to_string(Image.open(image_path)).strip()
        except Exception as e:
            logger.debug("Tesseract OCR failed: %s", e)
            return ""

    # ------------------------------------------------------------------
    # Document state querying via AppleScript
    # ------------------------------------------------------------------

    def get_document_state(self, app: str, window_title: str):
        from capman.platform.base import classify_app, parse_doc_state_from_title
        from capman.events import DocState

        doc_type = classify_app(app)
        if not doc_type:
            return None

        app_lower = app.lower()

        try:
            if "powerpoint" in app_lower:
                return self._query_powerpoint()
            elif "keynote" in app_lower:
                return self._query_keynote()
            elif "word" in app_lower:
                return self._query_word()
            elif "pages" in app_lower:
                return self._query_pages()
            elif "excel" in app_lower:
                return self._query_excel()
            elif "numbers" in app_lower:
                return self._query_numbers()
            elif app_lower in ("notes", "apple notes"):
                return self._query_notes()
            elif "preview" in app_lower:
                return self._query_preview()
        except Exception as e:
            logger.debug("AppleScript doc query failed for %s: %s", app, e)

        # Fallback: parse from window title
        return parse_doc_state_from_title(app, window_title)

    def _run_applescript(self, script: str, timeout: float = 1.5) -> str:
        import subprocess
        try:
            return subprocess.check_output(
                ["osascript", "-e", script], text=True, timeout=timeout,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            return ""

    def _query_powerpoint(self):
        from capman.events import DocState
        script = """
tell application "Microsoft PowerPoint"
    set d to active presentation
    set curr to slide number of current slide of active window
    set total to count of slides of d
    set t to ""
    try
        set t to name of current slide of active window
    end try
    set p to path of d
    return (name of d) & "|" & curr & "|" & total & "|" & t & "|" & p
end tell"""
        out = self._run_applescript(script)
        if not out or "|" not in out:
            return None
        parts = out.split("|", 4)
        return DocState(
            doc_type="presentation", app="Microsoft PowerPoint",
            doc_name=parts[0], current_slide=int(parts[1] or 0),
            total_slides=int(parts[2] or 0), slide_title=parts[3],
            doc_path=parts[4] if len(parts) > 4 else "",
        )

    def _query_keynote(self):
        from capman.events import DocState
        script = """
tell application "Keynote"
    set d to front document
    set curr to slide number of current slide
    set total to count of slides of d
    set t to ""
    try
        set t to name of current slide
    end try
    return (name of d) & "|" & curr & "|" & total & "|" & t
end tell"""
        out = self._run_applescript(script)
        if not out or "|" not in out:
            return None
        parts = out.split("|", 3)
        return DocState(
            doc_type="presentation", app="Keynote",
            doc_name=parts[0], current_slide=int(parts[1] or 0),
            total_slides=int(parts[2] or 0),
            slide_title=parts[3] if len(parts) > 3 else "",
        )

    def _query_word(self):
        from capman.events import DocState
        script = """
tell application "Microsoft Word"
    set d to active document
    set curr to get page number of selection
    set total to count of pages of d
    set h to ""
    try
        set h to name of active heading of selection
    end try
    set p to full name of d
    return (name of d) & "|" & curr & "|" & total & "|" & h & "|" & p
end tell"""
        out = self._run_applescript(script)
        if not out or "|" not in out:
            return None
        parts = out.split("|", 4)
        return DocState(
            doc_type="document", app="Microsoft Word",
            doc_name=parts[0], current_page=int(parts[1] or 0),
            total_pages=int(parts[2] or 0),
            section_heading=parts[3],
            doc_path=parts[4] if len(parts) > 4 else "",
        )

    def _query_pages(self):
        from capman.events import DocState
        script = """
tell application "Pages"
    set d to front document
    return name of d
end tell"""
        doc_name = self._run_applescript(script)
        if not doc_name:
            return None
        return DocState(doc_type="document", app="Pages", doc_name=doc_name)

    def _query_excel(self):
        from capman.events import DocState
        script = """
tell application "Microsoft Excel"
    set d to active workbook
    set s to name of active sheet
    set idx to 0
    try
        set idx to index of active sheet
    end try
    return (name of d) & "|" & s & "|" & idx
end tell"""
        out = self._run_applescript(script)
        if not out or "|" not in out:
            return None
        parts = out.split("|", 2)
        return DocState(
            doc_type="spreadsheet", app="Microsoft Excel",
            doc_name=parts[0], sheet_name=parts[1],
            sheet_index=int(parts[2] or 0) if len(parts) > 2 else 0,
        )

    def _query_numbers(self):
        from capman.events import DocState
        script = """
tell application "Numbers"
    set d to front document
    set s to name of active sheet
    return (name of d) & "|" & s
end tell"""
        out = self._run_applescript(script)
        if not out or "|" not in out:
            return None
        parts = out.split("|", 1)
        return DocState(
            doc_type="spreadsheet", app="Numbers",
            doc_name=parts[0], sheet_name=parts[1] if len(parts) > 1 else "",
        )

    def _query_notes(self):
        from capman.events import DocState
        script = """
tell application "Notes"
    set n to selection
    if n is not {} then
        set note to item 1 of n
        set t to name of note
        set f to ""
        try
            set f to name of container of note
        end try
        return t & "|" & f
    end if
    return ""
end tell"""
        out = self._run_applescript(script)
        if not out:
            return None
        parts = out.split("|", 1)
        return DocState(
            doc_type="notes", app="Notes",
            note_title=parts[0],
            notebook=parts[1] if len(parts) > 1 else "",
        )

    def _query_preview(self):
        from capman.events import DocState
        # Preview doesn't expose page via AppleScript; fall back to title parsing
        return None
