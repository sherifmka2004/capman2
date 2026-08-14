"""Persist troubleshooting playbooks as Obsidian-compatible Markdown.

One file per playbook under `<knowledge_dir>/playbooks/<domain>/`, with YAML
frontmatter so the vault can be queried by domain and reusability.

Playbooks are made searchable by the pipeline writing them into the
`documents` table, which feeds both the BM25 and the vector ranker — this
module only owns the human-readable artifact.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from capman.events import TroubleshootingPlaybook

logger = logging.getLogger(__name__)

_PLAYBOOKS_SUBDIR = "playbooks"


def _slug(text: str, maxlen: int = 60) -> str:
    """Filename-safe, hyphen-separated slug.

    Bounded length because these become filenames, and a long LLM-generated
    title would otherwise blow past filesystem limits.
    """
    s = re.sub(r"[^\w\s-]", "", (text or "").lower(), flags=re.UNICODE)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:maxlen] or "playbook"


def save_playbook_markdown(playbook: TroubleshootingPlaybook, knowledge_dir: Path | str) -> Path:
    """Write the playbook as Markdown; return the path written."""
    knowledge_dir = Path(knowledge_dir).expanduser()
    domain = playbook.domain or "general"
    out_dir = knowledge_dir / _PLAYBOOKS_SUBDIR / _slug(domain, 30)
    out_dir.mkdir(parents=True, exist_ok=True)

    path = out_dir / f"{_slug(playbook.title)}-{playbook.id[:8]}.md"

    created_at = getattr(playbook, "created_at", None) or time.time()
    lines: list[str] = [
        "---",
        f'id: "{playbook.id}"',
        f'session_id: "{playbook.session_id}"',
        "node_type: troubleshooting_playbook",
        f'domain: "{playbook.domain}"',
        f"reusability_score: {playbook.reusability_score}",
        f"created_at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(created_at))}",
        "---",
        "",
        f"# {playbook.title}",
        "",
    ]

    if playbook.symptoms:
        lines += ["## When This Applies (Symptoms)", ""]
        lines += [f"- {s}" for s in playbook.symptoms]
        lines.append("")

    if playbook.context_signals:
        lines += ["## Context Signals", ""]
        lines += [f"- {c}" for c in playbook.context_signals]
        lines.append("")

    if playbook.diagnostic_steps:
        lines += ["## Diagnostic Steps", ""]
        for step in playbook.diagnostic_steps:
            lines.append(f"### {step.sequence}. {step.action}")
            if step.tool:
                lines.append(f"- **Tool:** `{step.tool}`")
            if step.rationale:
                lines.append(f"- **Why:** {step.rationale}")
            if step.expected_signal:
                lines.append(f"- **Expected signal:** {step.expected_signal}")
            lines.append("")

    if playbook.root_cause:
        lines += ["## Root Cause", "", playbook.root_cause, ""]

    if playbook.fix:
        lines += ["## Fix", ""]
        lines += [f"{i}. {f}" for i, f in enumerate(playbook.fix, start=1)]
        lines.append("")

    if playbook.verification:
        lines += ["## Verification", ""]
        lines += [f"- [ ] {v}" for v in playbook.verification]
        lines.append("")

    if playbook.references:
        lines += ["## References", ""]
        lines += [f"- {r}" for r in playbook.references]
        lines.append("")

    if playbook.related_playbooks:
        lines += ["## Related Playbooks", ""]
        lines += [f"- [[{r}]]" for r in playbook.related_playbooks]
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def index_playbook_in_vector_store(playbook: TroubleshootingPlaybook, chroma_path: str = "") -> None:
    """Deprecated no-op, kept so older callers do not break.

    Playbooks are indexed by the pipeline writing them into the `documents`
    table, which feeds both the BM25 and the vector ranker. The ChromaDB path
    this used to take was removed along with capman.storage.vector; leaving the
    import here would raise ImportError at runtime.
    """
    logger.debug("index_playbook_in_vector_store is a no-op; playbooks are "
                 "indexed via the documents table (playbook %s)", playbook.id)
