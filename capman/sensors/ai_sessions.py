"""
AI coding-session sensor — captures local transcripts of terminal AI assistants.

Sources (all local files the user's own tools already wrote — no network, no keys):

  Claude Code   ~/.claude/projects/<slug>/<session-uuid>.jsonl   (append-only JSONL)
                (+ .../subagents/*.jsonl for spawned agents)
  Codex CLI     ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl      (append-only JSONL)
  Hermes        ~/.hermes/.hermes_history                         (human prompt log)
                ~/.hermes/state.db  (messages table)              (SQLite, id cursor)

For the append-only files we track a per-file line offset exactly like ShellSensor
tails shell history; for the Hermes DB we track the max `messages.id` seen. On the
first pass after a (re)start we replay a bounded tail of recent history so
"what was I doing an hour ago" works immediately
(config: sensors.ai_sessions.backfill_hours / backfill_max_lines).

Each captured user / assistant message becomes one AI_CONVERSATION event.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Iterable

from capman.events import Event, EventType
from capman.sensors.base import BaseSensor

logger = logging.getLogger(__name__)

_MAX_TEXT_CHARS = 2000

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)


def _parse_iso(ts: str | None) -> float | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        # tolerate trailing "Z"
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _flatten_content(content) -> str:
    """Message content → plain prose. Only real text blocks; tool calls, tool
    results, images, thinking are intentionally dropped — we want the human/model
    conversation, not the tool-plumbing that dominates an agent transcript."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    out: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in ("text", "input_text", "output_text"):
            txt = block.get("text") or ""
            if txt:
                out.append(txt)
    return "\n".join(s for s in out if s).strip()


def _has_tool_result(content) -> bool:
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") in ("tool_result", "tool_use")
        for b in content
    )


# Envelopes CLIs / orchestrators inject as fake "user" turns — command output,
# hook noise, IDE context, agent-bootstrap system prompts. Not things a human typed.
_NOISE_PREFIXES = (
    "<command-name>", "<local-command-", "<bash-", "<user-memory-input>",
    "Caveat: The messages below", "<system-reminder>", "[Request interrupted",
    "<environment_context", "<skills_instructions",
)
_NOISE_SUBSTR = (
    "file is current in your context",
    "has been updated successfully.",
    "tool ran without output",
)
# Paperclip/Hermes drop a long "You are <agent>, an AI agent employee ..." block
# in as the first user message of every autonomous run.
_AGENT_BOOTSTRAP_RE = re.compile(
    r"^You are .{0,80}?,\s*an AI agent\b|Paperclip runtime identity:", re.S
)
_AGENT_NAME_RE = re.compile(r"^You are ([A-Za-z0-9._-]{2,60}?),\s*an AI agent\b", re.S)

# Canned heartbeat replies an idle autonomous agent emits every cycle — no signal.
_TRIVIAL_REPLIES = frozenset({
    "nothing to do", "nothing to do.", "no action needed", "no action needed.",
    "no action required", "no action required.", "acknowledged", "acknowledged.",
    "no changes needed", "no changes needed.", "idle", "ok", "done",
})


def _hermes_label(title: str, session_id: str) -> str:
    """A short human label for a Hermes session. Paperclip stores the agent
    bootstrap prompt as the title (often ellipsis-truncated) — pull the agent
    name out of it instead."""
    title = (title or "").strip()
    m = _AGENT_NAME_RE.search(title)
    if m:
        return f"{m.group(1)} run"
    if title and not title.startswith("You are "):
        return title.split("\n", 1)[0][:120]
    # session_id like "paperclip:company:<uuid>:agent:<uuid>:issue:<uuid>"
    parts = [p for p in session_id.split(":") if p]
    if parts:
        return f"{parts[0]} run"
    return "hermes run"


def _is_noise(text: str) -> bool:
    head = text.lstrip()[:200]
    if head.startswith(_NOISE_PREFIXES):
        return True
    if _AGENT_BOOTSTRAP_RE.search(head):
        return True
    return any(s in head for s in _NOISE_SUBSTR)


