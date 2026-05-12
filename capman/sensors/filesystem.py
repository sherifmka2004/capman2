"""
Filesystem sensor — records file operations that the *user directly performed*.

Built on `watchdog`, this sensor captures create / modify / delete / rename
events under the configured watch paths, but only emits them when the change is
attributable to direct user action (editing in an editor, an interactive shell
command, a focused GUI file manager, ...) — not background churn from build
tools, package managers, language servers, watchers or the capman daemon itself.

For text files it also captures a unified diff of *what changed* (snapshot-based,
or `git diff` when the file lives in a repo), emitted as a CODE_DIFF event.

A privileged companion (`tools/capman-fsmon`) adds true file *open/read* capture
with PID-level process attribution; this sensor is the no-root, cross-platform
baseline.
"""
from __future__ import annotations

import asyncio
import difflib
import fnmatch
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import ClassVar

from capman.events import Event, EventType
from capman.sensors.activity_context import get_foreground, recent_commands
from capman.sensors.base import BaseSensor

logger = logging.getLogger(__name__)

# --- defaults (overridable via [sensors.filesystem] in config) ---------------

DEFAULT_EXCLUDE = [
    "**/.git/**", "**/node_modules/**", "**/.venv/**", "**/venv/**",
    "**/__pycache__/**", "**/.*cache/**", "**/.next/**", "**/dist/**",
    "**/build/**", "**/target/**", "**/.cache/**", "**/.idea/**", "**/.vscode/**",
    "*.pyc", "*.pyo", "*.lock", "*.swp", "*.swpx", "*.swx", "*~", ".#*",
    "4913", "*.tmp", "*.temp", "*.crdownload", "*.part", "*.partial",
]
DEFAULT_INTERACTIVE_APPS = [
    "code", "code - insiders", "cursor", "vim", "nvim", "neovim", "emacs", "nano",
    "micro", "helix", "sublime_text", "sublime text", "gedit", "kate", "kwrite",
    "geany", "idea", "intellij idea", "pycharm", "goland", "webstorm", "rubymine",
    "clion", "xcode", "textedit", "obsidian", "typora", "notepad++", "vscodium",
    "finder", "nautilus", "files", "dolphin", "thunar", "nemo", "pcmanfm", "caja",
    "kitty", "alacritty", "wezterm", "ghostty", "terminal", "iterm2", "iterm",
    "gnome-terminal", "konsole", "xterm", "urxvt", "terminator", "tilix",
    "windows terminal", "hyper", "warp",
]
DEFAULT_INTERACTIVE_CLI = [
    "vim", "nvim", "vi", "view", "emacs", "emacsclient", "nano", "micro", "helix", "hx",
    "ed", "ex", "cat", "bat", "less", "more", "head", "tail", "sed", "awk", "perl",
    "cp", "mv", "rm", "rmdir", "touch", "mkdir", "ln", "tee", "truncate", "dd",
    "tar", "unzip", "zip", "gzip", "gunzip", "bzip2", "xz", "7z", "rsync",
    "code", "subl", "open", "xdg-open", "gio", "shred", "install", "patch", "git-apply",
]
DEFAULT_MACHINE_PROCS = [
    "node", "deno", "bun", "webpack", "esbuild", "swc", "tsc", "tsserver", "vite",
    "next", "next-server", "rollup", "parcel", "turbo", "nx", "ng",
    "cargo", "rustc", "rust-analyzer", "go", "gopls", "gofmt",
    "pip", "pip3", "uv", "poetry", "pdm", "pipenv", "conda", "mamba", "hatch",
    "npm", "yarn", "pnpm", "npx", "corepack",
    "pyright", "pylsp", "pyls", "ruff", "black", "mypy", "flake8",
    "typescript-language-server", "vscode-eslint-language-server", "eslint",
    "java", "gradle", "mvn", "maven", "kotlin-language-server",
    "git", "git-remote-https", "hg", "svn",
    "make", "cmake", "ninja", "meson", "bazel", "buck", "ccache",
    "rclone", "borg", "restic", "duplicity",
    "systemd", "systemd-journald", "cron", "crond", "anacron", "atd",
    "updatedb", "mlocate", "plocate", "tracker-miner-fs", "tracker-extract",
    "tracker3", "baloo_file", "baloo_file_extractor", "mds", "mds_stores",
    "mdworker", "mdworker_shared", "mdsync", "fseventsd", "spotlight",
    "dropbox", "onedrive", "syncthing", "insync", "nextcloud", "megasync",
    "capman", "uvicorn", "gunicorn", "chromedriver", "headless_shell",
]

