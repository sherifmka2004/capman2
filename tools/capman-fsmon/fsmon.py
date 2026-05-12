#!/usr/bin/env python3
"""
capman-fsmon — privileged deep file-operation monitor (Linux + macOS).

Companion to the capman2 daemon. The in-daemon FilesystemSensor (watchdog) can
see *creates / modifies / deletes / renames* but cannot see *file opens/reads*
nor *which process* touched a file. This helper closes that gap using the kernel:

  Linux  --backend fanotify  (default; needs CAP_SYS_ADMIN / root)
        FAN_OPEN + FAN_CLOSE_WRITE on the mounts containing the watch paths.
        Gives the acting PID → comm / exe / cmdline / TTY / ancestor chain.
  Linux  --backend audit     (fallback; needs root + auditd)
        Installs `auditctl -w <path> -p rwxa -k capman` rules, tails the audit
        log, translates openat / write / unlinkat / renameat* records.
  Linux  --backend ebpf      (fallback; needs root + bpftrace)
        Runs bpftrace/fileops.bt (opensnoop-style), parses its stdout.
  macOS  --backend eslogger  (default; needs root + Full Disk Access; macOS 13+)
        Streams Endpoint Security events via /usr/bin/eslogger
        (open / create / close[modified] / rename / unlink), each carrying the
        responsible process (executable path, pid, ppid, signing id, tty).
  macOS  --backend fs_usage  (fallback; needs root)
        Parses `fs_usage -w -f filesys` — opens / deletes / renames only
        (file_save events come from the in-daemon watchdog sensor).
  --backend auto  picks the right default for the current OS.

Surviving events (those attributable to *direct user action* — an editor, an
interactive viewer, a TTY-attached file tool, or a captured shell command — and
NOT a build tool / language server / indexer / daemon / capman itself) are POSTed
to the capman daemon's /events endpoint exactly like the browser extension does:

    {"type": "file_open"|"file_save"|"file_delete"|"file_rename",
     "app": <comm>, "payload": {... "attribution": "...", "actor": {...}},
     "sensor_id": "fsmon"}

Run it manually:
    sudo python3 tools/capman-fsmon/fsmon.py
or via the bundled systemd unit (see capman-fsmon.service).

See docs/FILE_MONITORING.md for the full picture.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Config — try to reuse the capman config; fall back to standalone defaults.
# ---------------------------------------------------------------------------

_DEFAULTS = {
    "api": "http://127.0.0.1:7331",
    "watch_paths": ["~/Desktop", "~/Downloads", "~/Documents", "~/code", "~/projects"],
    "exclude": [
        "**/.git/**", "**/node_modules/**", "**/.venv/**", "**/venv/**", "**/__pycache__/**",
        "**/.*cache/**", "**/.next/**", "**/dist/**", "**/build/**", "**/target/**",
        "**/.cache/**", "**/.idea/**", "**/.vscode/**",
        "*.pyc", "*.pyo", "*.lock", "*.swp", "*.swx", "*~", ".#*", "4913",
        "*.tmp", "*.crdownload", "*.part",
    ],
    # Openers we record file_open (reads) for — must be clearly interactive.
    "open_recorders": [
        "vim", "nvim", "vi", "view", "mvim", "emacs", "emacsclient", "nano", "micro", "helix", "hx",
        "cat", "bat", "less", "more", "head", "tail", "code", "code-insiders", "cursor",
        "subl", "sublime_text", "gedit", "kate", "kwrite", "gnome-text-editor", "xed",
        # macOS
        "textedit", "bbedit", "bbedit_tool", "edit", "mate", "nova", "zed", "qlmanage", "preview",
        "code helper", "code helper (renderer)", "cursor helper", "cursor helper (renderer)", "electron",
    ],
    # GUI app names (from /proc comm) treated as interactive.
    "interactive_apps": [
        "code", "code-insiders", "cursor", "vscodium", "gnome-text-editor", "gedit", "kate",
        "kwrite", "xed", "obsidian", "typora", "nautilus", "dolphin", "thunar", "nemo",
        "pcmanfm", "caja", "subl", "sublime_text", "jetbrains", "idea", "pycharm", "goland",
        "webstorm", "clion", "rubymine",
        # macOS GUI apps / their executables
        "finder", "textedit", "bbedit", "nova", "zed", "macvim", "mvim", "iterm2", "terminal",
        "warp", "hyper", "alacritty", "kitty", "wezterm", "xcode", "preview", "code helper",
        "cursor helper", "electron",
    ],
    "interactive_cli": [
        "vim", "nvim", "vi", "view", "mvim", "emacs", "emacsclient", "nano", "micro", "helix", "hx",
        "ed", "ex", "sed", "awk", "cp", "mv", "rm", "rmdir", "touch", "mkdir", "ln", "tee",
        "truncate", "dd", "tar", "unzip", "zip", "gzip", "gunzip", "bzip2", "xz", "7z",
        "patch", "install", "shred", "code", "subl", "open", "xdg-open", "gio", "ditto", "pbcopy",
    ],
    # If a process's code-signing identity (macOS, via eslogger) contains any of
    # these substrings, treat it as an interactive editor (handles GUI editors
    # that write through generic-named helper subprocesses).
    "editor_signing_id_hints": [
        "vscode", "visualstudiocode", "cursor", "sublime", "sublimetext", "bbedit", "macvim",
        "textedit", "com.apple.textedit", "nova", "panic.nova", "zed", "dev.zed", "obsidian",
        "typora", "jetbrains", "intellij", "pycharm", "goland", "webstorm", "espresso", "coderunner",
    ],
    # Endpoint Security event types to subscribe to (macOS eslogger backend).
    "es_events": ["open", "create", "close", "rename", "unlink"],
    "machine_procs": [
        "node", "deno", "bun", "webpack", "esbuild", "swc", "tsc", "tsserver", "vite", "next",
        "next-server", "rollup", "parcel", "turbo", "nx", "ng", "cargo", "rustc", "rust-analyzer",
        "gofmt", "gopls", "pip", "pip3", "uv", "poetry", "pdm", "pipenv", "conda", "mamba",
        "hatch", "npm", "yarn", "pnpm", "npx", "corepack", "pyright", "pylsp", "ruff", "black",
        "mypy", "flake8", "typescript-language-server", "eslint", "gradle", "mvn", "make",
        "cmake", "ninja", "meson", "bazel", "ccache", "git", "hg", "svn", "rclone", "borg",
        "restic", "systemd", "systemd-journald", "cron", "crond", "updatedb", "mlocate",
        "plocate", "tracker-miner-fs", "tracker-extract", "baloo_file", "dropbox", "onedrive",
        "syncthing", "nextcloud", "capman", "uvicorn", "gunicorn", "python", "python3",
        "chromedriver", "headless_shell",
    ],
    # Per-path dedup window (s) for the same (path, type).
    "dedup_window_s": 30.0,
    # Global rate cap: at most N events per window_s; excess dropped.
    "rate_cap": 40,
    "rate_window_s": 10.0,
    "shell_correlate_s": 8.0,
}


def load_settings(config_path: str | None, cli_api: str | None, cli_paths: list[str] | None):
    s = dict(_DEFAULTS)
    try:
        # Make capman importable if we're run from the repo
        repo = Path(__file__).resolve().parents[2]
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from capman.config import load_config  # type: ignore
        cfg = load_config(Path(config_path) if config_path else None)
        fs = cfg.get("sensors", {}).get("filesystem", {})
        api_port = cfg.get("api", {}).get("port", 7331)
        s["api"] = f"http://127.0.0.1:{api_port}"
        if fs.get("deep_monitor_paths"):
            s["watch_paths"] = fs["deep_monitor_paths"]
        elif fs.get("watch_paths"):
            s["watch_paths"] = fs["watch_paths"]
        for k in ("exclude", "interactive_apps", "interactive_cli", "machine_procs",
                  "open_recorders", "editor_signing_id_hints", "es_events", "shell_correlate_s"):
            if fs.get(k):
                s[k] = fs[k]
        s["_deep_monitor"] = fs.get("deep_monitor", "off")
    except Exception as e:  # standalone / no capman on path
        print(f"[fsmon] (using standalone defaults: {e})", file=sys.stderr)
        s["_deep_monitor"] = "auto"
    if cli_api:
        s["api"] = cli_api
    if cli_paths:
        s["watch_paths"] = cli_paths
    s["watch_paths"] = [str(Path(p).expanduser()) for p in s["watch_paths"]]
    # normalize proc-name sets
    for k in ("open_recorders", "interactive_apps", "interactive_cli", "machine_procs"):
        s[k] = {_norm(x) for x in s.get(k, [])}
    s["editor_signing_id_hints"] = {h.lower() for h in s.get("editor_signing_id_hints", [])}
    return s


def _norm(name: str) -> str:
    if not name:
        return ""
    n = os.path.basename(name.strip().strip('"').strip("'"))
    for suf in (".exe", ".bin"):
        if n.lower().endswith(suf):
            n = n[: -len(suf)]
    return n.lower()


# ---------------------------------------------------------------------------
# Process identity & ancestry — /proc on Linux, `ps` on macOS.
# ---------------------------------------------------------------------------

_OUR_PIDS: set[int] = set()


def _ps_field(pid: int, fmt: str) -> str:
    try:
        import subprocess
        out = subprocess.run(["ps", "-p", str(pid), "-o", fmt],
                             capture_output=True, text=True, timeout=2)
        return out.stdout.strip()
    except Exception:
        return ""


def _proc_comm(pid: int) -> str:
    if sys.platform == "linux":
        try:
            return Path(f"/proc/{pid}/comm").read_text().strip()
        except OSError:
            return ""
    if sys.platform == "darwin":
        c = _ps_field(pid, "comm=")  # full executable path on macOS
        return os.path.basename(c) if c else _ps_field(pid, "ucomm=")
    return ""


def _proc_exe(pid: int) -> str:
    if sys.platform == "linux":
        try:
            return os.readlink(f"/proc/{pid}/exe")
        except OSError:
            return ""
    if sys.platform == "darwin":
        return _ps_field(pid, "comm=")
    return ""


def _proc_cmdline(pid: int) -> str:
    if sys.platform == "linux":
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
            return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        except OSError:
            return ""
    if sys.platform == "darwin":
        return _ps_field(pid, "args=")
    return ""


def _proc_stat(pid: int):
    """Return (ppid, tty_nr, session) from /proc/<pid>/stat, or None. (Linux only.)"""
    try:
        data = Path(f"/proc/{pid}/stat").read_text()
        # comm is in parens and may contain spaces/parens — split on last ')'
        rest = data[data.rindex(")") + 2:].split()
        # fields after comm: state ppid pgrp session tty_nr ...
        ppid = int(rest[1])
        session = int(rest[3])
        tty_nr = int(rest[4])
        return ppid, tty_nr, session
    except (OSError, ValueError, IndexError):
        return None


def _parent_pid(pid: int) -> int:
    if sys.platform == "linux":
        st = _proc_stat(pid)
        return st[0] if st else 0
    if sys.platform == "darwin":
        v = _ps_field(pid, "ppid=")
        try:
            return int(v) if v else 0
        except ValueError:
            return 0
    return 0


def _proc_tty(pid: int):
    """Truthy if the process has a controlling TTY (int on Linux, str on macOS)."""
    if sys.platform == "linux":
        st = _proc_stat(pid)
        return st[1] if st else 0
    if sys.platform == "darwin":
        v = _ps_field(pid, "tty=")
        return 0 if (not v or v in ("??", "?", "-")) else v
    return 0


def _ancestors(pid: int, limit: int = 12) -> list[int]:
    out = []
    cur = pid
    for _ in range(limit):
        ppid = _parent_pid(cur)
        if ppid <= 1 or ppid == cur:
            break
        out.append(ppid)
        cur = ppid
    return out


# ---------------------------------------------------------------------------
# Shell-command correlation (read recent SHELL_COMMAND events back from the
# daemon is overkill; instead we just look at the PID ancestor chain — if the
# acting process descends from a login shell with a TTY, it's user-driven).
# ---------------------------------------------------------------------------

_SHELLS = {"bash", "zsh", "sh", "fish", "dash", "ksh", "tcsh", "csh", "-bash", "-zsh"}
_DAEMON_CHAIN = {"capman", "uvicorn", "gunicorn"}


class Attributor:
    def __init__(self, s: dict):
        self.s = s

    def classify(self, pid: int) -> tuple[str, dict]:
        """Resolve process info for `pid` (/proc on Linux, `ps` on macOS) then classify."""
        if pid in _OUR_PIDS:
            return "machine", {"comm": "fsmon", "pid": pid}
        info = {
            "pid": pid,
            "comm": _proc_comm(pid),
            "exe": _proc_exe(pid),
            "cmdline": _proc_cmdline(pid),
            "tty": _proc_tty(pid),
            "ppid": _parent_pid(pid),
            "signing_id": "",
            "chain": [_norm(_proc_comm(p)) for p in _ancestors(pid)],
        }
        return self.classify_info(info)

    def classify_info(self, info: dict) -> tuple[str, dict]:
        """Classify from a pre-resolved process-info dict (used by the eslogger backend).

        Returns (verdict, actor).  verdict ∈ user|likely_user|machine|unknown.
        """
        pid = info.get("pid")
        if pid is not None and pid in _OUR_PIDS:
            return "machine", {"comm": "fsmon", "pid": pid}
        comm = _norm(info.get("comm", "") or "")
        exe = info.get("exe", "") or ""
        exe_base = _norm(exe) if exe else ""
        cmd = info.get("cmdline", "") or ""
        tty = info.get("tty") or 0
        sid = (info.get("signing_id") or "").lower()

        actor: dict = {"comm": comm or exe_base or "?"}
        if pid is not None:
            actor["pid"] = pid
        if info.get("ppid"):
            actor["ppid"] = info["ppid"]
        if exe:
            actor["exe"] = exe
        if cmd:
            actor["cmdline"] = cmd[:200]
        if tty:
            actor["tty"] = str(tty)
        if sid:
            actor["signing_id"] = sid

        names = {n for n in (comm, exe_base) if n}
        chain = list(names) + list(info.get("chain", []))

        # capman / uvicorn anywhere in the picture → machine (avoid feedback loops)
        if any(c in _DAEMON_CHAIN for c in chain):
            return "machine", actor
        if "capman" in cmd or "uvicorn" in cmd or "/capman" in exe:
            return "machine", actor
        if names & set(self.s["machine_procs"]):
            return "machine", actor
        # GUI editors on macOS write through generic-named helpers — recognize them
        # by code-signing identity.
        if sid and any(h in sid for h in self.s.get("editor_signing_id_hints", set())):
            return "user", actor
        if names & set(self.s["interactive_apps"]):
            return "user", actor
        if names & set(self.s["interactive_cli"]):
            return ("user" if tty else "likely_user"), actor
        # bare python is usually a script, not interactive
        if (comm in {"python", "python3"} or exe_base in {"python", "python3"}) and not tty:
            return "machine", actor
        # TTY-attached unknown binary, especially under a shell → likely the user
        if tty and any(c in _SHELLS for c in chain):
            return "likely_user", actor
        if tty:
            return "likely_user", actor
        return "unknown", actor

    def is_open_recorder(self, actor: dict) -> bool:
        """Should we record a *file_open* (read) for this actor? Be conservative."""
        comm = _norm(actor.get("comm", "") or "")
        exe_base = _norm(actor.get("exe", "") or "")
        sid = (actor.get("signing_id") or "").lower()
        if comm in self.s["open_recorders"] or exe_base in self.s["open_recorders"]:
            return True
        if comm in self.s["interactive_apps"] or exe_base in self.s["interactive_apps"]:
            return True
        if sid and any(h in sid for h in self.s.get("editor_signing_id_hints", set())):
            return True
        return False


# ---------------------------------------------------------------------------
# Path filtering
# ---------------------------------------------------------------------------

class PathFilter:
    def __init__(self, watch_paths: list[str], exclude: list[str], data_dir: str):
        self.roots = [os.path.realpath(p) for p in watch_paths]
        self.exclude = exclude
        self.data_dir = os.path.realpath(data_dir)

    def wanted(self, path: str) -> bool:
        if not path:
            return False
        try:
            rp = os.path.realpath(path)
        except OSError:
            rp = os.path.abspath(path)
        if rp == self.data_dir or rp.startswith(self.data_dir + os.sep):
            return False
        if not any(rp == r or rp.startswith(r + os.sep) for r in self.roots):
            return False
        name = os.path.basename(rp)
        parts = rp.split(os.sep)
        for pat in self.exclude:
            if pat.startswith("**/") and pat.endswith("/**"):
                seg = pat[3:-3]
                if any(fnmatch.fnmatch(p, seg) for p in parts):
                    return False
            elif "/" in pat:
                if fnmatch.fnmatch(rp, pat) or fnmatch.fnmatch(rp, "*/" + pat):
                    return False
            else:
                if fnmatch.fnmatch(name, pat):
                    return False
        return True


# ---------------------------------------------------------------------------
# Emitter — rate-limited, deduped POST to the daemon
# ---------------------------------------------------------------------------

class Emitter:
    def __init__(self, s: dict):
        self.api = s["api"].rstrip("/")
        self.dedup_window = float(s["dedup_window_s"])
        self.rate_cap = int(s["rate_cap"])
        self.rate_window = float(s["rate_window_s"])
        self._last: dict[tuple, float] = {}
        self._bucket: list[float] = []
        self.sent = 0
        self.dropped_rate = 0
        self.dropped_dup = 0

    def _rate_ok(self) -> bool:
        now = time.time()
        self._bucket = [t for t in self._bucket if now - t < self.rate_window]
        if len(self._bucket) >= self.rate_cap:
            self.dropped_rate += 1
            return False
        self._bucket.append(now)
        return True

    def emit(self, etype: str, payload: dict, app: str):
        key = (etype, payload.get("path") or payload.get("dest_path", ""))
        now = time.time()
        if now - self._last.get(key, 0.0) < self.dedup_window:
            self.dropped_dup += 1
            return
        if not self._rate_ok():
            return
        self._last[key] = now
        if len(self._last) > 8192:
            cutoff = now - max(self.dedup_window, 60)
            self._last = {k: v for k, v in self._last.items() if v >= cutoff}
        body = json.dumps({
            "type": etype, "app": app or "fsmon", "window_title": "",
            "payload": payload, "sensor_id": "fsmon",
        }).encode("utf-8")
        try:
            req = urllib.request.Request(self.api + "/events", data=body,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=2).read()
            self.sent += 1
        except Exception:
            pass  # daemon down / restarting — just drop


# ---------------------------------------------------------------------------
# Backend: fanotify (ctypes)
# ---------------------------------------------------------------------------

def run_fanotify(s: dict, pf: PathFilter, attr: Attributor, em: Emitter):
    import ctypes
    import struct

    libc = ctypes.CDLL("libc.so.6", use_errno=True)

    # constants (from <sys/fanotify.h> / <fcntl.h>)
    FAN_CLOEXEC        = 0x00000001
    FAN_CLASS_NOTIF    = 0x00000000
    FAN_NONBLOCK       = 0x00000002
    O_RDONLY           = 0
    O_LARGEFILE        = 0o0100000
    FAN_MARK_ADD       = 0x00000001
    FAN_MARK_MOUNT     = 0x00000010
    FAN_OPEN           = 0x00000020
    FAN_CLOSE_WRITE    = 0x00000008
    AT_FDCWD           = -100

    fan_fd = libc.fanotify_init(FAN_CLOEXEC | FAN_CLASS_NOTIF, O_RDONLY | O_LARGEFILE)
    if fan_fd < 0:
        err = ctypes.get_errno()
        raise OSError(err, f"fanotify_init failed ({os.strerror(err)}) — need root / CAP_SYS_ADMIN")

    # Mark the mount point of each watch path (fanotify classic mode watches
    # whole mounts; we filter by prefix afterwards).
    mounts = set()
    for root in pf.roots:
        m = root
        try:
            dev = os.stat(m).st_dev
            while m != "/" and os.stat(os.path.dirname(m)).st_dev == dev:
                m = os.path.dirname(m)
        except OSError:
            pass
        mounts.add(m)
    marked = 0
    for m in mounts:
        rc = libc.fanotify_mark(fan_fd, FAN_MARK_ADD | FAN_MARK_MOUNT,
                                ctypes.c_uint64(FAN_OPEN | FAN_CLOSE_WRITE),
                                AT_FDCWD, m.encode("utf-8"))
        if rc < 0:
            err = ctypes.get_errno()
            print(f"[fsmon] fanotify_mark({m}) failed: {os.strerror(err)}", file=sys.stderr)
        else:
            marked += 1
            print(f"[fsmon] fanotify watching mount {m}", file=sys.stderr)
    if not marked:
        os.close(fan_fd)
        raise OSError("no mounts could be marked")

    # struct fanotify_event_metadata: __u32 event_len; __u8 vers; __u8 reserved;
    #                                 __u16 metadata_len; __u64 mask; __s32 fd; __s32 pid;
    META_FMT = "IBBHQii"
    META_SIZE = struct.calcsize(META_FMT)  # 24
    open_recorders = s["open_recorders"]
    interactive_apps = s["interactive_apps"]

    print(f"[fsmon] backend=fanotify  api={em.api}  watch_roots={pf.roots}", file=sys.stderr)
    last_stats = time.time()
    while True:
        try:
            buf = os.read(fan_fd, 64 * 1024)
        except InterruptedError:
            continue
        except OSError as e:
            print(f"[fsmon] read error: {e}", file=sys.stderr)
            break
        off = 0
        while off + META_SIZE <= len(buf):
            event_len, vers, _res, _mlen, mask, fd, pid = struct.unpack_from(META_FMT, buf, off)
            off += event_len if event_len >= META_SIZE else META_SIZE
            path = ""
            if fd >= 0:
                try:
                    path = os.readlink(f"/proc/self/fd/{fd}")
                finally:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            if not path or not pf.wanted(path):
                continue
            if pid in _OUR_PIDS:
                continue
            verdict, actor = attr.classify(pid)
            comm = actor.get("comm", "")
            if mask & FAN_CLOSE_WRITE:
                # a write-close = a save. record user + likely_user.
                if verdict not in ("user", "likely_user"):
                    continue
                payload = {"path": path, "extension": os.path.splitext(path)[1],
                           "attribution": verdict, "actor": actor}
                try:
                    payload["size_bytes"] = os.path.getsize(path)
                    payload["mtime"] = os.path.getmtime(path)
                except OSError:
                    pass
                em.emit("file_save", payload, comm)
            elif mask & FAN_OPEN:
                # a (mostly read) open — only record for clearly interactive openers,
                # otherwise an editor/LSP/grep storm floods us.
                if not (comm in open_recorders or comm in interactive_apps):
                    continue
                if verdict not in ("user", "likely_user"):
                    continue
                payload = {"path": path, "extension": os.path.splitext(path)[1],
                           "attribution": verdict, "actor": actor, "mode": "open"}
                em.emit("file_open", payload, comm)
        now = time.time()
        if now - last_stats >= 120:
            print(f"[fsmon] sent={em.sent} dup_dropped={em.dropped_dup} "
                  f"rate_dropped={em.dropped_rate}", file=sys.stderr)
            last_stats = now


# ---------------------------------------------------------------------------
# Backend: auditd
# ---------------------------------------------------------------------------

def run_audit(s: dict, pf: PathFilter, attr: Attributor, em: Emitter):
    import shutil
    import subprocess
    import re

    if not shutil.which("auditctl"):
        raise OSError("auditctl not found — install auditd")
    # add watches
    for root in pf.roots:
        if os.path.isdir(root):
            subprocess.run(["auditctl", "-w", root, "-p", "rwxa", "-k", "capman_fsmon"],
                           capture_output=True)
            print(f"[fsmon] audit watching {root}", file=sys.stderr)

    log = "/var/log/audit/audit.log"
    if not os.path.exists(log):
        raise OSError(f"{log} not found")
    print(f"[fsmon] backend=audit  tailing {log}", file=sys.stderr)

    SYSCALL_MAP = {  # x86-64 numbers; names also accepted via syscall= field text
        "openat": "open", "open": "open", "creat": "open", "openat2": "open",
        "write": "save", "pwrite64": "save", "writev": "save", "ftruncate": "save",
        "unlink": "delete", "unlinkat": "delete",
        "rename": "rename", "renameat": "rename", "renameat2": "rename",
    }
    rec: dict = {}

    def flush_record():
        if not rec:
            return
        action = rec.get("action")
        if not action:
            return
        names = rec.get("names", [])
        if not names:
            return
        pid = rec.get("pid")
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            pid = -1
        verdict, actor = attr.classify(pid) if pid > 0 else ("unknown", {})
        comm = actor.get("comm", rec.get("comm", "")) if isinstance(actor, dict) else rec.get("comm", "")
        if action == "rename" and len(names) >= 2:
            src = next((n for n, t in names if t in ("DELETE", "PARENT", "UNKNOWN")), names[0][0])
            dst = next((n for n, t in names if t == "CREATE"), names[-1][0])
            if not pf.wanted(dst) and not pf.wanted(src):
                return
            if verdict not in ("user", "likely_user"):
                return
            em.emit("file_rename", {"src_path": src, "dest_path": dst,
                                    "extension": os.path.splitext(dst)[1],
                                    "attribution": verdict, "actor": actor}, comm)
            return
        for name, ntype in names:
            if ntype in ("PARENT",):
                continue
            if not pf.wanted(name):
                continue
            if action == "open":
                if comm not in s["open_recorders"] and comm not in s["interactive_apps"]:
                    continue
            if verdict not in ("user", "likely_user"):
                continue
            etype = {"open": "file_open", "save": "file_save", "delete": "file_delete"}[action]
            payload = {"path": name, "extension": os.path.splitext(name)[1],
                       "attribution": verdict, "actor": actor}
            em.emit(etype, payload, comm)

    proc = subprocess.Popen(["tail", "-n", "0", "-F", log], stdout=subprocess.PIPE, text=True)
    try:
        for line in proc.stdout:  # type: ignore
            line = line.strip()
            if line.startswith("type=SYSCALL"):
                flush_record()
                rec = {"names": []}
                m = re.search(r"syscall=(\S+)", line)
                if m:
                    sc = m.group(1)
                    rec["action"] = SYSCALL_MAP.get(sc)
                m = re.search(r"\bpid=(\d+)", line)
                if m:
                    rec["pid"] = m.group(1)
                m = re.search(r"comm=\"?([^\"\s]+)\"?", line)
                if m:
                    rec["comm"] = _norm(m.group(1))
                if "success=no" in line:
                    rec["action"] = None
            elif line.startswith("type=PATH") and rec:
                m = re.search(r'name="([^"]+)"', line)
                t = re.search(r"nametype=(\S+)", line)
                if m:
                    rec.setdefault("names", []).append((m.group(1), t.group(1) if t else "NORMAL"))
            elif line.startswith("type=EOE") and rec:
                flush_record()
                rec = {}
    finally:
        proc.terminate()


# ---------------------------------------------------------------------------
# Backend: bpftrace (eBPF)
# ---------------------------------------------------------------------------

def run_ebpf(s: dict, pf: PathFilter, attr: Attributor, em: Emitter):
    import shutil
    import subprocess

    bt = shutil.which("bpftrace")
    if not bt:
        raise OSError("bpftrace not found — install bpftrace")
    script = Path(__file__).with_name("bpftrace") / "fileops.bt"
    if not script.exists():
        raise OSError(f"{script} missing")
    print(f"[fsmon] backend=ebpf  running {script}", file=sys.stderr)
    proc = subprocess.Popen([bt, str(script)], stdout=subprocess.PIPE, text=True)
    try:
        for line in proc.stdout:  # type: ignore
            line = line.rstrip("\n")
            # expected: "<KIND>\t<pid>\t<comm>\t<path>"
            if "\t" not in line:
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            kind, pid_s, comm, path = parts[0], parts[1], parts[2], "\t".join(parts[3:])
            if kind not in ("OPEN", "WRITE"):
                continue
            try:
                pid = int(pid_s)
            except ValueError:
                continue
            if not pf.wanted(path) or pid in _OUR_PIDS:
                continue
            verdict, actor = attr.classify(pid)
            cn = actor.get("comm", _norm(comm))
            if kind == "OPEN":
                if cn not in s["open_recorders"] and cn not in s["interactive_apps"]:
                    continue
                if verdict not in ("user", "likely_user"):
                    continue
                em.emit("file_open", {"path": path, "extension": os.path.splitext(path)[1],
                                      "attribution": verdict, "actor": actor, "mode": "open"}, cn)
            else:  # WRITE
                if verdict not in ("user", "likely_user"):
                    continue
                em.emit("file_save", {"path": path, "extension": os.path.splitext(path)[1],
                                      "attribution": verdict, "actor": actor}, cn)
    finally:
        proc.terminate()


# ---------------------------------------------------------------------------
# Backend: macOS Endpoint Security via /usr/bin/eslogger (macOS 13+)
# ---------------------------------------------------------------------------

def _dig(d, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
        if d is None:
            return None
    return d


def _es_proc_info(msg: dict) -> dict:
    """Pull a process-info dict out of an eslogger JSON message (schema varies by OS version)."""
    p = msg.get("process") or msg.get("proc") or {}
    if not isinstance(p, dict):
        p = {}
    exe = _dig(p, "executable", "path")
    if not exe:
        ex = p.get("executable")
        exe = ex.get("path") if isinstance(ex, dict) else (ex if isinstance(ex, str) else "")
    exe = exe or ""
    sid = p.get("signing_id") or p.get("team_id") or ""
    tty = _dig(p, "tty", "path") or (p.get("tty") if isinstance(p.get("tty"), str) else "") or ""
    ppid = p.get("ppid") or p.get("original_ppid") or _dig(p, "parent_audit_token", "pid")
    pid = None
    at = p.get("audit_token")
    if isinstance(at, dict):
        pid = at.get("pid")
    elif isinstance(at, (list, tuple)) and len(at) >= 6:
        # audit_token_t fields: [auid, euid, egid, ruid, rgid, pid, asid, pidversion]
        try:
            pid = int(at[5])
        except (TypeError, ValueError):
            pid = None
    if pid is None:
        pid = p.get("pid")
    return {
        "pid": pid,
        "comm": os.path.basename(exe) if exe else "",
        "exe": exe,
        "cmdline": "",
        "tty": tty,
        "ppid": ppid,
        "signing_id": sid,
        "chain": [],
    }


def _es_extract(msg: dict):
    """Return (etype | None, src_path, dest_path, proc_info) for one eslogger message."""
    ev = msg.get("event")
    if not isinstance(ev, dict):
        return None, None, None, {}
    info = _es_proc_info(msg)
    if "open" in ev:
        path = _dig(ev, "open", "file", "path") or _dig(ev, "open", "path") or ""
        return "file_open", path, "", info
    if "create" in ev:
        c = ev.get("create") or {}
        dest = c.get("destination") or {}
        path = _dig(dest, "existing_file", "path")
        if not path:
            d = _dig(dest, "new_path", "dir", "path")
            fn = _dig(dest, "new_path", "filename")
            if d:
                path = os.path.join(d, fn) if fn else d
        path = path or _dig(c, "target", "path") or ""
        return "file_open", path, "", info
    if "close" in ev:
        c = ev.get("close") or {}
        if not c.get("modified"):
            return None, None, None, info
        path = _dig(c, "target", "path") or _dig(c, "file", "path") or ""
        return "file_save", path, "", info
    if "rename" in ev:
        r = ev.get("rename") or {}
        src = _dig(r, "source", "path") or ""
        dest = r.get("destination") or {}
        dst = _dig(dest, "existing_file", "path")
        if not dst:
            d = _dig(dest, "new_path", "dir", "path")
            fn = _dig(dest, "new_path", "filename")
            if d:
                dst = os.path.join(d, fn) if fn else d
        return "file_rename", src, dst or "", info
    if "unlink" in ev:
        path = _dig(ev, "unlink", "target", "path") or ""
        return "file_delete", path, "", info
    return None, None, None, info


def run_eslogger(s: dict, pf: PathFilter, attr: Attributor, em: Emitter):
    import shutil
    import subprocess

    eslogger = shutil.which("eslogger") or "/usr/bin/eslogger"
    if not os.path.exists(eslogger):
        raise OSError("eslogger not found — needs macOS 13+ (Ventura)")
    events = list(s.get("es_events", ["open", "create", "close", "rename", "unlink"]))
    print(f"[fsmon] backend=eslogger  events={events}  api={em.api}", file=sys.stderr)
    print("[fsmon] note: the process running eslogger needs root AND Full Disk Access "
          "(System Settings → Privacy & Security → Full Disk Access).", file=sys.stderr)
    try:
        proc = subprocess.Popen([eslogger, *events], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, bufsize=1)
    except OSError as e:
        raise OSError(f"could not launch eslogger: {e}")
    last_stats = time.time()
    try:
        for line in proc.stdout:  # type: ignore
            line = line.strip()
            if not line or line[0] not in "{[":
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, list):
                continue
            kind, src, dst, info = _es_extract(msg)
            if kind is None:
                continue
            primary = dst or src
            wanted = (primary and pf.wanted(primary)) or (kind == "file_rename" and src and pf.wanted(src))
            if not wanted:
                continue
            pid = info.get("pid")
            if pid is not None and pid in _OUR_PIDS:
                continue
            verdict, actor = attr.classify_info(info)
            if verdict not in ("user", "likely_user"):
                continue
            comm = actor.get("comm", "")
            if kind == "file_open":
                if verdict != "user" or not attr.is_open_recorder(actor):
                    continue
                em.emit("file_open", {"path": primary, "extension": os.path.splitext(primary)[1],
                                      "attribution": verdict, "actor": actor, "mode": "open"}, comm)
            elif kind == "file_save":
                payload = {"path": primary, "extension": os.path.splitext(primary)[1],
                           "attribution": verdict, "actor": actor}
                try:
                    payload["size_bytes"] = os.path.getsize(primary)
                    payload["mtime"] = os.path.getmtime(primary)
                except OSError:
                    pass
                em.emit("file_save", payload, comm)
            elif kind == "file_rename":
                em.emit("file_rename", {"src_path": src or "", "dest_path": dst or "",
                                        "extension": os.path.splitext(dst or src or "")[1],
                                        "attribution": verdict, "actor": actor}, comm)
            elif kind == "file_delete":
                em.emit("file_delete", {"path": primary, "extension": os.path.splitext(primary)[1],
                                        "attribution": verdict, "actor": actor}, comm)
            now = time.time()
            if now - last_stats >= 120:
                print(f"[fsmon] sent={em.sent} dup_dropped={em.dropped_dup} "
                      f"rate_dropped={em.dropped_rate}", file=sys.stderr)
                last_stats = now
        # stdout closed → eslogger exited
        rc = proc.wait()
        err = (proc.stderr.read() if proc.stderr else "") or ""
        msg = err.strip()[:300] if err.strip() else f"exit {rc}"
        raise OSError(f"eslogger ended ({msg})")
    finally:
        try:
            proc.terminate()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Backend: macOS fs_usage (fallback — least precise; saves come from the
# in-daemon watchdog sensor, so this only adds opens / deletes / renames).
# ---------------------------------------------------------------------------

def run_fs_usage(s: dict, pf: PathFilter, attr: Attributor, em: Emitter):
    import shutil
    import subprocess
    import re

    if not shutil.which("fs_usage"):
        raise OSError("fs_usage not found")
    print("[fsmon] backend=fs_usage  (least precise — file_save events come from the "
          "in-daemon filesystem sensor; needs root)", file=sys.stderr)
    proc = subprocess.Popen(["fs_usage", "-w", "-f", "filesys"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    tail_re = re.compile(r"(.+)\.(\d+)\s*$")
    try:
        for line in proc.stdout:  # type: ignore
            line = line.rstrip("\n")
            m = tail_re.search(line)
            if not m:
                continue
            pname, pid_s = m.group(1).strip(), m.group(2)
            try:
                pid = int(pid_s)
            except ValueError:
                continue
            if pid in _OUR_PIDS:
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            op = parts[1].lower()
            paths = [tok for tok in parts[2:] if tok.startswith("/")]
            if op.startswith("open") and not op.startswith("opendir"):
                if not paths or not pf.wanted(paths[0]):
                    continue
                pn = _norm(pname)
                if pn not in s["open_recorders"] and pn not in s["interactive_apps"]:
                    continue
                verdict, actor = attr.classify(pid)
                if verdict != "user" and not attr.is_open_recorder(actor):
                    continue
                if verdict not in ("user", "likely_user"):
                    continue
                em.emit("file_open", {"path": paths[0], "extension": os.path.splitext(paths[0])[1],
                                      "attribution": verdict, "actor": actor, "mode": "open"},
                        actor.get("comm", pn))
            elif op in ("unlink", "rmdir", "unlink_nocancel"):
                if not paths or not pf.wanted(paths[0]):
                    continue
                verdict, actor = attr.classify(pid)
                if verdict not in ("user", "likely_user"):
                    continue
                em.emit("file_delete", {"path": paths[0], "extension": os.path.splitext(paths[0])[1],
                                        "attribution": verdict, "actor": actor}, actor.get("comm", _norm(pname)))
            elif op.startswith("rename"):
                if len(paths) < 2 or not (pf.wanted(paths[0]) or pf.wanted(paths[1])):
                    continue
                verdict, actor = attr.classify(pid)
                if verdict not in ("user", "likely_user"):
                    continue
                em.emit("file_rename", {"src_path": paths[0], "dest_path": paths[1],
                                        "extension": os.path.splitext(paths[1] or paths[0])[1],
                                        "attribution": verdict, "actor": actor}, actor.get("comm", _norm(pname)))
    finally:
        proc.terminate()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

_LINUX_BACKENDS = ("fanotify", "audit", "ebpf")
_MACOS_BACKENDS = ("eslogger", "fs_usage")
_ALL_BACKENDS = _LINUX_BACKENDS + _MACOS_BACKENDS


def main():
    ap = argparse.ArgumentParser(description="capman2 privileged deep file monitor")
    ap.add_argument("--backend", choices=("auto",) + _ALL_BACKENDS, default="auto",
                    help="Linux: fanotify|audit|ebpf · macOS: eslogger|fs_usage · auto picks per-OS")
    ap.add_argument("--api", help="capman daemon base URL (default: from config or http://127.0.0.1:7331)")
    ap.add_argument("--config", help="path to capman config dir")
    ap.add_argument("--paths", nargs="*", help="override watch paths")
    ap.add_argument("--data-dir", default=os.path.expanduser("~/.capman"),
                    help="capman data dir (never recorded)")
    args = ap.parse_args()

    if sys.platform not in ("linux", "darwin"):
        print("[fsmon] only Linux and macOS are supported", file=sys.stderr)
        sys.exit(2)
    is_mac = sys.platform == "darwin"
    os_backends = _MACOS_BACKENDS if is_mac else _LINUX_BACKENDS

    _OUR_PIDS.add(os.getpid())
    _OUR_PIDS.update(_ancestors(os.getpid()))

    s = load_settings(args.config, args.api, args.paths)
    backend = args.backend
    if backend == "auto":
        cfgd = s.get("_deep_monitor", "auto")
        backend = cfgd if cfgd in os_backends else os_backends[0]
    elif backend not in os_backends:
        print(f"[fsmon] backend '{backend}' is not available on {sys.platform}; "
              f"using '{os_backends[0]}'", file=sys.stderr)
        backend = os_backends[0]

    pf = PathFilter(s["watch_paths"], s["exclude"], args.data_dir)
    attr = Attributor(s)
    em = Emitter(s)
    if not pf.roots:
        print("[fsmon] no watch paths — nothing to do", file=sys.stderr)
        sys.exit(1)

    runners = {
        "fanotify": run_fanotify, "audit": run_audit, "ebpf": run_ebpf,
        "eslogger": run_eslogger, "fs_usage": run_fs_usage,
    }
    order = [backend] + [b for b in os_backends if b != backend]
    last_err = None
    for b in order:
        try:
            runners[b](s, pf, attr, em)
            return
        except KeyboardInterrupt:
            print("\n[fsmon] stopped.", file=sys.stderr)
            return
        except Exception as e:
            print(f"[fsmon] backend '{b}' unavailable: {e}", file=sys.stderr)
            last_err = e
    print(f"[fsmon] no usable backend. last error: {last_err}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
