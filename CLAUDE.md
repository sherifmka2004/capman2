# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

**capman2** is a personal cognitive workflow capture engine that records how you work — not just what you accomplish, but the *thinking process* that got you there. It captures activity across keyboard, mouse, files, shell, documents, and browsers, groups events into sessions, and uses Claude AI to extract chains-of-thought, decision patterns, and reusable methodologies.

## Development Setup

### Prerequisites
- Python 3.11–3.12
- [uv](https://docs.astral.sh/uv/) (Python package manager, installed by `install.sh`)
- For OCR: `tesseract` (optional but recommended for screenshot text extraction)

### Install Dependencies
```bash
uv sync
```

This installs all runtime and dev dependencies, including pytest, pytest-asyncio, and pytest-mock.

## Common Commands

### Running Tests
```bash
# Run all tests
uv run pytest tests/ -v

# Run a single test file
uv run pytest tests/unit/test_events.py -v

# Run a single test
uv run pytest tests/unit/test_events.py::test_event_has_auto_id_and_ts -v

# Run tests with coverage
uv run pytest tests/ --cov=capman --cov-report=term-missing
```

Tests use pytest with asyncio mode enabled (see `pyproject.toml: asyncio_mode = "auto"`).

### Running the Application

**Desktop mode** (with system tray + browser UI):
```bash
uv run capman-desktop
```

**Daemon mode** (CLI):
```bash
uv run capman start
uv run capman stop
uv run capman status
uv run capman query "search term"
```

### Building for Release

**Desktop executable** (PyInstaller):
```bash
uv pip install pyinstaller
uv run pyinstaller capman.spec --noconfirm

# Output:
#   macOS:   dist/capman2.app
#   Linux:   dist/capman2/capman2
#   Windows: dist\capman2\capman2.exe
```

**Cross-platform builds** are automated via GitHub Actions (`.github/workflows/build.yml`) on version tag pushes.

## Architecture Overview

### Data Flow

```
Sensors → AsyncEventBuffer → SessionDetector → Enricher → LLM Analyzer → Storage
```

**Sensors** capture raw input from 10 pluggable sources (window, keyboard, mouse, screenshot, filesystem, shell, browser, documents, clipboard, idle). Each sensor runs in its own async task and emits `Event` objects into a thread-safe queue.

**SessionDetector** groups events into sessions using a sliding-window state machine (IDLE → ACTIVE → COOL). Sessions break on long idle periods, hard timeouts, or window blur.

**Enricher** post-processes sessions: OCR on screenshots, URL normalization, structured data extraction.

**LLM Analyzer** runs 3 sequential passes:
  - **Pass 1** (Haiku): Summarization — problem statement, approach, methodology tags, confidence
  - **Pass 2** (Sonnet): Chain-of-Thought extraction — cognitive steps, decision points, outcome (only for high-reusability sessions)
  - **Pass 3** (Haiku): Triple extraction — knowledge graph nodes/edges

**Storage** persists everything:
  - **SQLite** (`~/.capman/timeline.db`): raw events, sessions, analysis summaries
  - **ChromaDB** (`~/.capman/chroma`): semantic embeddings for vector search
  - **Markdown** (`~/.capman/knowledge`): Obsidian-compatible knowledge graph

### Key Modules

#### `capman/events.py`
All data models are here: `Event`, `EventType`, `Session`, `SessionAnalysis`, `ChainOfThought`, `CognitiveStep`, `Triple`, `TroubleshootingPlaybook`, etc. This is the canonical source of truth for all types.

#### `capman/sensors/`
Plugin architecture for capture inputs. Each sensor:
  - Subclasses `BaseSensor` (defines `sensor_id`, `platform_support`, `async run()`)
  - Emits `Event` objects via `await self.emit(event)`
  - Is auto-discovered by `SensorRegistry` (no registration needed)

Notable sensors:
  - `filesystem.py`: File ops with attribution (user vs. build tool) via activity context + focused window
  - `documents.py`: Slide/page/sheet navigation with dwell gating; triggers content extraction
  - `mouse.py`: Click enrichment via OS accessibility tree (50ms timeout); scroll coalescing; per-app heatmaps
  - `screenshot.py`: Event-triggered + periodic with configurable format/quality/retention

#### `capman/pipeline/`
Orchestrates event flow through buffering, session detection, enrichment, and analysis.

  - `buffer.py`: `AsyncEventBuffer` wraps `asyncio.Queue` for thread-safe emit
  - `session.py`: `SessionDetector` state machine (tracks idle/active/cool state, emits Session objects)
  - `analyzer.py`: `SessionAnalyzer` orchestrates 3-pass LLM analysis
  - `runner.py`: `PipelineRunner` wires everything together, handles graceful shutdown, triggers analysis async

#### `capman/storage/`
Persistence layer.

  - `timeline.py`: `TimelineDB` — async SQLite adapter for events/sessions/analysis CRUD
  - `vector.py`: `VectorDB` — ChromaDB integration for semantic embeddings + similarity search
  - `schema.sql`: Version-controlled DDL (migrations auto-run on startup)

#### `capman/api/`
FastAPI server (localhost:7331 by default).

  - `server.py`: App factory with CORS, route registration
  - `routes/chat.py`: `/chat/message` — stateless chat endpoint with context window building
  - `routes/brain.py`: `/brain/analyze` — manual session re-analysis, graph introspection
  - `routes/sessions.py`, `events.py`, `knowledge.py`: CRUD + semantic search

#### `capman/platform/`
OS-specific adapters for document content extraction.

  - `base.py`: `PlatformAdapter` ABC + app classification registry
  - `macos.py`: AppleScript for PowerPoint, Keynote, Notes, Word, Pages, Numbers, Finder
  - `linux.py`: LibreOffice UNO + xdotool
  - `windows.py`: pywin32 COM automation

#### `capman/pipeline/prompts.py`
Versioned LLM prompt templates for all 3 analysis passes. Includes event narrative building (compacts 100+ events into structured text).

#### `capman/main.py`
CLI entry point. Commands: `start`, `stop`, `status`, `query`, `storage`. Daemon orchestration: spawns sensors, pipeline, API server in async tasks; handles graceful shutdown.

### Configuration System

Config is TOML-based, merged hierarchically:

1. `config/default.toml` — all defaults
2. `config/{platform}.toml` — macOS/Linux/Windows overrides (e.g. different file watch paths)
3. `~/.capman/config.toml` — user overrides (secrets, custom thresholds)
4. Named overlays — e.g., `--headless` applies `config/headless.toml` (disables display sensors for servers)

Key sections:
  - `[core]`: data_dir, log_level
  - `[sensors]`: enabled list + per-sensor config (screenshot interval, idle threshold, etc.)
  - `[pipeline.analysis]`: model IDs (Haiku/Sonnet), batch delays, reusability threshold
  - `[api]`: port, chat model, context window limits
  - `[storage]`: SQLite/ChromaDB/Obsidian paths

Config loader auto-expands `~` in paths and does deep merging (dicts merge recursively).

### Adding a New Sensor

1. Create `capman/sensors/my_sensor.py`
2. Subclass `BaseSensor`, set `sensor_id` and `platform_support` class vars
3. Implement `async def run(self)` — check `self._stop_event.is_set()` in loop, emit events via `await self.emit(...)`
4. Done — `SensorRegistry` auto-discovers via `pkgutil`

No explicit registration or imports needed anywhere else.

### Desktop vs. Daemon Mode

**Desktop** (`capman-desktop`):
  - Launches daemon in background thread
  - Spawns system tray icon (pystray)
  - Opens browser to `http://localhost:7331` automatically
  - Graceful shutdown via tray menu or signal handler

**Daemon** (`capman start`):
  - Runs in foreground (or can be backgrounded)
  - Writes PID file to `~/.capman/capman.pid`
  - `capman stop` sends SIGTERM via PID file
  - Useful for servers (detects headless via `DISPLAY`/`WAYLAND_DISPLAY` env vars)

### Privileged Deep File Monitor

`tools/capman-fsmon/fsmon.py` is an optional root-level helper that captures true file opens/reads + the responsible process PID/comm/exe. Backends:

  - **Linux**: `fanotify` (default, fastest), `auditd` (fallback), `eBPF` (bpftrace fallback)
  - **macOS**: `eslogger` (Endpoint Security, needs Full Disk Access), `fs_usage` (fallback)

Runs separately; POSTs events to daemon's `/events` endpoint. Config key: `sensors.filesystem.deep_monitor`.

### Testing

**Test structure**:
  - `tests/unit/` — isolated logic tests (events, session detector, graph merger, document extraction)
  - `tests/integration/` — end-to-end pipeline tests (full event flow through storage)

**Key patterns**:
  - Async tests use `@pytest.mark.asyncio` (enabled globally via `asyncio_mode = "auto"`)
  - Mock LLM responses to avoid API calls in unit tests
  - Fixtures provide in-memory SQLite DBs for storage tests

**Running a subset**:
```bash
uv run pytest tests/unit/ -v                    # Unit tests only
uv run pytest tests/integration/ -v             # Integration tests only
uv run pytest tests/ -k "session" -v            # Tests matching "session"
uv run pytest tests/unit/test_events.py -v     # Single file
```

## Important Notes

### LLM Backend Configuration

Analysis is controlled by environment variables:

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # Uses Anthropic SDK (preferred)
# OR
export OPENROUTER_API_KEY=sk-or-v1-...  # Uses OpenRouter (OpenAI-compatible fallback)
```

Both support Claude Haiku (Pass 1/3) and Claude Sonnet (Pass 2) models. Backend auto-detection in `capman/pipeline/analyzer.py`.

### Headless Detection

`capman start` auto-detects headless servers (Linux without `DISPLAY`/`WAYLAND_DISPLAY`) and applies the headless config overlay. Override with `--headless` flag.

### File Attribution

The filesystem sensor attributes file ops to direct user action (editor, interactive shell, focused file manager) vs. background tools (build, LSP, watchers, package managers). Attribution logic:

  - Checks focused window at time of event
  - Correlates with recently-captured shell commands
  - (With fsmon helper) inspects process ancestry (is parent a TTY-attached tool?)
  - Config gates: `user_only`, `keep_unknown`, `foreground_window_grace_s`, `shell_correlate_s`

### Shell Integration

`shell/capman-init.sh` is a bash/zsh hook that captures every command (real-time, not history-file polling). Source in `.bashrc`/`.zshrc`. Sends command text + exit code + duration + CWD + PID to daemon via async Python subprocess.

### Document Content Extraction

When a user navigates to a slide/page/sheet, the document sensor checks dwell time (configurable, default 4s). If met, it triggers content extraction via:

  1. **App model** (platform adapter AppleScript / UNO / COM) — fastest, most reliable
  2. **OCR fallback** (screenshot + Tesseract/Vision) — slower but works when app integration isn't available

Full text goes to ChromaDB; SQLite keeps 300-char excerpt. Configurable order, per-doc caps, OCR backend.

## Code Quality & Testing

All changes should be tested. Run `uv run pytest tests/ -v` before pushing. Integration tests exercise the full event → session → storage → analysis pipeline.

The project uses:
- **pytest** for test framework
- **pytest-asyncio** for async test support (auto mode enabled)
- **aiosqlite** for non-blocking SQLite
- **chromadb** for vector storage
- **fastapi** + **uvicorn** for the HTTP API
- **anthropic** SDK and **httpx** for LLM calls
- **pynput**, **mss**, **watchdog** for input/screen/file monitoring