# Max bytes of diff text persisted in an event payload
_DIFF_TEXT_CAP = 8000


def _norm_proc(token: str) -> str:
    """Normalize an argv[0] / process name to its bare command basename."""
    if not token:
        return ""
    t = token.strip().strip('"').strip("'")
    # strip path
    t = os.path.basename(t)
    # strip common suffixes
    for suf in (".exe", ".bin"):
        if t.lower().endswith(suf):
            t = t[: -len(suf)]
    return t.lower()


def _command_head(command: str) -> str:
    """First *real* token of a shell command line (skips `env VAR=x`, `sudo`, `nohup`, `time`)."""
    skip = {"sudo", "nohup", "time", "command", "exec", "nice", "ionice", "stdbuf", "env", "doas"}
    for raw in command.replace("\t", " ").split():
        tok = raw
        if "=" in tok and "/" not in tok.split("=", 1)[0]:
            # VAR=value prefix
            continue
        bn = _norm_proc(tok)
        if bn in skip:
            continue
        return bn
    return ""


class FilesystemSensor(BaseSensor):
    sensor_id: ClassVar[str] = "filesystem"
    platform_support: ClassVar[set[str]] = {"*"}

    async def run(self) -> None:
        try:
            from watchdog.observers import Observer  # type: ignore
            from watchdog.events import FileSystemEventHandler  # type: ignore
        except ImportError:
            logger.warning("watchdog not available, filesystem sensor disabled")
            return

        cfg = self.config.get("sensors", {}).get("filesystem", {})
        watch_paths = [str(Path(p).expanduser()) for p in cfg.get("watch_paths", [])]
        self._allowed_ext = set(cfg.get("extensions", []))
        self._exclude = list(cfg.get("exclude", DEFAULT_EXCLUDE))
        self._debounce_s = max(0.1, float(cfg.get("debounce_ms", 1200)) / 1000.0)
        self._capture_diffs = bool(cfg.get("capture_diffs", True))
        self._diff_max_bytes = int(cfg.get("diff_max_bytes", 1024 * 1024))
        self._snapshot_dir = Path(cfg.get("snapshot_dir", "~/.capman/file_snapshots")).expanduser()
        self._user_only = bool(cfg.get("user_only", True))
        self._keep_unknown = bool(cfg.get("keep_unknown", False))
        self._fg_grace = float(cfg.get("foreground_window_grace_s", 4))
        self._shell_grace = float(cfg.get("shell_correlate_s", 8))
        self._interactive_apps = {a.lower() for a in cfg.get("interactive_apps", DEFAULT_INTERACTIVE_APPS)}
        self._interactive_cli = {_norm_proc(c) for c in cfg.get("interactive_cli", DEFAULT_INTERACTIVE_CLI)}
        self._machine_procs = {_norm_proc(c) for c in cfg.get("machine_procs", DEFAULT_MACHINE_PROCS)}
        data_dir = self.config.get("core", {}).get("data_dir", "~/.capman")
        self._capman_dir = Path(data_dir).expanduser().resolve()

        try:
            self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("Could not create snapshot dir %s: %s — diffs disabled", self._snapshot_dir, e)
            self._capture_diffs = False
        try:
            self._snapshot_realpath = os.path.realpath(str(self._snapshot_dir))
        except OSError:
            self._snapshot_realpath = str(self._snapshot_dir)

        self._loop = asyncio.get_running_loop()
        self._debounce: dict[str, asyncio.TimerHandle] = {}
        self._dedup: dict[tuple, float] = {}          # (kind, path) -> last emit ts
        self._git_root_cache: dict[str, str | None] = {}
        self._created_recently: dict[str, float] = {}  # path -> ts (for new-file diffs)
        self._dropped_machine = 0
        self._emitted = 0
        self._last_stats_log = time.time()

        sensor = self

        class _Handler(FileSystemEventHandler):
            def on_modified(self, ev):
                if ev.is_directory:
                    return
                sensor._loop.call_soon_threadsafe(sensor._on_modified, ev.src_path)

            def on_created(self, ev):
                if ev.is_directory:
                    return
                sensor._loop.call_soon_threadsafe(sensor._on_created, ev.src_path)

            def on_deleted(self, ev):
                if ev.is_directory:
                    return
                sensor._loop.call_soon_threadsafe(sensor._on_deleted, ev.src_path)

            def on_moved(self, ev):
                # A directory rename matters too if it moves files around, but we
                # only emit for files to keep noise down.
                if ev.is_directory:
                    return
                sensor._loop.call_soon_threadsafe(
                    sensor._on_moved, ev.src_path, getattr(ev, "dest_path", "")
                )

        observer = Observer()
        handler = _Handler()
        scheduled = 0
        for p in watch_paths:
            if Path(p).is_dir():
                try:
                    observer.schedule(handler, p, recursive=True)
                    scheduled += 1
                except OSError as e:
                    logger.warning("Cannot watch %s: %s", p, e)
        if not scheduled:
            logger.warning("FilesystemSensor: no valid watch paths — sensor idle")
        observer.start()
        logger.info("FilesystemSensor watching %d path(s); user_only=%s capture_diffs=%s",
                    scheduled, self._user_only, self._capture_diffs)
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(2.0)
                now = time.time()
                if now - self._last_stats_log >= 300:
                    logger.debug("FilesystemSensor: emitted=%d dropped_machine=%d",
                                 self._emitted, self._dropped_machine)
                    self._last_stats_log = now
        finally:
            observer.stop()
            observer.join(timeout=5)
            for h in self._debounce.values():
                h.cancel()
            self._debounce.clear()

    # ---- thread-hop landing points (run on the event loop) ------------------

    def _on_modified(self, path: str) -> None:
        if self._excluded(path):
            return
        # Coalesce a burst of writes to the same path into a single FILE_SAVE.
        h = self._debounce.pop(path, None)
        if h is not None:
            h.cancel()
        self._debounce[path] = self._loop.call_later(self._debounce_s, self._fire_save, path)

    def _fire_save(self, path: str) -> None:
        self._debounce.pop(path, None)
        asyncio.create_task(self._handle_save(path))

    def _on_created(self, path: str) -> None:
        if self._excluded(path):
            return
        self._created_recently[path] = time.time()
        # Seed an empty snapshot so the first subsequent save produces a real diff.
        if self._capture_diffs:
            try:
                snap = self._snapshot_path(path)
                if not snap.exists():
                    snap.write_text("", encoding="utf-8")
            except OSError:
                pass
        asyncio.create_task(self._handle_simple(EventType.FILE_OPEN, path))

    def _on_deleted(self, path: str) -> None:
        if self._excluded(path):
            return
        # Clean up any snapshot we kept.
        try:
            sp = self._snapshot_path(path)
            if sp.exists():
                sp.unlink()
        except OSError:
            pass
        asyncio.create_task(self._handle_simple(EventType.FILE_DELETE, path))

    def _on_moved(self, src: str, dest: str) -> None:
        # If the destination is excluded but the source wasn't, treat as a delete;
        # if the source was excluded but dest isn't, treat as a creation.
        src_ex = self._excluded(src)
        dest_ex = self._excluded(dest) if dest else True
        if src_ex and dest_ex:
            return
        if self._capture_diffs and dest and not dest_ex:
            try:
                old_snap = self._snapshot_path(src)
                new_snap = self._snapshot_path(dest)
                if old_snap.exists():
                    old_snap.replace(new_snap)
            except OSError:
                pass
        if src_ex and not dest_ex:
            asyncio.create_task(self._handle_simple(EventType.FILE_OPEN, dest))
        elif dest_ex and not src_ex:
            asyncio.create_task(self._handle_simple(EventType.FILE_DELETE, src))
        else:
            asyncio.create_task(self._handle_rename(src, dest))

    # ---- event builders -----------------------------------------------------

    def _dedup_ok(self, kind: str, path: str, window: float = 0.4) -> bool:
        key = (kind, path)
        now = time.time()
        last = self._dedup.get(key, 0.0)
        if now - last < window:
            return False
        self._dedup[key] = now
        # opportunistic cleanup
        if len(self._dedup) > 4096:
            cutoff = now - 5.0
            self._dedup = {k: v for k, v in self._dedup.items() if v >= cutoff}
        return True

    async def _handle_simple(self, etype: EventType, path: str) -> None:
        kind = etype.value
        if not self._dedup_ok(kind, path):
            return
        verdict, actor, via = self._attribute(path)
        if not self._pass_filter(verdict):
            self._dropped_machine += 1
            return
        p = Path(path)
        payload: dict = {"path": path, "extension": p.suffix,
                         "attribution": verdict, "actor": actor}
        if via:
            payload["via_command"] = via.get("command", "")
            if via.get("command_id"):
                payload["command_id"] = via["command_id"]
        if etype != EventType.FILE_DELETE:
            try:
                st = p.stat()
                payload["size_bytes"] = st.st_size
                payload["mtime"] = st.st_mtime
            except OSError:
                pass
        await self._emit_file(etype, payload, actor)

    async def _handle_rename(self, src: str, dest: str) -> None:
        if not self._dedup_ok("file_rename", dest or src):
            return
        verdict, actor, via = self._attribute(dest or src)
        if not self._pass_filter(verdict):
            self._dropped_machine += 1
            return
        payload: dict = {
            "src_path": src, "dest_path": dest, "extension": Path(dest or src).suffix,
            "attribution": verdict, "actor": actor,
        }
        if via:
            payload["via_command"] = via.get("command", "")
            if via.get("command_id"):
                payload["command_id"] = via["command_id"]
        await self._emit_file(EventType.FILE_RENAME, payload, actor)

    async def _handle_save(self, path: str) -> None:
        if self._excluded(path):
            return
        if not self._dedup_ok("file_save", path, window=0.2):
            return
        verdict, actor, via = self._attribute(path)
        if not self._pass_filter(verdict):
            self._dropped_machine += 1
            return
        p = Path(path)
        try:
            st = p.stat()
        except OSError:
            return  # vanished between debounce and now
        payload: dict = {
            "path": path, "extension": p.suffix,
            "size_bytes": st.st_size, "mtime": st.st_mtime,
            "attribution": verdict, "actor": actor,
        }
        if via:
            payload["via_command"] = via.get("command", "")
            if via.get("command_id"):
                payload["command_id"] = via["command_id"]
        await self._emit_file(EventType.FILE_SAVE, payload, actor)

        if self._capture_diffs:
            diff = await self._compute_diff(path, st.st_size)
            if diff is not None:
                dpayload = {
                    "path": path, "extension": p.suffix,
                    "diff": diff["text"], "lines_added": diff["added"],
                    "lines_removed": diff["removed"],
                    "attribution": verdict, "actor": actor,
                }
                if diff.get("repo"):
                    dpayload["repo"] = diff["repo"]
                if diff.get("branch"):
                    dpayload["branch"] = diff["branch"]
                if via:
                    dpayload["via_command"] = via.get("command", "")
                await self._emit_file(EventType.CODE_DIFF, dpayload, actor)

    async def _emit_file(self, etype: EventType, payload: dict, actor: dict) -> None:
        app = actor.get("app") or actor.get("comm") or "filesystem"
        await self.emit(Event(type=etype, app=app, payload=payload, sensor_id=self.sensor_id))
        self._emitted += 1

    # ---- attribution --------------------------------------------------------

    def _pass_filter(self, verdict: str) -> bool:
        if not self._user_only:
            return True
        if verdict in ("user", "likely_user"):
            return True
        if verdict == "unknown" and self._keep_unknown:
            return True
        return False

    def _attribute(self, path: str) -> tuple[str, dict, dict | None]:
        """
        Decide whether this file op was driven by direct user action.
        Returns (verdict, actor, via_command_or_None).
          verdict ∈ {"user", "likely_user", "machine", "unknown"}
        """
        # 1. Shell-command correlation (strongest no-PID signal).
        try:
            cmds = recent_commands(self._shell_grace)
        except Exception:
            cmds = []
        rp = self._realpath(path)
        match = None
        for c in reversed(cmds):  # newest first
            cwd = c.get("cwd") or ""
            if cwd and (rp == self._realpath(cwd) or self._realpath(cwd) in self._parents(rp)):
                match = c
                break
            # also: command line literally mentions the file's basename
            bn = os.path.basename(path)
            if bn and len(bn) > 2 and bn in c.get("command", ""):
                match = c
                break
        if match is not None:
            head = _command_head(match.get("command", ""))
            actor = {"comm": head or "shell", "pid": match.get("pid")}
            if match.get("cwd"):
                actor["cwd"] = match["cwd"]
            via = {"command": match.get("command", ""), "command_id": match.get("command_id", "")}
            if head and head in self._machine_procs:
                return "machine", actor, via
            if head and head in self._interactive_cli:
                return "user", actor, via
            return "likely_user", actor, via

        # 2. Foreground-window correlation. The currently-focused app drove this
        #    change if (a) it's an editor/IDE/terminal/file-manager and (b) it
        #    has been (or just was) focused — `_fg_grace` covers the lag between
        #    a user action and the filesystem event landing here.
        app, title, since = get_foreground()
        if app:
            base = os.path.basename(app).lower()
            name = app.lower()
            is_interactive = (
                name in self._interactive_apps
                or base in self._interactive_apps
                or any(name.startswith(a) or a in name for a in self._interactive_apps)
            )
            if is_interactive:
                return "user", {"app": app}, None

        # 3. Nothing tied it to the user.
        return "unknown", {}, None

    # ---- diffing ------------------------------------------------------------

    async def _compute_diff(self, path: str, size: int) -> dict | None:
        p = Path(path)
        if self._allowed_ext and p.suffix not in self._allowed_ext:
            return None
        if size > self._diff_max_bytes:
            return None
        try:
            new_bytes = p.read_bytes()
        except OSError:
            return None
        try:
            new_text = new_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return None  # binary-ish

        # Prefer `git diff` when the file lives in a work tree.
        repo_root = self._git_root(p)
        if repo_root is not None:
            g = await self._git_diff(repo_root, path)
            if g is not None and g["text"].strip():
                # keep the snapshot in sync regardless
                self._write_snapshot(path, new_text)
                g["repo"] = Path(repo_root).name
                g["branch"] = self._git_branch(repo_root)
                g["text"] = self._cap_diff(g["text"])
                return g

        # Snapshot fallback.
        snap = self._snapshot_path(path)
        if not snap.exists():
            # First time we see this file. If we watched it being created we
            # have a seeded empty snapshot (so this branch won't run); otherwise
            # it's a pre-existing file — record the baseline silently, no diff.
            created = getattr(self, "_created_recently", {})
            if path not in created:
                self._write_snapshot(path, new_text)
                return None
        old_text = ""
        if snap.exists():
            try:
                old_text = snap.read_text(encoding="utf-8", errors="replace")
            except OSError:
                old_text = ""
        self._write_snapshot(path, new_text)
        if old_text == new_text:
            return None
        diff_lines = list(difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{p.name}", tofile=f"b/{p.name}", n=3,
        ))
        if not diff_lines:
            return None
        text = "".join(diff_lines)
        added, removed = self._count_diff(text)
        out = {"text": self._cap_diff(text), "added": added, "removed": removed}
        if repo_root is not None:
            out["repo"] = Path(repo_root).name
            out["branch"] = self._git_branch(repo_root)
        return out

    @staticmethod
    def _count_diff(patch: str) -> tuple[int, int]:
        added = removed = 0
        for ln in patch.splitlines():
            if ln.startswith("+++") or ln.startswith("---"):
                continue
            if ln.startswith("+"):
                added += 1
            elif ln.startswith("-"):
                removed += 1
        return added, removed

    @staticmethod
    def _cap_diff(text: str) -> str:
        if len(text) <= _DIFF_TEXT_CAP:
            return text
        return text[:_DIFF_TEXT_CAP] + f"\n... [truncated, {len(text) - _DIFF_TEXT_CAP} more chars]\n"

    async def _git_diff(self, repo_root: str, path: str) -> dict | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", repo_root, "--no-pager", "diff", "--no-color", "--", path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=2.5)
        except (OSError, asyncio.TimeoutError):
            return None
        if proc.returncode not in (0, 1):
            return None
        text = out.decode("utf-8", errors="replace")
        if not text.strip():
            return None
        added, removed = self._count_diff(text)
        return {"text": text, "added": added, "removed": removed}

    def _git_root(self, p: Path) -> str | None:
        d = p.parent
        key = str(d)
        if key in self._git_root_cache:
            return self._git_root_cache[key]
        cur = d
        root = None
        # walk up at most 30 levels
        for _ in range(30):
            if (cur / ".git").exists():
                root = str(cur)
                break
            if cur.parent == cur:
                break
            cur = cur.parent
        self._git_root_cache[key] = root
        return root

    def _git_branch(self, repo_root: str) -> str:
        try:
            head = Path(repo_root) / ".git" / "HEAD"
            if head.is_file():
                txt = head.read_text(encoding="utf-8", errors="replace").strip()
                if txt.startswith("ref:"):
                    return txt.split("/", 2)[-1] if "/" in txt else txt[4:].strip()
                return txt[:12]  # detached HEAD → short sha
        except OSError:
            pass
        return ""

    # ---- snapshots ----------------------------------------------------------

    def _snapshot_path(self, path: str) -> Path:
        h = hashlib.sha1(self._realpath(path).encode("utf-8")).hexdigest()
        return self._snapshot_dir / h

    def _write_snapshot(self, path: str, text: str) -> None:
        try:
            self._snapshot_path(path).write_text(text, encoding="utf-8")
        except OSError:
            pass

    # ---- exclusions ---------------------------------------------------------

    def _excluded(self, path: str) -> bool:
        if not path:
            return True
        try:
            rp = Path(self._realpath(path))
        except Exception:
            rp = Path(path)
        # Never look at our own data dir / snapshot store (would feedback-loop).
        if rp == self._capman_dir or self._capman_dir in rp.parents:
            return True
        snap_rp = getattr(self, "_snapshot_realpath", None)
        if snap_rp:
            sp = str(rp)
            if sp == snap_rp or sp.startswith(snap_rp + os.sep):
                return True
        name = rp.name
        parts = rp.parts
        for pat in self._exclude:
            if pat.startswith("**/") and pat.endswith("/**"):
                seg = pat[3:-3]
                if any(fnmatch.fnmatch(part, seg) for part in parts):
                    return True
            elif "/" in pat:
                if fnmatch.fnmatch(str(rp), pat) or fnmatch.fnmatch(str(rp), "*/" + pat):
                    return True
            else:
                if fnmatch.fnmatch(name, pat):
                    return True
        return False

    # ---- helpers ------------------------------------------------------------

    @staticmethod
    def _realpath(path: str) -> str:
        try:
            return os.path.realpath(path)
        except OSError:
            return os.path.abspath(path)

    @staticmethod
    def _parents(path: str) -> set[str]:
        try:
            return {str(p) for p in Path(path).parents}
        except Exception:
            return set()
