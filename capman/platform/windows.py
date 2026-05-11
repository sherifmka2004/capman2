"""Windows platform adapter using win32api and Tesseract."""
from __future__ import annotations

import logging

from capman.platform.base import PlatformAdapter

logger = logging.getLogger(__name__)


class WindowsAdapter(PlatformAdapter):
    def __init__(self, config: dict):
        self._config = config

    @property
    def name(self) -> str:
        return "windows"

    def get_active_window(self) -> tuple[str, str]:
        try:
            import win32gui  # type: ignore
            import win32process  # type: ignore
            import psutil  # type: ignore
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid)
            return proc.name(), title
        except Exception as e:
            logger.debug("get_active_window failed: %s", e)
            return "", ""

    def ocr_image(self, image_path: str) -> str:
        try:
            import pytesseract
            from PIL import Image
            return pytesseract.image_to_string(Image.open(image_path)).strip()
        except Exception as e:
            logger.debug("OCR failed: %s", e)
            return ""

    # ------------------------------------------------------------------
    # Document state querying via COM (win32com)
    # ------------------------------------------------------------------

    def get_document_state(self, app: str, window_title: str):
        from capman.platform.base import classify_app, parse_doc_state_from_title
        doc_type = classify_app(app)
        if not doc_type:
            return None
        app_lower = app.lower()
        try:
            if "powerpnt" in app_lower or "powerpoint" in app_lower:
                return self._query_powerpoint()
            elif "winword" in app_lower or "word" in app_lower:
                return self._query_word()
            elif "excel" in app_lower:
                return self._query_excel()
            elif "onenote" in app_lower:
                return self._query_onenote()
        except Exception as e:
            logger.debug("COM doc query failed for %s: %s", app, e)
        return parse_doc_state_from_title(app, window_title)

    def _query_powerpoint(self):
        from capman.events import DocState
        import win32com.client  # type: ignore
        ppt = win32com.client.GetActiveObject("PowerPoint.Application")
        pres = ppt.ActivePresentation
        slide = ppt.ActiveWindow.View.Slide
        curr = slide.SlideIndex
        total = pres.Slides.Count
        title = ""
        try:
            title = slide.Shapes.Title.TextFrame.TextRange.Text
        except Exception:
            pass
        return DocState(
            doc_type="presentation", app="Microsoft PowerPoint",
            doc_name=pres.Name, doc_path=pres.FullName,
            current_slide=curr, total_slides=total, slide_title=title,
        )

    def _query_word(self):
        from capman.events import DocState
        import win32com.client  # type: ignore
        word = win32com.client.GetActiveObject("Word.Application")
        doc = word.ActiveDocument
        # wdActiveEndPageNumber = 3
        curr_page = word.Selection.Information(3)
        total_pages = doc.ComputeStatistics(2)  # wdStatisticPages = 2
        return DocState(
            doc_type="document", app="Microsoft Word",
            doc_name=doc.Name, doc_path=doc.FullName,
            current_page=curr_page, total_pages=total_pages,
        )

    def _query_excel(self):
        from capman.events import DocState
        import win32com.client  # type: ignore
        xl = win32com.client.GetActiveObject("Excel.Application")
        wb = xl.ActiveWorkbook
        ws = xl.ActiveSheet
        return DocState(
            doc_type="spreadsheet", app="Microsoft Excel",
            doc_name=wb.Name, doc_path=wb.FullName,
            sheet_name=ws.Name, sheet_index=ws.Index,
        )

    def _query_onenote(self):
        from capman.events import DocState
        import win32com.client  # type: ignore
        on = win32com.client.GetActiveObject("OneNote.Application")
        # OneNote COM API — get current page title
        import xml.etree.ElementTree as ET
        xml_str = on.GetHierarchy("", 4, "")  # hsPages = 4
        # Parse to find current page (simplified)
        return DocState(doc_type="notes", app="Microsoft OneNote")
