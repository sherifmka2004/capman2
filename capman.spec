# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for capman2 desktop app.
Build: pyinstaller capman.spec

Produces a one-dir bundle (not one-file) to avoid slow startup unpacking.
"""
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# Collect chromadb including Rust native extensions
chroma_datas, chroma_binaries, chroma_hidden = collect_all("chromadb")

# Collect onnxruntime (used by chromadb for embeddings)
ort_datas, ort_binaries, ort_hidden = collect_all("onnxruntime")

# All submodules that use dynamic import / pkgutil discovery
sensor_hidden = collect_submodules("capman.sensors")
pipeline_hidden = collect_submodules("capman.pipeline")
api_hidden = collect_submodules("capman.api")
api_hidden += collect_submodules("capman.api.routes")
knowledge_hidden = collect_submodules("capman.knowledge")

hiddenimports = (
    sensor_hidden
    + pipeline_hidden
    + api_hidden
    + knowledge_hidden
    + chroma_hidden
    + ort_hidden
    + [
        # uvicorn dynamic protocol selection
        "uvicorn",
        "uvicorn.main",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.lifespan.on",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        # fastapi / pydantic
        "fastapi",
        "fastapi.routing",
        "pydantic",
        "pydantic.v1",
        "starlette",
        "starlette.routing",
        # storage
        "aiosqlite",
        "sqlite3",
        # sensors
        "pynput.keyboard",
        "pynput.mouse",
        "pynput.keyboard._xorg",
        "pynput.mouse._xorg",
        "mss",
        "mss.darwin",
        "mss.linux",
        "mss.windows",
        "pyperclip",
        "watchdog",
        "watchdog.observers",
        "watchdog.observers.fsevents",
        "watchdog.observers.inotify",
        "watchdog.observers.winapi",
        "pytesseract",
        # desktop
        "pystray",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.ImageFont",
        # network / tls
        "cryptography",
        "cryptography.hazmat.primitives",
        "cryptography.hazmat.primitives.asymmetric.rsa",
        "cryptography.x509",
        "httpx",
        "anyio",
        "anyio.lowlevel",
        # config
        "tomllib",
        "tomli_w",
        # other
        "anthropic",
        "click",
        "rich",
        "rich.console",
    ]
)

# Platform-specific additions
if sys.platform == "darwin":
    hiddenimports += [
        "AppKit",
        "Foundation",
        "ApplicationServices",
        "Quartz",
        "Vision",
        "objc",
        "pynput.keyboard._darwin",
        "pynput.mouse._darwin",
        "mss.darwin",
    ]
elif sys.platform == "win32":
    hiddenimports += [
        "win32gui",
        "win32process",
        "win32com",
        "win32com.client",
        "pywintypes",
        "comtypes",
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        "mss.windows",
    ]
elif sys.platform.startswith("linux"):
    hiddenimports += [
        "Xlib",
        "pynput.keyboard._xorg",
        "pynput.mouse._xorg",
        "mss.linux",
    ]

datas = [
    ("config/*.toml", "config"),
    ("capman/storage/schema.sql", "capman/storage"),
    ("capman/storage/migrations", "capman/storage/migrations"),
    ("capman/assets", "capman/assets"),
] + chroma_datas + ort_datas

a = Analysis(
    ["capman_desktop_entry.py"],
    pathex=["."],
    binaries=chroma_binaries + ort_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=["hooks/"],
    hooksconfig={},
    runtime_hooks=["hooks/runtime_hook_paths.py"],
    excludes=[
        "sentence_transformers",
        "torch",
        "torchvision",
        "torchaudio",
        "transformers",
        "tensorflow",
        "keras",
        "matplotlib",
        "scipy",
        "sklearn",
        "IPython",
        "ipykernel",
        "jupyter",
        "notebook",
        "unittest",
        "pytest",
        "setuptools",
        "pip",
        "pkg_resources",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="capman2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX breaks native extensions; keep off
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=(
        "capman/assets/icon.icns" if sys.platform == "darwin"
        else "capman/assets/icon.ico" if sys.platform == "win32"
        else None
    ),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="capman2",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="capman2.app",
        icon="capman/assets/icon.icns",
        bundle_identifier="com.capman2.desktop",
        info_plist={
            "NSPrincipalClass": "NSApplication",
            "NSAppleScriptEnabled": False,
            "LSUIElement": True,                  # menu-bar only, no Dock icon
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "1",
            "NSAccessibilityUsageDescription": (
                "capman2 uses accessibility to track active windows and capture "
                "keyboard/mouse activity for knowledge capture."
            ),
            "NSScreenCaptureUsageDescription": (
                "capman2 takes periodic screenshots for OCR-based knowledge capture."
            ),
        },
    )
