"""Linux platform adapter using xdotool and Tesseract."""
from __future__ import annotations

import logging
import subprocess

from capman.platform.base import PlatformAdapter

logger = logging.getLogger(__name__)


class LinuxAdapter(PlatformAdapter):
    def __init__(self, config: dict):
        self._config = config

    @property
    def name(self) -> str:
        return "linux"

    def get_active_window(self) -> tuple[str, str]:
        try:
            wid = subprocess.check_output(
                ["xdotool", "getactivewindow"], text=True
            ).strip()
            name_out = subprocess.check_output(
                ["xdotool", "getwindowname", wid], text=True
            ).strip()
            pid_out = subprocess.check_output(
                ["xdotool", "getwindowpid", wid], text=True
            ).strip()
            app_name = self._pid_to_app(pid_out)
            return app_name, name_out
        except Exception as e:
            logger.debug("get_active_window failed: %s", e)
            return "", ""

    def get_element_at(self, x: int, y: int) -> dict | None:
        """Resolve the AT-SPI accessible at (x, y). Best-effort; returns None
        when ``pyatspi`` isn't installed or the desktop a11y bus isn't running
        (the typical case on a headless box or Wayland-only session)."""
        try:
            import pyatspi  # type: ignore
        except Exception:
            return None
        try:
            desktop = pyatspi.Registry.getDesktop(0)
            for app in desktop:
                if app is None:
                    continue
                # Walk the topmost windows and dive into the deepest descendant
                # at (x, y).  pyatspi.Component.getAccessibleAtPoint is the
                # standard call but isn't exposed uniformly across versions —
                # fall back to a manual hit-test.
                try:
                    elem = app.queryComponent().getAccessibleAtPoint(int(x), int(y), 0)
                except Exception:
                    elem = None
                if elem:
                    role = elem.getRoleName() if hasattr(elem, "getRoleName") else ""
                    label = elem.name or ""
                    return {
                        "role": str(role),
                        "label": str(label)[:200],
                        "value": "",
                        "app": app.name or "",
                    }
        except Exception as e:
            logger.debug("get_element_at (AT-SPI) failed: %s", e)
        return None

    def _pid_to_app(self, pid: str) -> str:
        try:
            comm = subprocess.check_output(
                ["cat", f"/proc/{pid}/comm"], text=True
            ).strip()
            return comm
        except Exception:
            return ""

    def ocr_image(self, image_path: str) -> str:
        try:
            import pytesseract
            from PIL import Image
            text = pytesseract.image_to_string(Image.open(image_path))
            return text.strip()
        except Exception as e:
            logger.debug("OCR failed for %s: %s", image_path, e)
            return ""

    # ------------------------------------------------------------------
    # Document state querying: LibreOffice UNO + title-parsing fallback
    # ------------------------------------------------------------------

    def get_document_state(self, app: str, window_title: str):
        from capman.platform.base import classify_app, parse_doc_state_from_title
        doc_type = classify_app(app)
        if not doc_type:
            return None
        app_lower = app.lower()
        # Try LibreOffice UNO for LibreOffice apps
        if "soffice" in app_lower or "libreoffice" in app_lower:
            result = self._query_libreoffice_uno(window_title)
            if result:
                return result
        # Title parsing covers most other cases (Evince, Okular, Zathura, etc.)
        return parse_doc_state_from_title(app, window_title)

    def _query_libreoffice_uno(self, window_title: str):
        """Query LibreOffice via UNO Python bridge."""
        from capman.events import DocState
        try:
            import subprocess
            # Check if LibreOffice is accepting UNO connections
            # LibreOffice must be started with:
            # soffice --accept="socket,host=localhost,port=2002;urp;StarOffice.ServiceManager"
            # We use the python-pptx / odfpy approach as fallback if UNO unavailable
            import sys
            # Try to connect via UNO
            sys.path.insert(0, "/usr/lib/libreoffice/program")
            import uno  # type: ignore
            from com.sun.star.beans import PropertyValue  # type: ignore
            localContext = uno.getComponentContext()
            resolver = localContext.ServiceManager.createInstanceWithContext(
                "com.sun.star.bridge.UnoUrlResolver", localContext
            )
            ctx = resolver.resolve(
                "uno:socket,host=localhost,port=2002;urp;StarOffice.ComponentContext"
            )
            smgr = ctx.ServiceManager
            desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
            comp = desktop.getCurrentComponent()
            if comp is None:
                return None

            comp_url = comp.getURL()
            doc_name = comp_url.split("/")[-1] if comp_url else window_title

            # Determine type from component service names
            names = comp.getSupportedServiceNames()
            if "com.sun.star.presentation.PresentationDocument" in names:
                controller = comp.getCurrentController()
                slide = controller.getCurrentPage()
                curr = slide.Number
                total = comp.DrawPages.Count
                title = ""
                try:
                    for i in range(slide.Count):
                        shape = slide.getByIndex(i)
                        if shape.getShapeType() == "com.sun.star.presentation.TitleTextShape":
                            title = shape.getString()
                            break
                except Exception:
                    pass
                return DocState(
                    doc_type="presentation", app="LibreOffice Impress",
                    doc_name=doc_name, doc_path=comp_url,
                    current_slide=curr, total_slides=total, slide_title=title,
                )
            elif "com.sun.star.text.TextDocument" in names:
                controller = comp.getCurrentController()
                view_cursor = controller.getViewCursor()
                curr_page = view_cursor.getPage()
                total_pages = comp.getDrawPages().Count if hasattr(comp, "getDrawPages") else 0
                return DocState(
                    doc_type="document", app="LibreOffice Writer",
                    doc_name=doc_name, doc_path=comp_url,
                    current_page=curr_page,
                )
            elif "com.sun.star.sheet.SpreadsheetDocument" in names:
                controller = comp.getCurrentController()
                sheet = controller.getActiveSheet()
                return DocState(
                    doc_type="spreadsheet", app="LibreOffice Calc",
                    doc_name=doc_name, doc_path=comp_url,
                    sheet_name=sheet.getName(),
                )
        except Exception as e:
            logger.debug("LibreOffice UNO query failed: %s", e)
        return None
