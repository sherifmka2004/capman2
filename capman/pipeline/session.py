"""
SessionDetector — sliding-window state machine that groups events into problem-solving sessions.

States: IDLE → ACTIVE → COOLING_DOWN → (IDLE | ACTIVE)

A "session" is a contiguous episode of meaningful computer activity around a
common topic/problem. Sessions are the unit of LLM analysis.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from urllib.parse import urlparse

from capman.events import Event, EventType, Session

logger = logging.getLogger(__name__)

_INSIGNIFICANT = frozenset({EventType.WINDOW_FOCUS, EventType.WINDOW_BLUR, EventType.MOUSE_CLICK})


class SessionDetector:
    def __init__(self, config: dict):
        cfg = config.get("pipeline", {}).get("session", {})
        self.idle_threshold_s: float = cfg.get("idle_threshold_s", 90)
        self.cool_period_s: float = cfg.get("cool_period_s", 120)
        self.hard_break_s: float = cfg.get("hard_break_s", 1200)
        self.min_events: int = cfg.get("min_session_events", 5)

        self._current: Session | None = None
        self._state: str = "IDLE"  # IDLE | ACTIVE | COOLING
        self._last_significant_ts: float = 0.0
        self._app_counts: Counter = Counter()
        self._domain_counts: Counter = Counter()

    def ingest(self, event: Event) -> tuple[Session | None, Session | None]:
        """
        Process one event.
        Returns: (completed_session | None, current_session | None)
        completed_session is non-None only when a session just closed.
        """
        now = event.ts
        is_sig = self._is_significant(event)

        completed: Session | None = None

        if self._state == "IDLE":
            if is_sig:
                self._start_session(event)
                self._state = "ACTIVE"

        elif self._state == "ACTIVE":
            if is_sig:
                if self._should_break(event):
                    completed = self._close_current()
                    self._start_session(event)
                    # State stays ACTIVE
                else:
                    self._add_event(event)
                    self._last_significant_ts = now
            else:
                self._add_event(event)
                # Check idle
                if now - self._last_significant_ts > self.idle_threshold_s:
                    self._state = "COOLING"

        elif self._state == "COOLING":
            if is_sig:
                if self._should_break(event):
                    completed = self._close_current()
                    self._start_session(event)
                    self._state = "ACTIVE"
                else:
                    self._add_event(event)
                    self._last_significant_ts = now
                    self._state = "ACTIVE"
            else:
                if now - self._last_significant_ts > self.idle_threshold_s + self.cool_period_s:
                    completed = self._close_current()
                    self._state = "IDLE"

        return completed, self._current

    def check_timeouts(self) -> tuple[Session | None, Session | None]:
        """Call periodically to flush sessions that timed out without new events."""
        if self._current is None or self._state == "IDLE":
            return None, None

        now = time.time()
        if self._state in ("ACTIVE", "COOLING"):
            elapsed = now - self._last_significant_ts
            if elapsed > self.idle_threshold_s + self.cool_period_s:
                completed = self._close_current()
                self._state = "IDLE"
                return completed, None

        return None, self._current

    def flush(self) -> Session | None:
        """Force-close the current session (called on daemon shutdown)."""
        if self._current:
            return self._close_current()
        return None

    def _is_significant(self, event: Event) -> bool:
        if event.type in _INSIGNIFICANT:
            return False
        if event.type == EventType.KEYSTROKE:
            return len(event.payload.get("text", "")) >= 3
        return True

    def _should_break(self, event: Event) -> bool:
        if self._current is None:
            return False
        now = event.ts

        # Hard time break
        if now - self._current.started_at > self.hard_break_s:
            return True

        breaks = 0

        # App change
        if event.app and event.app != self._current.dominant_app:
            breaks += 1

        # Domain change
        if event.type == EventType.URL_VISIT:
            domain = self._extract_domain(event.payload.get("url", ""))
            if domain and domain != self._current.primary_domain and self._current.primary_domain:
                breaks += 1

        return breaks >= 2

    def _start_session(self, event: Event) -> None:
        self._current = Session(
            started_at=event.ts,
            dominant_app=event.app,
        )
        self._app_counts = Counter()
        self._domain_counts = Counter()
        self._last_significant_ts = event.ts
        self._add_event(event)

    def _add_event(self, event: Event) -> None:
        if self._current is None:
            return
        self._current.events.append(event)

        if event.app:
            self._app_counts[event.app] += 1
            self._current.dominant_app = self._app_counts.most_common(1)[0][0]

        if event.type == EventType.SEARCH_QUERY:
            q = event.payload.get("query", "")
            if q and q not in self._current.search_queries:
                self._current.search_queries.append(q)

        if event.type == EventType.URL_VISIT:
            url = event.payload.get("url", "")
            if url and url not in self._current.urls_visited:
                self._current.urls_visited.append(url)
            domain = self._extract_domain(url)
            if domain:
                self._domain_counts[domain] += 1
                self._current.primary_domain = self._domain_counts.most_common(1)[0][0]

        if event.type == EventType.SHELL_COMMAND:
            cmd = event.payload.get("command", "")
            if cmd and cmd not in self._current.commands_run:
                self._current.commands_run.append(cmd)

    def _close_current(self) -> Session | None:
        if self._current is None:
            return None
        self._current.ended_at = time.time()
        session = self._current
        self._current = None
        self._app_counts = Counter()
        self._domain_counts = Counter()
        logger.info(
            "Session closed: id=%s events=%d app=%s duration=%.0fs",
            session.id, len(session.events), session.dominant_app,
            (session.ended_at or 0) - session.started_at,
        )
        return session

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower()
            # Strip www.
            if host.startswith("www."):
                host = host[4:]
            return host
        except Exception:
            return ""
