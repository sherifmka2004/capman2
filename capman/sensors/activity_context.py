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
# Last keyboard/mouse/scroll input timestamp — used by IdleSensor.
_last_input_ts: float = 0.0


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


def record_input_activity(ts: float | None = None) -> None:
    """Any direct user input (key press, click, scroll, …) calls this.
    Cheap (one bounded float assignment) so it's safe to call on every event."""
    global _last_input_ts
    t = ts if ts is not None else time.time()
    with _lock:
        if t > _last_input_ts:
            _last_input_ts = t


def time_since_last_input() -> float:
    """Seconds since the last recorded user input. Returns +inf if no input
    has ever been seen this session (so first-ever check doesn't trigger
    spurious IDLE_END)."""
    with _lock:
        ts = _last_input_ts
    if ts <= 0.0:
        return float("inf")
    return max(0.0, time.time() - ts)


def last_input_ts() -> float:
    """Raw last-input timestamp (0.0 if never seen)."""
    with _lock:
        return _last_input_ts
