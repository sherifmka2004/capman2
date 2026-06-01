"""Config loader: merges default.toml with OS-specific overrides."""
from __future__ import annotations

import copy
import os
import sys
import tomllib
from pathlib import Path


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning new dict."""
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _expand_paths(config: dict) -> dict:
    """Expand ~ in all string values recursively."""
    if isinstance(config, dict):
        return {k: _expand_paths(v) for k, v in config.items()}
    if isinstance(config, list):
        return [_expand_paths(v) for v in config]
    if isinstance(config, str) and config.startswith("~"):
        return str(Path(config).expanduser())
    return config


def load_config(config_dir: Path | None = None, extra_overrides: list[str] | None = None) -> dict:
    """
    Load and merge config files in order:
    1. config/default.toml
    2. config/{platform}.toml
    3. ~/.capman/config.toml (optional)
    4. Any extra named overrides from config/ (e.g. "headless")
    """
    if config_dir is None:
        if getattr(sys, "frozen", False):
            config_dir = Path(sys._MEIPASS) / "config"  # type: ignore[attr-defined]
        else:
            config_dir = Path(__file__).parent.parent / "config"

    default_path = config_dir / "default.toml"
    with open(default_path, "rb") as f:
        config = tomllib.load(f)

    # OS-specific override
    platform_map = {"darwin": "macos", "linux": "linux", "win32": "windows"}
    platform_name = platform_map.get(sys.platform, "linux")
    platform_path = config_dir / f"{platform_name}.toml"
    if platform_path.exists():
        with open(platform_path, "rb") as f:
            platform_override = tomllib.load(f)
        config = _deep_merge(config, platform_override)

    # User override (~/.capman/config.toml)
    user_config_path = Path("~/.capman/config.toml").expanduser()
    if user_config_path.exists():
        with open(user_config_path, "rb") as f:
            user_override = tomllib.load(f)
        config = _deep_merge(config, user_override)

    # Named extra overrides (e.g. "headless" → config/headless.toml)
    for name in (extra_overrides or []):
        extra_path = config_dir / f"{name}.toml"
        if extra_path.exists():
            with open(extra_path, "rb") as f:
                extra = tomllib.load(f)
            config = _deep_merge(config, extra)

    config = _expand_paths(config)
    return config


def get_data_dir(config: dict) -> Path:
    p = Path(config["core"]["data_dir"])
    p.mkdir(parents=True, exist_ok=True)
    return p
