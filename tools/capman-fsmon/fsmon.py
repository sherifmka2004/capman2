#!/usr/bin/env python3
"""
capman-fsmon — privileged deep file-operation monitor (Linux only).

Companion to the capman2 daemon. The in-daemon FilesystemSensor (watchdog) can
see *creates / modifies / deletes / renames* but cannot see *file opens/reads*
nor *which process* touched a file. This helper closes that gap using the kernel:

  --backend fanotify  (default; needs CAP_SYS_ADMIN / root)
        FAN_OPEN + FAN_CLOSE_WRITE on the mounts containing the watch paths.
        Gives the acting PID → comm / exe / cmdline / TTY / ancestor chain.
  --backend audit     (fallback; needs root + auditd)
        Installs `auditctl -w <path> -p rwxa -k capman` rules, tails the audit
        log, translates openat / write / unlinkat / renameat* records.
  --backend ebpf      (fallback; needs root + bpftrace)
        Runs bpftrace/fileops.bt (opensnoop-style), parses its stdout.

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
        "vim", "nvim", "vi", "view", "emacs", "emacsclient", "nano", "micro", "helix", "hx",
        "cat", "bat", "less", "more", "head", "tail", "code", "code-insiders", "cursor",
        "subl", "sublime_text", "gedit", "kate", "kwrite", "gnome-text-editor", "xed",
    ],
    # GUI app names (from /proc comm) treated as interactive.
    "interactive_apps": [
        "code", "code-insiders", "cursor", "vscodium", "gnome-text-editor", "gedit", "kate",
        "kwrite", "xed", "obsidian", "typora", "nautilus", "dolphin", "thunar", "nemo",
        "pcmanfm", "caja", "subl", "sublime_text", "jetbrains", "idea", "pycharm", "goland",
        "webstorm", "clion", "rubymine",
    ],
    "interactive_cli": [
        "vim", "nvim", "vi", "view", "emacs", "emacsclient", "nano", "micro", "helix", "hx",
        "ed", "ex", "sed", "awk", "cp", "mv", "rm", "rmdir", "touch", "mkdir", "ln", "tee",
        "truncate", "dd", "tar", "unzip", "zip", "gzip", "gunzip", "bzip2", "xz", "7z",
        "patch", "install", "shred", "code", "subl", "open", "xdg-open", "gio",
    ],
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
        for k in ("exclude", "interactive_apps", "interactive_cli", "machine_procs", "shell_correlate_s"):
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
# /proc helpers — process identity & ancestry
# ---------------------------------------------------------------------------

_OUR_PIDS: set[int] = set()


def _proc_comm(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return ""


def _proc_exe(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return ""


def _proc_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _proc_stat(pid: int):
    """Return (ppid, tty_nr, session) from /proc/<pid>/stat, or None."""
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


def _has_tty(pid: int) -> bool:
    st = _proc_stat(pid)
    return bool(st and st[1] != 0)


def _ancestors(pid: int, limit: int = 12) -> list[int]:
    out = []
    cur = pid
    for _ in range(limit):
        st = _proc_stat(cur)
        if not st:
            break
        ppid = st[0]
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

class Attributor:
    def __init__(self, s: dict):
        self.s = s

    def classify(self, pid: int) -> tuple[str, dict]:
        """Return (verdict, actor).  verdict ∈ user|likely_user|machine|unknown."""
        if pid in _OUR_PIDS:
            return "machine", {"comm": "fsmon", "pid": pid}
        comm = _norm(_proc_comm(pid))
        exe = _proc_exe(pid)
        cmd = _proc_cmdline(pid)
        st = _proc_stat(pid)
        tty = st[1] if st else 0
        actor = {"pid": pid, "comm": comm or "?"}
        if exe:
            actor["exe"] = exe
        if cmd:
            actor["cmdline"] = cmd[:200]
        if tty:
            actor["tty"] = str(tty)

        # capman/uvicorn/etc anywhere up the chain → machine
        chain = [comm] + [_norm(_proc_comm(p)) for p in _ancestors(pid)]
        if any(c in {"capman", "uvicorn", "gunicorn"} for c in chain):
            return "machine", actor
        if comm in self.s["machine_procs"]:
            return "machine", actor
        if comm in self.s["interactive_cli"] or comm in self.s["interactive_apps"]:
            return ("user" if (tty or comm in self.s["interactive_apps"]) else "likely_user"), actor
        # python alone is ambiguous but usually scripted → machine unless it's
        # clearly an interactive REPL (`python` with no script arg + a tty)
        if comm in {"python", "python3"} and not tty:
            return "machine", actor
        # TTY-attached unknown binary that descends from a shell → likely user
        shells = {"bash", "zsh", "sh", "fish", "dash", "ksh", "tcsh", "csh"}
        if tty and any(c in shells for c in chain):
            return "likely_user", actor
        if tty:
            return "likely_user", actor
        return "unknown", actor


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
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="capman2 privileged deep file monitor")
    ap.add_argument("--backend", choices=["auto", "fanotify", "audit", "ebpf"], default="auto")
    ap.add_argument("--api", help="capman daemon base URL (default: from config or http://127.0.0.1:7331)")
    ap.add_argument("--config", help="path to capman config dir")
    ap.add_argument("--paths", nargs="*", help="override watch paths")
    ap.add_argument("--data-dir", default=os.path.expanduser("~/.capman"),
                    help="capman data dir (never recorded)")
    args = ap.parse_args()

    if sys.platform != "linux":
        print("[fsmon] only supported on Linux", file=sys.stderr)
        sys.exit(2)

    _OUR_PIDS.add(os.getpid())
    _OUR_PIDS.update(_ancestors(os.getpid()))

    s = load_settings(args.config, args.api, args.paths)
    backend = args.backend
    if backend == "auto":
        cfgd = s.get("_deep_monitor", "auto")
        backend = cfgd if cfgd in ("fanotify", "audit", "ebpf") else "fanotify"

    pf = PathFilter(s["watch_paths"], s["exclude"], args.data_dir)
    attr = Attributor(s)
    em = Emitter(s)
    if not pf.roots:
        print("[fsmon] no watch paths — nothing to do", file=sys.stderr)
        sys.exit(1)

    runners = {"fanotify": run_fanotify, "audit": run_audit, "ebpf": run_ebpf}
    order = [backend] + [b for b in ("fanotify", "audit", "ebpf") if b != backend]
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
