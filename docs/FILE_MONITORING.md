# File-Operation Monitoring

capman2 records the file operations **you** perform — opening, editing, renaming
and deleting files — plus, for text files, a diff of *what changed*. The design
goal is fidelity to *direct user action*: we deliberately do **not** record the
constant background churn from build tools, package managers, language servers,
file watchers, sync clients, indexers, or the capman daemon itself.

There are two layers. Layer 1 is on by default and needs no special privileges.
Layer 2 is opt-in, needs root, and runs on Linux (fanotify/auditd/eBPF) or
macOS (Endpoint Security via `eslogger`, or `fs_usage`).

---

## Layer 1 — `filesystem` sensor (default, no root, cross-platform)

Built on `watchdog`. Under each path in `[sensors.filesystem].watch_paths` it
watches for:

| Filesystem event | capman event |
|---|---|
| file created            | `file_open`  (a freshly-created file) |
| file modified (debounced) | `file_save` |
| file deleted            | `file_delete` |
| file moved / renamed    | `file_rename` (`src_path` → `dest_path`) |
| text file changed       | `code_diff` — unified diff + `lines_added`/`lines_removed` (+ `repo`/`branch` if in a git work tree) |

- **Debounce/coalesce** — a burst of writes to the same file collapses into one
  `file_save` (`debounce_ms`, default 1200 ms).
- **Content diffs** — for files whose extension is in `[sensors.filesystem].extensions`
  and under `diff_max_bytes` (default 1 MiB), we keep the last-seen content in
  `snapshot_dir` (`~/.capman/file_snapshots/`, keyed by a hash of the path) and
  emit a `code_diff` of what changed. If the file is inside a git repo we use
  `git diff` instead (ignores already-committed formatting noise) and tag the
  event with the repo name and branch.
- **Exclusions** — paths matching `[sensors.filesystem].exclude` are never looked
  at (defaults: `**/.git/**`, `**/node_modules/**`, `**/.venv/**`,
  `**/__pycache__/**`, `**/dist/**`, `**/build/**`, `**/target/**`, caches,
  editor swap/temp files, `*.lock`, …), and `~/.capman/**` is always excluded.

### Direct-user-action attribution

A raw filesystem event doesn't say *who* caused it, so we infer it from up to
three signals and only record the event if the verdict is `user` or
`likely_user` (configurable). `machine` and `unknown` are dropped; a debug
counter (`dropped_machine`) is logged periodically so you can tune the lists.

1. **Shell-command correlation** — the real-time shell hook publishes every
   interactive command (with cwd and the shell's PID). If a file op happens in a
   directory under a recently-run command's cwd (or the command line mentions the
   file), it's attributed to that command:
   - command starts with a known build/package/LSP tool (`machine_procs`) → **machine** (dropped)
   - command starts with an interactive file tool (`interactive_cli`: `vim`, `cp`, `sed`, `tee`, `tar`, …) → **user**
   - otherwise (it still ran from a shell) → **likely_user**
2. **Foreground-window correlation** — if no command matched, but the currently
   focused app is an editor/IDE/terminal/file-manager (`interactive_apps`), the
   change is attributed to **user**. (On headless machines there's no window
   sensor, so this signal is absent.)
3. **Neither** → **unknown** → dropped (set `keep_unknown = true` to keep these
   as `likely_user`).

Every recorded file event carries `attribution` (`"user"`/`"likely_user"`),
`actor` (`{app}` or `{comm, pid, exe, tty}`), and — when shell-correlated —
`via_command` / `command_id`.

> Caveat: without process-level info, Layer 1 can mis-attribute a write that a
> language server or watcher makes *while* you happen to have its editor focused.
> Layer 2 fixes this with real PIDs. In practice the path exclusions already
> cover the noisy locations.

### Config — `config/default.toml` → `[sensors.filesystem]`

