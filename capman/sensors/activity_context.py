"""
Shared in-process activity context — used to attribute filesystem events to
*direct user action* rather than background machine churn.

Two producers publish into this module:
  - WindowSensor   → set_foreground(app, title)  on every focus change
  - PipelineRunner → record_shell_command(...)   when it ingests a SHELL_COMMAND event

One consumer reads it:
  - FilesystemSensor → get_foreground() / recent_commands()  to classify file ops

Everything is guarded by a lock so it is safe to call from the watchdog thread.
"""
from __future__ import annotations

import threading
import time
from collections import deque

_lock = threading.Lock()
_foreground: tuple[str, str, float] = ("", "", 0.0)  # (app, title, since_ts)
# Most-recent interactive shell commands: dicts {ts, command, cwd, pid, command_id}
_recent_commands: "deque[dict]" = deque(maxlen=128)


def set_foreground(app: str, title: str = "") -> None:
    global _foreground
    with _lock:
        _foreground = (app or "", title or "", time.time())


def get_foreground() -> tuple[str, str, float]:
    """Return (app, title, since_ts) of the currently-focused window."""
    with _lock:
        return _foreground


def record_shell_command(command: str, cwd: str = "", pid: int | None = None,
                         command_id: str = "", ts: float | None = None) -> None:
    if not command:
        return
    with _lock:
        _recent_commands.append({
            "ts": ts if ts is not None else time.time(),
            "command": command,
            "cwd": cwd or "",
            "pid": pid,
            "command_id": command_id or "",
        })


def recent_commands(within_s: float) -> list[dict]:
    """Shell commands seen in the last `within_s` seconds, newest last."""
    cutoff = time.time() - within_s
    with _lock:
        return [c for c in _recent_commands if c["ts"] >= cutoff]
