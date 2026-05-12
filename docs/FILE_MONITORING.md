# File-Operation Monitoring

capman2 records the file operations **you** perform — opening, editing, renaming
and deleting files — plus, for text files, a diff of *what changed*. The design
goal is fidelity to *direct user action*: we deliberately do **not** record the
constant background churn from build tools, package managers, language servers,
file watchers, sync clients, indexers, or the capman daemon itself.

There are two layers. Layer 1 is on by default and needs no special privileges.
Layer 2 is opt-in, Linux-only, and needs root.

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

deep_monitor              = "off"     # off | fanotify | audit | ebpf   (Layer 2)
deep_monitor_paths        = []        # defaults to watch_paths when empty
```

To disable attribution filtering entirely (record *every* file op under the
watch paths), set `user_only = false`.

---

## Layer 2 — `capman-fsmon`: privileged deep monitor (opt-in, Linux, **root**)

`watchdog` can see creates/modifies/deletes/renames but it **cannot** see file
*opens/reads*, nor *which process* touched a file. `tools/capman-fsmon/fsmon.py`
closes that gap using the kernel and POSTs the surviving events to the daemon's
`/events` endpoint (`sensor_id: "fsmon"`), exactly like the browser extension.
It uses the acting PID to attribute *authoritatively* — an editor or a
TTY-attached file tool → `user`; a build tool / language server / daemon /
capman → `machine` (dropped).

Because file opens fire constantly (every `grep -r`, every LSP scan), `fsmon`
only records `file_open` for clearly-interactive openers (`vim`, `cat`, `bat`,
`less`, `code`, `subl`, GUI editors, …) and rate-limits/dedups per path. It does
not read file contents — diffing stays in Layer 1.

### Backends

| `deep_monitor` | Mechanism | Requirements |
|---|---|---|
| `fanotify` (preferred) | `fanotify_init` + `FAN_MARK_MOUNT` for `FAN_OPEN` & `FAN_CLOSE_WRITE` on the mounts containing your watch paths; PID from the event. | root / `CAP_SYS_ADMIN`, Linux ≥ 2.6.37 |
| `audit` | `auditctl -w <path> -p rwxa -k capman_fsmon`, tail `/var/log/audit/audit.log`, translate `openat`/`write`/`unlinkat`/`renameat*` records. | root, `auditd` installed |
| `ebpf` | `bpftrace tools/capman-fsmon/bpftrace/fileops.bt` (opensnoop-style), parse stdout. Least precise (relative paths not resolved). | root, `bpftrace`, recent kernel |

`--backend auto` (the default) uses the `deep_monitor` value from your config,
falling back to `fanotify`, then `audit`, then `ebpf`.

### Enable it

1. Set `deep_monitor = "fanotify"` (or `audit` / `ebpf`) in
   `config/default.toml` → `[sensors.filesystem]`. `capman start` will then
   print the exact command to run the helper.
2. Run the helper as root, alongside the (user-owned) daemon:
   ```bash
   sudo python3 tools/capman-fsmon/fsmon.py --backend auto
   # or specify everything explicitly:
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

The daemon does **not** spawn `fsmon` itself (it would need root) — it only
prints the recommended command. Stop it with `Ctrl+C` or `systemctl stop
capman-fsmon`.

### Trade-offs

- **Privilege** — fanotify/audit/eBPF all require root. If that's not acceptable,
  stay on Layer 1; you still get edits + diffs + attribution, just not reads or
  process names.
- **Volume** — opens are frequent. The interactive-opener allowlist + per-path
  dedup (`dedup_window_s`, default 30 s) + global rate cap (`rate_cap` per
  `rate_window_s`) keep it sane; if you still see noise, trim `open_recorders`.
- **Coverage** — fanotify classic mode watches whole *mounts* and we filter by
  path prefix; deletes/renames are handled by Layer 1 (fanotify dirent events
  need newer kernels + `FAN_REPORT_FID`, not used here).

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

# Layer 2 — with fsmon running as root, `cat ~/code/somefile.py` in a terminal:
sqlite3 ~/.capman/timeline.db "
  SELECT type, json_extract(payload,'$.path'),
         json_extract(payload,'$.actor')
  FROM events WHERE sensor_id='fsmon' ORDER BY ts DESC LIMIT 10;"
# → a file_open row with actor.comm='cat', a pid, and attribution='user'.
```

Ask the chatbot UI: *"what files did I change in the last hour, and what did I
change in them?"* — it reads recent `file_save` / `code_diff` events (with the
diff text) into its context.