```toml
watch_paths     = ["~/Desktop","~/Downloads","~/Documents","~/code","~/projects"]
extensions      = [".py",".js",".ts", ...]   # gates CODE_DIFF capture only
exclude         = ["**/.git/**","**/node_modules/**", ...]
debounce_ms     = 1200
capture_diffs   = true
diff_max_bytes  = 1048576
snapshot_dir    = "~/.capman/file_snapshots"

user_only                 = true     # drop file ops not attributable to the user
keep_unknown              = false
foreground_window_grace_s = 4
shell_correlate_s         = 8
interactive_apps          = [ ... ]   # editors / IDEs / terminals / file managers
interactive_cli           = [ ... ]   # vim, nano, cp, mv, sed, tar, ...
machine_procs             = [ ... ]   # node, tsc, cargo, pip, npm, lsp servers, git, ...

deep_monitor              = "off"     # Layer 2 — Linux: fanotify|audit|ebpf · macOS: eslogger|fs_usage
deep_monitor_paths        = []        # defaults to watch_paths when empty
```

To disable attribution filtering entirely (record *every* file op under the
watch paths), set `user_only = false`.

---

## Layer 2 — `capman-fsmon`: privileged deep monitor (opt-in, Linux + macOS, **root**)

`watchdog` can see creates/modifies/deletes/renames but it **cannot** see file
*opens/reads*, nor *which process* touched a file. `tools/capman-fsmon/fsmon.py`
closes that gap using the kernel and POSTs the surviving events to the daemon's
`/events` endpoint (`sensor_id: "fsmon"`), exactly like the browser extension.
It uses the acting process to attribute *authoritatively* — an editor (matched
by process name **or code-signing identity**) or a TTY-attached file tool →
`user`; a build tool / language server / daemon / capman → `machine` (dropped).

Because file opens fire constantly (every `grep -r`, every LSP scan), `fsmon`
only records `file_open` for clearly-interactive openers (`vim`, `cat`, `bat`,
`less`, `code`, `subl`, GUI editors and editor-signed helper processes, …) and
rate-limits/dedups per path. It does not read file contents — diffing stays in
Layer 1.

### Backends

| `deep_monitor` | OS | Mechanism | Requirements |
|---|---|---|---|
| `fanotify` (Linux default) | Linux | `fanotify_init` + `FAN_MARK_MOUNT` for `FAN_OPEN` & `FAN_CLOSE_WRITE` on the mounts containing your watch paths; PID from the event. | root / `CAP_SYS_ADMIN`, Linux ≥ 2.6.37 (blocked in unprivileged containers) |
| `audit` | Linux | `auditctl -w <path> -p rwxa -k capman_fsmon`, tail `/var/log/audit/audit.log`, translate `openat`/`write`/`unlinkat`/`renameat*` records. | root, `auditd` |
| `ebpf` | Linux | `bpftrace tools/capman-fsmon/bpftrace/fileops.bt` (opensnoop-style), parse stdout. Least precise (relative paths not resolved). | root, `bpftrace`, recent kernel |
| `eslogger` (macOS default) | macOS | Streams **Endpoint Security** events via `/usr/bin/eslogger` (`open` / `create` / `close[modified]` / `rename` / `unlink`); each message carries the responsible process — executable path, pid, ppid, **signing id**, tty. | root **and Full Disk Access**, macOS 13+ |
| `fs_usage` | macOS | Parse `fs_usage -w -f filesys` — opens / deletes / renames only (`file_save` comes from the in-daemon watchdog sensor). | root |

`--backend auto` (the default) uses the `deep_monitor` value from your config if
it's valid for the current OS, else picks the OS default (`fanotify` on Linux,
`eslogger` on macOS) and falls back through the rest. So leaving
`deep_monitor = "fanotify"` is fine — on macOS `fsmon --backend auto` will use
`eslogger` instead.

### Enable it — Linux

1. Set `deep_monitor = "fanotify"` (or `audit` / `ebpf`) in
   `config/default.toml` → `[sensors.filesystem]`. `capman start` then prints
   the command to run the helper.
2. Run the helper as root, alongside the (user-owned) daemon:
   ```bash
   sudo python3 tools/capman-fsmon/fsmon.py --backend auto
   # or explicitly:
   sudo python3 tools/capman-fsmon/fsmon.py --backend fanotify \
        --api http://127.0.0.1:7331 --paths ~/code ~/projects
   ```
   Or install the systemd unit:
   ```bash
   sudo cp tools/capman-fsmon/capman-fsmon.service /etc/systemd/system/
   sudoedit /etc/systemd/system/capman-fsmon.service   # fix WorkingDirectory / ExecStart / HOME
   sudo systemctl daemon-reload
   sudo systemctl enable --now capman-fsmon
   journalctl -u capman-fsmon -f
   ```

