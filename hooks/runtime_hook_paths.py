"""PyInstaller runtime hook — fix up paths and env vars for the frozen bundle."""
import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    bundle = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    # Help onnxruntime locate its shared libraries inside the bundle
    os.environ.setdefault("ORT_DYLIB_PATH", str(bundle))
