"""Persist troubleshooting playbooks as Markdown and index them in ChromaDB."""
from __future__ import annotations

import logging
from pathlib import Path

from capman.events import TroubleshootingPlaybook

logger = logging.getLogger(__name__)

_PLAYBOOKS_SUBDIR = "playbooks"


def save_playbook_markdown(playbook: TroubleshootingPlaybook, knowledge_dir: Path) -> Path:
    """Write playbook as a Markdown file; return the path written."""
    out_dir = knowledge_dir / _PLAYBOOKS_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in playbook.title)[:80]
    filename = f"{playbook.id[:8]}_{safe_title.replace(' ', '_')}.md"
    path = out_dir / filename

    lines: list[str] = [f"# {playbook.title}", ""]

    if playbook.domain:
        lines += [f"**Domain:** {playbook.domain}", ""]

    if playbook.symptoms:
        lines += ["## Symptoms", ""]
        lines += [f"- {s}" for s in playbook.symptoms]
        lines.append("")

    if playbook.context_signals:
        lines += ["## Context signals", ""]
        lines += [f"- {s}" for s in playbook.context_signals]
        lines.append("")

    if playbook.diagnostic_steps:
        lines += ["## Diagnostic steps", ""]
        for step in playbook.diagnostic_steps:
            lines.append(f"### Step {step.sequence}: {step.action}")
            if step.rationale:
                lines.append(f"*Rationale:* {step.rationale}")
            if step.expected_signal:
                lines.append(f"*Expected signal:* {step.expected_signal}")
            if step.tool:
                lines.append(f"*Tool:* `{step.tool}`")
            lines.append("")

    if playbook.root_cause:
        lines += ["## Root cause", "", playbook.root_cause, ""]

    if playbook.fix:
        lines += ["## Fix", ""]
        lines += [f"- {f}" for f in playbook.fix]
        lines.append("")

    if playbook.verification:
        lines += ["## Verification", ""]
        lines += [f"- {v}" for v in playbook.verification]
        lines.append("")

    if playbook.references:
        lines += ["## References", ""]
        lines += [f"- {r}" for r in playbook.references]
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
