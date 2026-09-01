"""Privacy helpers shared by every derived-knowledge export surface.

Raw capture is intentionally never passed to these helpers.  They reduce the
risk that a useful derived summary accidentally carries a secret or a direct
identifier into a portable Markdown vault.
"""
from __future__ import annotations

import re


_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"),
    (re.compile(r"(?:/Users|/home)/[^/\s]+"), "[HOME]"),
    (re.compile(r"[A-Za-z]:\\\\Users\\\\[^\\\\\s]+"), "[HOME]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "[IP]"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|bearer)\b\s*[:=]\s*\S+"),
     r"\1=[REDACTED]"),
    (re.compile(r"\b(?:sk|pk|ghp|gho|xox[baprs])-[A-Za-z0-9_\-]{10,}"), "[CREDENTIAL]"),
)


def redact_derived_text(text: str) -> str:
    """Redact common secret and direct-identifier shapes from derived text."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text or "")
    return text
