"""GET/POST /settings — read and write user config overlay (~/.capman/config.toml)."""
from __future__ import annotations

import os
import signal
from pathlib import Path
from typing import Optional

import tomli_w
import tomllib
from fastapi import Request
from fastapi.routing import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/settings", tags=["settings"])

ALL_SENSORS = [
    "window", "screenshot", "keyboard", "mouse", "clipboard",
    "shell", "filesystem", "browser_relay", "documents", "idle",
]

_USER_CONFIG = Path("~/.capman/config.toml").expanduser()


def _read_user_config() -> dict:
    if _USER_CONFIG.exists():
        with open(_USER_CONFIG, "rb") as f:
            return tomllib.load(f)
    return {}


def _write_user_config(data: dict) -> None:
    _USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with open(_USER_CONFIG, "wb") as f:
        tomli_w.dump(data, f)


def _is_daemon_running(config: dict) -> bool:
    try:
        from capman.config import get_data_dir
        data_dir = get_data_dir(config)
        pid_file = data_dir / "capman.pid"
        if not pid_file.exists():
            return False
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError, FileNotFoundError):
        return False


@router.get("")
async def get_settings(request: Request):
    config = request.app.state.config
    sensors_enabled = config.get("sensors", {}).get("enabled", ALL_SENSORS)
    screenshot_interval = config.get("sensors", {}).get("screenshot", {}).get("interval_seconds", 30)
    idle_threshold = config.get("sensors", {}).get("idle", {}).get("idle_threshold_s", 180)

    user_cfg = _read_user_config()
    anthropic_key_set = bool(
        os.environ.get("ANTHROPIC_API_KEY")
        or user_cfg.get("secrets", {}).get("anthropic_api_key")
    )
    openrouter_key_set = bool(
        os.environ.get("OPENROUTER_API_KEY")
        or user_cfg.get("secrets", {}).get("openrouter_api_key")
    )

    # Embeddings are now static model2vec weights bundled with the app;
    # nothing is downloaded at first use. Kept reporting the legacy ONNX
    # cache only so users can see (and delete) what Chroma left behind.
    onnx_cache = Path("~/.cache/chroma/onnx_models").expanduser()
    model_ready = onnx_cache.exists() and any(onnx_cache.iterdir())

    return {
        "sensors_enabled": sensors_enabled,
        "all_sensors": ALL_SENSORS,
        "screenshot_interval_s": screenshot_interval,
        "idle_threshold_s": idle_threshold,
        "anthropic_api_key_set": anthropic_key_set,
        "openrouter_api_key_set": openrouter_key_set,
        "daemon_running": _is_daemon_running(config),
        "model_ready": model_ready,
    }


class SettingsPayload(BaseModel):
    sensors_enabled: Optional[list[str]] = None
    screenshot_interval_s: Optional[int] = None
    idle_threshold_s: Optional[int] = None
    anthropic_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None


@router.post("")
async def save_settings(payload: SettingsPayload):
    user_cfg = _read_user_config()

    if payload.sensors_enabled is not None:
        user_cfg.setdefault("sensors", {})["enabled"] = payload.sensors_enabled

    if payload.screenshot_interval_s is not None:
        user_cfg.setdefault("sensors", {}).setdefault("screenshot", {})["interval_seconds"] = payload.screenshot_interval_s

    if payload.idle_threshold_s is not None:
        user_cfg.setdefault("sensors", {}).setdefault("idle", {})["idle_threshold_s"] = payload.idle_threshold_s

    if payload.anthropic_api_key:
        user_cfg.setdefault("secrets", {})["anthropic_api_key"] = payload.anthropic_api_key
        os.environ["ANTHROPIC_API_KEY"] = payload.anthropic_api_key

    if payload.openrouter_api_key:
        user_cfg.setdefault("secrets", {})["openrouter_api_key"] = payload.openrouter_api_key
        os.environ["OPENROUTER_API_KEY"] = payload.openrouter_api_key

    _write_user_config(user_cfg)
    return {"status": "saved"}


@router.post("/stop")
async def stop_daemon(request: Request):
    config = request.app.state.config
    try:
        from capman.config import get_data_dir
        data_dir = get_data_dir(config)
        pid_file = data_dir / "capman.pid"
        if not pid_file.exists():
            return {"status": "not_running"}
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        return {"status": "stopped", "pid": pid}
    except (ProcessLookupError, ValueError):
        return {"status": "not_running"}