class AISessionSensor(BaseSensor):
    sensor_id: ClassVar[str] = "ai_sessions"
    platform_support: ClassVar[set[str]] = {"darwin", "linux", "win32"}

    async def run(self) -> None:
        cfg = self.config.get("sensors", {}).get("ai_sessions", {})
        poll_interval_s = float(cfg.get("poll_interval_s", 3.0))
        backfill_cutoff = time.time() - float(cfg.get("backfill_hours", 12)) * 3600
        backfill_max_lines = int(cfg.get("backfill_max_lines", 200))

        roots = [
            Path(p).expanduser()
            for p in (cfg.get("roots") or ["~/.claude/projects", "~/.codex/sessions"])
        ]
        hermes_db = Path(cfg.get("hermes_db", "~/.hermes/state.db")).expanduser()
        hermes_history = Path(
            cfg.get("hermes_history", "~/.hermes/.hermes_history")
        ).expanduser()

        positions: dict[Path, int] = {}   # append-only file -> lines consumed
        self._hermes_cursor: int | None = None   # max messages.id seen
        first_pass = True

        while not self._stop_event.is_set():
            try:
                for path in self._iter_transcripts(roots):
                    try:
                        await self._consume_file(
                            path, positions, first_pass,
                            backfill_cutoff, backfill_max_lines,
                        )
                    except Exception as e:  # never let one bad file kill the loop
                        logger.debug("ai_sessions: error on %s: %s", path, e)
                try:
                    await self._consume_file(
                        hermes_history, positions, first_pass,
                        backfill_cutoff, backfill_max_lines,
                    )
                except Exception as e:
                    logger.debug("ai_sessions: hermes history error: %s", e)
                try:
                    await self._poll_hermes_db(
                        hermes_db, first_pass, backfill_cutoff, backfill_max_lines,
                    )
                except Exception as e:
                    logger.debug("ai_sessions: hermes db error: %s", e)
            except Exception as e:
                logger.debug("ai_sessions: scan error: %s", e)
            first_pass = False
            await asyncio.sleep(poll_interval_s)

    # ------------------------------------------------------------ append-only files

    @staticmethod
    def _iter_transcripts(roots: Iterable[Path]) -> Iterable[Path]:
        for root in roots:
            if not root.exists():
                continue
            for p in root.rglob("*.jsonl"):
                name = p.name
                if name.startswith("session_index") or name.endswith(".tmp"):
                    continue
                yield p

    async def _consume_file(
        self,
        path: Path,
        positions: dict[Path, int],
        first_pass: bool,
        backfill_cutoff: float,
        backfill_max_lines: int,
    ) -> None:
        try:
            lines = path.read_text(errors="replace").splitlines()
        except (FileNotFoundError, NotADirectoryError):
            return
        except OSError:
            return

        if path not in positions:
            if first_pass:
                recent = path.stat().st_mtime >= backfill_cutoff
                start = max(0, len(lines) - backfill_max_lines) if recent else len(lines)
            else:
                # Appeared while running — brand new, take it from the top.
                start = 0
            positions[path] = start

        prev = positions[path]
        if len(lines) <= prev:
            positions[path] = len(lines)  # truncation / rotation
            return

        tool = self._tool_for(path)
        if tool == "hermes-history":
            for evt in self._hermes_history_events(path, lines[prev:]):
                await self.emit(evt)
        else:
            for raw in lines[prev:]:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                evt = self._to_event(tool, path, obj)
                if evt is not None:
                    await self.emit(evt)
        positions[path] = len(lines)

    @staticmethod
    def _tool_for(path: Path) -> str:
        s = str(path).replace("\\", "/")
        if path.name == ".hermes_history":
            return "hermes-history"
        if "/.codex/" in s or path.name.startswith("rollout-"):
            return "codex"
        if "/.claude/" in s:
            return "claude-code"
        return "ai-cli"

    def _to_event(self, tool: str, path: Path, obj: dict) -> Event | None:
        if tool == "codex":
            return self._codex_event(path, obj)
        return self._claude_event(path, obj)

    # ---- Claude Code ----
    def _claude_event(self, path: Path, obj: dict) -> Event | None:
        kind = obj.get("type")
        if kind not in ("user", "assistant"):
            return None
        msg = obj.get("message")
        if not isinstance(msg, dict):
            return None
        # A "user" line that carries a tool result (or has toolUseResult set) is
        # the harness feeding output back to the model, not a human turn.
        if kind == "user" and (obj.get("toolUseResult") is not None
                               or _has_tool_result(msg.get("content"))):
            return None
        role = msg.get("role") or kind
        text = _flatten_content(msg.get("content"))
        if not text:
            return None
        if role == "user" and _is_noise(text):
            return None
        ts = _parse_iso(obj.get("timestamp")) or time.time()
        cwd = obj.get("cwd") or ""
        project = Path(cwd).name or path.parent.name
        is_sub = "/subagents/" in str(path)
        return Event(
            type=EventType.AI_CONVERSATION,
            app="claude-code",
            window_title=f"claude-code:{project}" + (" (subagent)" if is_sub else ""),
            ts=ts,
            payload={
                "tool": "claude-code",
                "role": role,
                "text": text[:_MAX_TEXT_CHARS],
                "text_chars": len(text),
                "cwd": cwd,
                "project": project,
                "model": msg.get("model", ""),
                "session_id": obj.get("sessionId") or obj.get("session_id") or "",
                "git_branch": obj.get("gitBranch", ""),
                "subagent": is_sub,
                "source_file": str(path),
            },
            sensor_id=self.sensor_id,
        )

    # ---- Codex CLI ----
    def _codex_event(self, path: Path, obj: dict) -> Event | None:
        if obj.get("type") != "response_item":
            return None
        payload = obj.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "message":
            return None
        role = payload.get("role")
        if role not in ("user", "assistant"):  # skip "developer"/"system" scaffolding
            return None
        text = _flatten_content(payload.get("content"))
        if not text or _is_noise(text):
            return None
        ts = _parse_iso(obj.get("timestamp")) or time.time()
        m = _UUID_RE.search(path.name)
        return Event(
            type=EventType.AI_CONVERSATION,
            app="codex",
            window_title="codex",
            ts=ts,
            payload={
                "tool": "codex",
                "role": role,
                "text": text[:_MAX_TEXT_CHARS],
                "text_chars": len(text),
                "project": "",
                "session_id": m.group(0) if m else path.stem,
                "source_file": str(path),
            },
            sensor_id=self.sensor_id,
        )

    # ---- Hermes: human prompt log (~/.hermes/.hermes_history) ----
    # Format: blocks of "# <ISO timestamp>" then one "+<line>" per line typed.
    def _hermes_history_events(self, path: Path, new_lines: list[str]) -> Iterable[Event]:
        ts = time.time()
        buf: list[str] = []

        def flush():
            nonlocal buf
            text = "\n".join(buf).strip()
            buf = []
            if not text or _is_noise(text):
                return None
            return Event(
                type=EventType.AI_CONVERSATION,
                app="hermes",
                window_title="hermes",
                ts=ts,
                payload={
                    "tool": "hermes",
                    "role": "user",
                    "text": text[:_MAX_TEXT_CHARS],
                    "text_chars": len(text),
                    "project": "",
                    "session_id": "",
                    "source_file": str(path),
                },
                sensor_id=self.sensor_id,
            )

        for line in new_lines:
            if line.startswith("# "):
                evt = flush()
                if evt is not None:
                    yield evt
                parsed = _parse_iso(line[2:].strip().replace(" ", "T"))
                ts = parsed or time.time()
            elif line.startswith("+"):
                buf.append(line[1:])
        evt = flush()
        if evt is not None:
            yield evt

    # ---- Hermes: agent message store (~/.hermes/state.db) ----
    async def _poll_hermes_db(
        self, db_path: Path, first_pass: bool,
        backfill_cutoff: float, backfill_max_lines: int,
    ) -> None:
        if not db_path.exists():
            return
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            if not cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
            ).fetchone():
                return

            if self._hermes_cursor is None:
                if first_pass:
                    row = cur.execute(
                        "SELECT MIN(id) AS lo FROM (SELECT id FROM messages "
                        "WHERE timestamp >= ? ORDER BY id DESC LIMIT ?)",
                        (backfill_cutoff, backfill_max_lines),
                    ).fetchone()
                    if row and row["lo"] is not None:
                        self._hermes_cursor = row["lo"] - 1
                if self._hermes_cursor is None:
                    row = cur.execute("SELECT COALESCE(MAX(id), 0) AS hi FROM messages").fetchone()
                    self._hermes_cursor = row["hi"]
                    return

            rows = cur.execute(
                """SELECT m.id, m.session_id, m.role, m.content, m.timestamp,
                          s.title AS s_title, s.model AS s_model, s.source AS s_source,
                          s.cwd AS s_cwd, s.git_branch AS s_branch
                   FROM messages m
                   LEFT JOIN sessions s ON s.id = m.session_id
                   WHERE m.id > ? AND m.role IN ('user', 'assistant')
                   ORDER BY m.id ASC LIMIT 500""",
                (self._hermes_cursor,),
            ).fetchall()

            for r in rows:
                self._hermes_cursor = r["id"]
                text = (r["content"] or "").strip()
                if not text or _is_noise(text):
                    continue
                if r["role"] == "assistant" and text.lower() in _TRIVIAL_REPLIES:
                    continue
                label = _hermes_label(r["s_title"] or "", r["session_id"] or "")
                await self.emit(Event(
                    type=EventType.AI_CONVERSATION,
                    app="hermes",
                    window_title=f"hermes: {label}",
                    ts=float(r["timestamp"]) if r["timestamp"] else time.time(),
                    payload={
                        "tool": "hermes",
                        "role": r["role"],
                        "text": text[:_MAX_TEXT_CHARS],
                        "text_chars": len(text),
                        "project": label,
                        "model": r["s_model"] or "",
                        "session_id": r["session_id"] or "",
                        "git_branch": r["s_branch"] or "",
                        "hermes_source": r["s_source"] or "",
                        "source_file": str(db_path),
                    },
                    sensor_id=self.sensor_id,
                ))
        finally:
            conn.close()