### Enable it — macOS

1. Set `deep_monitor = "eslogger"` in `[sensors.filesystem]` (or leave
   `"fanotify"` — `--backend auto` self-corrects on macOS).
2. **Grant Full Disk Access** to the Python interpreter you'll run `fsmon.py`
   with (e.g. your capman venv's `python3`, or `/usr/bin/python3`):
   System Settings → Privacy & Security → Full Disk Access → add it. Without
   this, `eslogger` produces no events.
3. Run it as root:
   ```bash
   sudo /path/to/capman2/.venv/bin/python3 tools/capman-fsmon/fsmon.py --backend auto
   # (auto falls back to fs_usage if eslogger is unavailable)
   ```
   Or install the LaunchDaemon:
   ```bash
   sudo cp tools/capman-fsmon/com.capman.fsmon.plist /Library/LaunchDaemons/
   sudo nano /Library/LaunchDaemons/com.capman.fsmon.plist   # fix paths + HOME
   sudo launchctl load -w /Library/LaunchDaemons/com.capman.fsmon.plist
   log stream --predicate 'process == "fsmon.py"'
   ```

> **The "real" ES System Extension** — a production-grade Endpoint Security
> client (a code-signed `.systemextension` with the
> `com.apple.developer.endpoint-security.client` entitlement, which Apple grants
> on request) is out of scope here. `eslogger` is Apple's own already-entitled
> CLI and delivers the same events with far less ceremony — the right tool for a
> personal capture engine.

The daemon does **not** spawn `fsmon` itself (it would need root) — it only
prints the recommended command. Stop it with `Ctrl+C`, `systemctl stop
capman-fsmon`, or `launchctl unload …`.

### Trade-offs

- **Privilege** — every Layer 2 backend needs root, and `eslogger` also needs
  Full Disk Access. If that's not acceptable, stay on Layer 1; you still get
  edits + diffs + attribution, just not reads or process names.
- **Volume** — opens are frequent. The interactive-opener allowlist + per-path
  dedup (`dedup_window_s`, default 30 s) + global rate cap (`rate_cap` per
  `rate_window_s`) keep it sane; if you still see noise, trim `open_recorders`.
- **Coverage** — Linux `fanotify` watches whole *mounts* (we filter by path
  prefix) and deletes/renames there fall back to Layer 1; macOS `fs_usage`
  doesn't carry write paths, so `file_save` there comes from Layer 1; `eslogger`
  covers all of open/create/close/rename/unlink with the process attached.

---

## Verifying it works

```bash
# Layer 1 — make some user edits, then:
sqlite3 ~/.capman/timeline.db "
  SELECT type, json_extract(payload,'$.path'),
         json_extract(payload,'$.attribution'),
         json_extract(payload,'$.lines_added')
  FROM events
  WHERE type IN ('file_open','file_save','file_delete','file_rename','code_diff')
  ORDER BY ts DESC LIMIT 20;"

# Layer 1 — confirm machine churn is NOT recorded: run `npm install` in a repo,
# then check there are no file_save/code_diff rows for node_modules/**, and the
# daemon log shows `dropped_machine` rising.

# Layer 2 — with fsmon running as root (Linux: sudo … --backend auto;
# macOS: sudo … --backend auto, after granting Full Disk Access), then
# `cat ~/code/somefile.py` in a terminal:
sqlite3 ~/.capman/timeline.db "
  SELECT type, json_extract(payload,'$.path'),
         json_extract(payload,'$.actor')
  FROM events WHERE sensor_id='fsmon' ORDER BY ts DESC LIMIT 10;"
# → a file_open row with actor.comm='cat', a pid, and attribution='user'
#   (on macOS the actor also carries the signing_id).
```

Ask the chatbot UI: *"what files did I change in the last hour, and what did I
change in them?"* — it reads recent `file_save` / `code_diff` events (with the
diff text) into its context.
