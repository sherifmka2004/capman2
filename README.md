# capman2 — Cognitive Workflow Capture Engine

> Clone a domain expert's knowledge — not just what they do, but **how they think**.

Most knowledge management tools capture outputs: notes, bookmarks, saved articles. capman2 captures the **cognitive process itself**: the searches that failed before the one that worked, the docs skimmed vs. the ones read deeply, the commands run in sequence, the decision points where one approach was abandoned for another. That methodology — not the conclusion — is what makes an expert replicable.

---

## Quick Install (one command)

```bash
curl -sSL https://raw.githubusercontent.com/sherifmka2004/capman2/main/install.sh | bash
```

Then:

```bash
export OPENROUTER_API_KEY=sk-or-v1-...   # or ANTHROPIC_API_KEY
capman start
```

That's it. Open `http://localhost:7331` for the **chat UI**, or use the CLI (`capman query`, `capman status`).

The installer handles `uv`, Python deps, the global `capman` command, and the daemon scaffolding. It auto-detects headless servers and disables display sensors.

---

## Desktop App (macOS / Linux / Windows)

No Python required — download the pre-built app and double-click.

### Download

Grab the latest release from the [**Releases page**](https://github.com/sherifmka2004/capman2/releases):

| Platform | File |
|----------|------|
| macOS (Apple Silicon) | `capman2-macos-arm64.dmg` |
| macOS (Intel) | `capman2-macos-x86_64.dmg` |
| Linux (x86_64) | `capman2-linux-x86_64.AppImage` |
| Windows (x86_64) | `capman2-windows-x86_64-setup.exe` |

### macOS

1. Open the `.dmg` and drag **capman2** into your Applications folder.
2. Launch capman2 from Applications (or Spotlight).
3. On first launch macOS may show *"capman2 cannot be opened because the developer cannot be verified"* — open **System Settings → Privacy & Security**, scroll down, and click **Open Anyway**.
4. Grant **Accessibility** access when prompted (required for keyboard/mouse sensors): **System Settings → Privacy & Security → Accessibility** → enable capman2.
5. The app icon appears in your **menu bar**. Your browser opens automatically at `http://localhost:7331`.
6. Click the menu-bar icon → **Open Dashboard** → **Settings tab** → enter your API key and save.

### Linux

```bash
chmod +x capman2-linux-x86_64.AppImage
./capman2-linux-x86_64.AppImage
```

> **Note:** FUSE is required for AppImage. Install with:
> ```bash
> # Debian/Ubuntu
> sudo apt install libfuse2
> # Fedora
> sudo dnf install fuse
> ```

The app icon appears in your system tray. Your browser opens at `http://localhost:7331`.

### Windows

1. Run `capman2-windows-x86_64-setup.exe` and follow the installer.
2. Launch **capman2** from the Start menu or desktop shortcut.
3. The app icon appears in the **system tray** (bottom-right). Your browser opens at `http://localhost:7331`.

### First-run setup (all platforms)

1. Open the dashboard (browser auto-opens, or click tray icon → **Open Dashboard**).
2. Go to the **⚙ Settings** tab.
3. Enter your **Anthropic API Key** (or OpenRouter API Key) and click **Save Settings**.
4. Enable or disable sensors and adjust thresholds to your preference.
5. Capture starts automatically — sessions appear in the **Sessions** tab after 60+ seconds of activity.

> **API key:** Get one at [console.anthropic.com](https://console.anthropic.com) (Claude Haiku is the primary model; costs are very low — typically <$1/day of heavy use).

### Tray menu

| Action | What it does |
|--------|-------------|
| Open Dashboard | Opens `http://localhost:7331` in your browser |
| Stop Capture | Sends a graceful shutdown signal to the daemon |
| Quit capman2 | Stops capture and exits the tray app |

---

## Build from Source (desktop app)

Prerequisites: Python 3.11–3.12, [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/sherifmka2004/capman2.git
cd capman2
uv sync

# Run in desktop mode (tray icon + browser UI, no PyInstaller needed)
uv run capman-desktop

# Or build the platform executable
uv pip install pyinstaller
uv run pyinstaller capman.spec --noconfirm
# macOS: open dist/capman2.app
# Linux: ./dist/capman2/capman2
# Windows: dist\capman2\capman2.exe
```

The PyInstaller build creates a `dist/capman2/` directory (~150–200 MB). On macOS it also produces `dist/capman2.app`. Package into a distributable:

```bash
# macOS — requires: brew install create-dmg
create-dmg --volname "capman2" --app-drop-link 400 120 \
  dist/capman2-macos.dmg dist/capman2.app

# Linux — requires appimagetool
# (See .github/workflows/build.yml for the full AppImage build steps)

# Windows — requires Inno Setup
iscc packaging\windows\capman2.iss
```

### Automated releases (GitHub Actions)

Pushing a version tag triggers the full cross-platform build:

```bash
git tag v0.1.0
git push origin v0.1.0
```

Four artifacts are built in parallel (macOS arm64, macOS x86_64, Linux AppImage, Windows installer) and attached to the GitHub Release automatically. See [`.github/workflows/build.yml`](.github/workflows/build.yml).

---

## Why This Exists: The Cognitive Imperative

In a world where LLMs are ubiquitous, the value shift has moved from *information* to *methodology*.

When a senior engineer debugs a production incident, a data scientist investigates an anomaly, or a researcher traces a literature gap, the most valuable asset isn't the final answer they reach. It is the **cognitive workflow that got them there**: what they searched first, how they pivoted when that failed, what signals told them they were on the right track, and how they structured their thought process.

Currently, that methodology is invisible. It lives only in the expert's head, and it dies when the session ends.

**capman2** makes this cognitive process persistent, searchable, and replayable. By capturing not just outputs but the *sequence of decisions*, it builds a knowledge engine that allows future-you (or future-AI) to approach new problems not just with *data*, but with the *proven methodology* of an expert. It transforms your daily work from a series of disconnected tasks into a structured, evolving playbook of your own expertise.


---

## What It Captures

### Activity Sensors (all passive, zero friction)

| Sensor | What it captures | Platforms |
|--------|-----------------|-----------|
| `window` | Active app + window title + focus duration | macOS, Linux, Windows |
| `keyboard` | Aggregated text blocks (not individual keys) | macOS, Linux, Windows |
| `mouse` | Clicks **enriched with the UI element under the cursor** (AX/AT-SPI/UIAutomation), coalesced **scroll bursts**, per-app per-minute **move heatmap** | macOS, Linux, Windows |
| `idle` | AFK detection on real input activity (keys/clicks/scroll). Emits `idle_start`/`idle_end`; force-closes the current session on idle. See [docs/INPUT_ACTIVITY.md](docs/INPUT_ACTIVITY.md). | macOS, Linux, Windows |
| `clipboard` | Copy/paste chains with source app | macOS, Linux, Windows |
| `screenshot` | Periodic + event-triggered screenshots with OCR | macOS, Linux, Windows |
| `shell` | History watcher (`.bash_history`, `.zsh_history`) — passive fallback | macOS, Linux |
| `shell_hook` | Real-time bash/zsh hook with command + exit code + duration + CWD + SSH/TTY context | macOS, Linux |
| `filesystem` | File create/save/delete/rename **+ content diffs** — only files the *user* directly touched (editor / interactive shell / focused file manager), not build-tool/LSP/daemon churn. Git-aware diffs in repos. | macOS, Linux, Windows |
| `capman-fsmon` | **Privileged deep monitor** (opt-in, needs root): true file *opens/reads* + the responsible **process** (PID/comm/exe/signing-id/TTY). Linux via fanotify (auditd/eBPF fallback); macOS via Endpoint Security `eslogger` (or `fs_usage`, needs Full Disk Access). POSTs to the daemon. See [docs/FILE_MONITORING.md](docs/FILE_MONITORING.md). | Linux, macOS |
| `browser_relay` | Tab lifecycle, URLs, search queries, page text (via extension) | Chrome, Firefox |
| `documents` | Slide/page/sheet navigation with dwell times + **content of what you actually read** | macOS, Linux, Windows |

### Document Navigation (the layer nobody else has)

When you navigate a PowerPoint, capman2 records every slide you visit, how long you stayed, whether you jumped non-linearly, and which slides you returned to. Same for Word pages, Excel sheets, PDF pages, and notes apps. This turns a document session into a structured reading graph — "spent 4 minutes on slide 7, jumped back to slide 3, skipped slides 8-10" — which the LLM uses to infer what content you found important, confusing, or already known.

On top of navigation, capman2 also captures the **text of the slides / pages /
sheets you actually read** — gated by an attention policy so quick
scroll-throughs are silently dropped. The full text is embedded into ChromaDB
(searchable from the chatbot, e.g. *"summarize what I read in that PDF
yesterday"*); SQLite keeps a slim 300-char excerpt. Configurable: dwell
threshold, per-doc cap, OCR vs. app-model extractor order — see
[`docs/DOCUMENT_CONTENT.md`](docs/DOCUMENT_CONTENT.md).

Supported apps:
- **Presentations**: PowerPoint, Keynote, LibreOffice Impress
- **Documents**: Word, Pages, LibreOffice Writer
- **Spreadsheets**: Excel, Numbers, LibreOffice Calc
- **Notes**: Apple Notes, OneNote, Obsidian, Evernote, Notion
- **PDFs**: Preview, Acrobat, Evince, Zathura

### File Operations — *only what you actually did*

The `filesystem` sensor records when **you** create, edit, rename or delete files
under your watch paths, and for text files it captures a **unified diff of what
changed** (snapshot-based, or `git diff` when the file is in a repo). The catch
that makes it useful: it attributes every file op and only keeps the ones driven
by **direct user action** — an editor/IDE, an interactive shell command, a focused
file manager — and drops the firehose of background churn (webpack/tsc/cargo,
language servers, `npm install`, watchers, indexers, the capman daemon itself, …).
Attribution uses the focused window, recently-captured shell commands, and (with
the privileged helper) the acting process's identity.

Optionally enable **`capman-fsmon`** (root; Linux via fanotify/auditd/eBPF,
macOS via Endpoint Security `eslogger`) to also capture true file *opens/reads*
and the *responsible process* — see [docs/FILE_MONITORING.md](docs/FILE_MONITORING.md).

---

## Architecture
 
```
┌──────────────────────────────────────────────────────────────────┐
│                        Capture Sensors                           │
│  window · keyboard · mouse · idle · clipboard · screenshot       │
│  shell · filesystem · browser_relay · documents                  │
└────────────────────────┬─────────────────────────────────────────┘
                         │ Event objects (typed, timestamped)
                         ▼
              ┌─────────────────────┐
              │   AsyncEventBuffer  │  thread-safe asyncio.Queue bridge
              └──────────┬──────────┘
                         │
                         ▼
              ┌──────────────────────────┐
              │     SessionDetector      │  sliding-window state machine
              │   IDLE → ACTIVE → COOL   │  groups events into episodes
              └─────────────┬────────────┘
                         │ completed Session
                         ▼
              ┌─────────────────────┐
              │      Enricher       │  OCR on screenshots, URL normalization
              └──────────┬──────────┘
                         │
                         ▼
              ┌─────────────────────────────────────────┐
              │         LLM Analyzer (3 passes)         │
              └──────────┬──────────────────────────────┘
                         │
               ┌─────────┴──────────────────────────────┐
               │        Pluggable Storage Layer         │
               │  [Local / Remote Adapters]             │
               │  [Encryption-at-rest Decorator]        │
               └─────────┬──────────────────────────────┘
                         │
              ┌──────────┴──────────────────────────────┐
              │                Stores                   │
              │  SQLite    — raw event timeline          │
              │  ChromaDB  — semantic vector index       │
              │  Markdown  — Obsidian knowledge graph    │
              └─────────────────────────────────────────┘
```

### Pluggable Storage & Encryption
capman2 now supports a modular storage architecture. You can configure it to use local storage (SQLite/ChromaDB) or plug in remote/dedicated backends. 

All storage operations can be optionally wrapped in an encryption-at-rest layer using AES-256 (`cryptography` library), ensuring that your sensitive cognitive workflows remain private even if stored in remote cloud services.


### Session Detection

Events are grouped into **problem-solving sessions** by a sliding-window state machine:

```
IDLE ──(any significant event)──► ACTIVE
ACTIVE ──(idle for 60s)──────────► COOLING_DOWN
COOLING_DOWN ──(new event)────────► ACTIVE
COOLING_DOWN ──(timeout)──────────► IDLE  →  flush session
```

A new session (vs. extending the current one) is triggered when 2+ of:
- Dominant app changed (e.g., terminal → Slack)
- URL domain family changed
- 10+ minutes elapsed since last activity
- Hard break threshold exceeded (600s)

### 3-Pass LLM Analysis

| Pass | Model | Runs when | Output |
|------|-------|-----------|--------|
| 1 — Summarize | `claude-haiku-4-5` | Every session with ≥ 2 events | `problem_statement`, `approach_description`, `methodology_tags`, `reusability_estimate` |
| 2 — Chain-of-Thought | `claude-sonnet-4-6` | Sessions with reusability ≥ 0.3 | Full `ChainOfThought`: steps, decision points, methodology pattern |
| 3 — Triple Extract | `claude-haiku-4-5` | After every Pass 2 | Knowledge graph triples |

Pass 2 is the core of the system. It extracts a **replicable methodology pattern** — not just what you did, but the cognitive sequence behind it:

```
symptom-search → official-docs → refine-query → SO-solution → verify
orient (ls) → state-check (git status) → validate (pytest)
reproduce → isolate → binary-search → fix → regression-test
```

### Knowledge Graph

Every session produces:
- **Knowledge nodes** — concepts, tools, patterns, methodologies (Obsidian-compatible markdown)
- **Knowledge edges** — typed relationships: `causes`, `requires`, `resolves`, `part_of`, `contradicts`
- **Chain-of-thought records** — the full cognitive workflow as a reusable template

Stored as human-readable markdown (`~/.capman/knowledge/`) and indexed in ChromaDB for semantic search.

**Example node** (`~/.capman/knowledge/react-hydration-error.md`):
```markdown
---
id: "..."
node_type: concept
tags: [react, hydration, ssr, debugging]
first_seen: 2026-05-06T10:23:00Z
source_sessions: ["sess-abc"]
---

# React Hydration Error

## Relationships
- [[Server-Side Rendering]] ← requires
- [[suppressHydrationWarning]] ← resolved_by

## Chain of Thought Patterns Used
- [[CoT: symptom-search → docs → SO → verify]] — applied 2×
```

---

## Installation

### One-line install (recommended)

```bash
curl -sSL https://raw.githubusercontent.com/sherifmka2004/capman2/main/install.sh | bash
```

The installer:
- installs `uv` (if missing)
- clones the repo to `~/capman2`
- runs `uv sync` to resolve all Python dependencies
- creates a global `capman` command at `/usr/local/bin/capman` (or `~/.local/bin/capman` if no sudo)
- prompts for your LLM API key

Override defaults with env vars:
```bash
CAPMAN_DIR=/opt/capman2 CAPMAN_REPO=https://github.com/youruser/capman2.git \
  curl -sSL https://raw.githubusercontent.com/sherifmka2004/capman2/main/install.sh | bash
```

### Manual install

**Requirements:** Python 3.11+, `uv`

```bash
git clone https://github.com/sherifmka2004/capman2.git
cd capman2
uv sync
```

Optional global command:
```bash
sudo tee /usr/local/bin/capman > /dev/null << EOF
#!/bin/bash
cd $(pwd) && exec uv run capman "\$@"
EOF
sudo chmod +x /usr/local/bin/capman
```

### Real-time Shell Capture

The installer adds a one-line hook to your `~/.bashrc` and `~/.zshrc`:

```bash
source ~/capman2/shell/capman-init.sh
```

After `source ~/.bashrc` (or opening a new terminal), every command you run gets captured **in real time** with full context:

- `command` — exact command text
- `cwd` — working directory at execution time
- `exit_code` — success or failure
- `duration_ms` — how long it took
- `hostname`, `user`, `tty`, `ssh` — environmental context

This works across local terminals, SSH sessions, tmux/screen, and remote servers. The hook is async (never blocks your prompt) and silently no-ops if the daemon is unreachable.

You can also post custom events from any shell script:

```bash
capman-event note_taken '{"text": "remember to refactor the auth layer"}'
```

### LLM Backend

Set one of:
```bash
export ANTHROPIC_API_KEY=sk-...        # Direct Anthropic SDK
export OPENROUTER_API_KEY=sk-or-v1-... # OpenRouter (supports same Claude models)
```

If neither is set, capman still captures and stores all events — analysis just won't run until a key is configured.

---

## Running

### Start (auto-detects headless vs. desktop)

```bash
capman start
```

On a desktop (with `$DISPLAY` / Wayland), all sensors activate.

On a server or SSH session without a display, headless mode is detected automatically and only the sensors that don't require a GUI are enabled: `shell`, `filesystem`, `browser_relay`.

```bash
capman start --headless   # force headless regardless of $DISPLAY
```

### Stop

```bash
capman stop
```

### Status

```bash
capman status
# capman2 running (PID 12345)
#   Events:   1,847
#   Sessions: 23
#   Analyzed: 21
```

### Storage usage

```bash
capman storage
# capman2 storage  —  ~/.capman
#   Total: 8.1 MB  (298 files)
#   Growth: ~1.4 MB/day  (~41.2 MB/month, over 5.9 days)
#   ┌ Component ──────────────┬─ Size ──┬─ % ──┬ Files ┐
#   │ Vector store (ChromaDB) │  6.4 MB │ 79%  │     6 │
#   │ Timeline DB (SQLite)    │  1.3 MB │ 16%  │     3 │
#   │ Screenshots             │   ...   │ ...  │   ... │
#   └─────────────────────────┴─────────┴──────┴───────┘
```

Also available as `GET /storage` (JSON) and as the **💾 Storage** tab in the
web UI — full breakdown by component, DB row counts, events-by-type, and a
growth estimate.

### Query your knowledge

```bash
capman query "how did I debug the react hydration issue"
capman query "python async patterns I've used"
capman query "my approach to debugging prod incidents"
```

---

## Browser Extension

For full web capture (search queries, tab sequences, page text, visit durations):

1. Open `chrome://extensions` → **Load unpacked** → select `browser-extension/`
2. The extension posts events to `http://localhost:7331` (the capman API)
3. Supported engines: Google, Bing, DuckDuckGo, GitHub, YouTube, Reddit, Stack Overflow, and more

---

## Configuration
 
Config is layered (each overrides the previous):
1. `config/default.toml` — base defaults
2. `config/{macos,linux,windows}.toml` — OS-specific overrides
3. `~/.capman/config.toml` — user overrides (optional)
4. Named overlays: `headless.toml` (loaded automatically when no display)
 
**Key settings** (`config/default.toml`):
 
```toml
[storage]
# 'local' or 'remote' (pluggable adapters)
mode = "local"
# Optional AES-256 encryption-at-rest
encryption_enabled = false
encryption_key_env = "CAPMAN_MASTER_KEY"

[storage.local]
vector_path = "~/.capman/chroma"
timeline_path = "~/.capman/timeline.db"

[sensors]
enabled = ["window", "screenshot", "keyboard", "clipboard",
           "shell", "filesystem", "browser_relay", "documents"]
```

The `[storage]` section now supports flexible configuration of local paths or remote backend credentials. If `encryption_enabled` is set to true, all data is encrypted locally before being passed to the storage adapter.


## Data Storage

| Store | Path | Contents |
|-------|------|----------|
| SQLite | `~/.capman/timeline.db` | Raw event log, sessions, analyses, triples |
| Markdown | `~/.capman/knowledge/` | Obsidian-compatible knowledge graph nodes |
| ChromaDB | `~/.capman/chroma/` | Semantic vector index for similarity search |
| Screenshots | `~/.capman/screenshots/` | Timestamped PNGs with OCR text |

All raw events are immutable — re-analysis is always possible without data loss.

---

## Use Cases

### Personal knowledge base that actually reflects how you think

After 3 months of running, capman2 has seen you debug dozens of issues. It knows your playbook: you always check official docs before Stack Overflow, you run `git log --oneline` before bisecting, you reach for `grep -r` before installing a search tool. That methodology, encoded as chain-of-thought templates, can be fed to an LLM to make it approach new problems your way.

### Onboarding acceleration

A new team member gets access to the captured workflows of senior engineers. Instead of "ask Alice when you're stuck on the auth service," they can query: *"how has our team debugged JWT expiry issues before?"* — and get a step-by-step reconstruction of what was actually done, not a post-hoc write-up.

### Knowledge audit

Which concepts appear repeatedly in your work? Which tools keep coming up? Which problems are you solving the same way over and over (automation opportunity)? Which problems required multiple sessions before resolution (knowledge gap)?

### LLM context injection

Before starting a new task, query capman for similar past sessions. Inject the matching chain-of-thought as context: *"the last time I approached this type of problem, I used this sequence..."* — grounding the LLM in your actual proven methodology.

### Document reading intelligence

Track how you navigate technical documentation, research papers, or slide decks. Which sections do you reread? Where do you spend the most time? Which pages do you jump back to? This produces a semantic map of what you found important vs. skimmed, which can inform future reading priorities and summarization.

---

## Project Structure

```
capman2/
├── config/
│   ├── default.toml          # base configuration
│   ├── headless.toml         # server/SSH overlay (no display sensors)
│   ├── macos.toml            # macOS overrides
│   ├── linux.toml            # Linux overrides
│   └── windows.toml          # Windows overrides
├── capman/
│   ├── events.py             # ALL data models (Event, Session, ChainOfThought, Triple, ...)
│   ├── config.py             # TOML config loader with layered merging
│   ├── main.py               # CLI: start · stop · status · query
│   ├── sensors/
│   │   ├── base.py           # BaseSensor ABC — plugin interface
│   │   ├── registry.py       # auto-discovers sensors via pkgutil
│   │   ├── window.py         # active window + focus duration
│   │   ├── screenshot.py     # periodic + event-triggered screenshots
│   │   ├── keyboard.py       # keystroke aggregation into text blocks
│   │   ├── mouse.py          # click events
│   │   ├── clipboard.py      # copy/paste chain tracking
│   │   ├── filesystem.py     # file open/save/close via watchdog
│   │   ├── shell.py          # shell history watcher
│   │   ├── browser_relay.py  # HTTP receiver for browser extension
│   │   └── documents.py      # slide/page/sheet navigation with dwell times
│   ├── platform/
│   │   ├── base.py           # PlatformAdapter ABC + app classification registry
│   │   ├── macos.py          # AppleScript queries for Office/iWork/Notes
│   │   ├── linux.py          # xdotool + LibreOffice UNO
│   │   └── windows.py        # win32com COM automation
│   ├── pipeline/
│   │   ├── buffer.py         # AsyncEventBuffer: thread-safe queue bridge
│   │   ├── session.py        # SessionDetector: IDLE/ACTIVE/COOLING state machine
│   │   ├── enricher.py       # OCR on screenshots, URL normalization
│   │   ├── ocr.py            # OCR abstraction: Apple Vision (macOS) or Tesseract
│   │   ├── prompts.py        # versioned LLM prompt templates (Pass 1/2/3)
│   │   ├── analyzer.py       # 3-pass LLM orchestrator (Anthropic SDK or OpenRouter)
│   │   └── runner.py         # PipelineRunner: wires all pipeline stages
│   ├── knowledge/
│   │   ├── nodes.py          # KnowledgeNode, KnowledgeEdge dataclasses
│   │   ├── extractor.py      # LLM JSON → Triple + ChainOfThought
│   │   ├── graph.py          # KnowledgeGraph: in-memory graph + disk persistence
│   │   ├── merger.py         # GraphMerger: merge triples, resolve conflicts
│   │   └── markdown.py       # Obsidian-compatible serializer + document node writer
│   ├── storage/
│   │   ├── schema.sql        # SQLite DDL (version-controlled)
│   │   ├── timeline.py       # async SQLite adapter (events/sessions/analyses CRUD)
│   │   └── vector.py         # ChromaDB adapter (embed + semantic search)
│   └── api/
│       ├── server.py         # FastAPI app
│       └── routes/
│           ├── events.py     # POST /events (browser extension ingestion)
│           ├── sessions.py   # GET /sessions, /sessions/{id}
│           ├── query.py      # GET /query?q=... (semantic search)
│           └── knowledge.py  # GET /knowledge/nodes, /knowledge/graph
├── browser-extension/
│   ├── manifest.json         # MV3 (Chrome + Firefox compatible)
│   ├── background.js         # tab lifecycle, URL capture, search detection
│   ├── content.js            # page text + heading extraction
│   └── utils/
│       ├── api.js            # POST events to localhost:7331
│       └── search.js         # query extraction from 11 search engines
└── tests/
    ├── unit/
    │   ├── test_events.py
    │   ├── test_session_detector.py
    │   ├── test_graph_merger.py
    │   ├── test_document_sensor.py
    │   └── test_document_markdown.py
    └── integration/
        └── test_storage.py
```

---

## Tests

```bash
uv run pytest tests/ -v
# 60 tests, ~0.5s
```

---

## Adding a New Sensor

1. Create `capman/sensors/my_sensor.py`
2. Subclass `BaseSensor`, set `sensor_id` and `platform_support`
3. Implement `async def run(self)`
4. Done — `SensorRegistry` auto-discovers it via `pkgutil`, no registration needed

```python
from capman.sensors.base import BaseSensor
from capman.events import Event, EventType

class MySensor(BaseSensor):
    sensor_id = "my_sensor"
    platform_support = {"darwin", "linux", "win32"}

    async def run(self) -> None:
        while not self._stop.is_set():
            # ... capture something ...
            await self.emit(Event(
                type=EventType.SHELL_COMMAND,
                app="terminal",
                window_title="",
                payload={"command": "...", "cwd": "", "shell": "", "command_id": ""},
                sensor_id=self.sensor_id,
            ))
```
