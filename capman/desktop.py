"""
capman2 desktop entry point — system tray icon + browser-based web UI.

Launches the FastAPI daemon in a background thread and opens the dashboard
in the user's default browser. The system tray icon provides Start/Stop/Quit.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

logger = logging.getLogger("capman.desktop")


def _generate_icon(path: Path) -> None:
    """Create a simple 64x64 tray icon using Pillow if the asset is missing."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Blue filled circle
    draw.ellipse([2, 2, 62, 62], fill=(37, 99, 235, 255))
    # White "c" letter
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 36)
    except Exception:
        font = ImageFont.load_default()
    draw.text((16, 10), "c", fill=(255, 255, 255, 255), font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")


def _icon_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).parent
    return base / "assets" / "icon.png"


def _wait_for_server(port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/health", timeout=1)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    return False


def _run_server(config: dict) -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    from capman.main import _run_daemon, _inject_secrets
    _inject_secrets(config)
    asyncio.run(_run_daemon(config))


def _build_tray_icon(config: dict, server_thread: threading.Thread):
    import pystray
    from PIL import Image

    icon_p = _icon_path()
    if not icon_p.exists():
        _generate_icon(icon_p)
    img = Image.open(icon_p)

    port = config["api"]["port"]

    def on_open(icon, item):
        webbrowser.open(f"http://localhost:{port}")

    def on_stop(icon, item):
        from capman.config import get_data_dir
        data_dir = get_data_dir(config)
        pid_file = data_dir / "capman.pid"
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
            except (ProcessLookupError, ValueError):
                pass

    def on_quit(icon, item):
        on_stop(icon, item)
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open Dashboard", on_open, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Stop Capture", on_stop),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit capman2", on_quit),
    )
    return pystray.Icon("capman2", img, "capman2", menu)


def run_desktop() -> None:
    from capman.config import load_config
    from capman.main import setup_logging

    config = load_config()
    setup_logging(config["core"]["log_level"])

    server_thread = threading.Thread(target=_run_server, args=(config,), daemon=True)
    server_thread.start()

    port = config["api"]["port"]
    logger.info("Waiting for server on port %d...", port)
    if _wait_for_server(port):
        webbrowser.open(f"http://localhost:{port}")
    else:
        logger.warning("Server did not start within timeout; opening anyway")
        webbrowser.open(f"http://localhost:{port}")

    icon = _build_tray_icon(config, server_thread)
    icon.run()
