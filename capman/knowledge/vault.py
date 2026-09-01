"""Write Capman's derived, evidence-linked knowledge as an OKF-compatible vault.

The vault is deliberately a projection of the private timeline, never another
capture store.  It contains only analysed summaries, extracted concepts and
playbooks, and each generated page retains stable ``capman://`` evidence links.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from capman.events import SessionAnalysis, TroubleshootingPlaybook
from capman.knowledge.graph import KnowledgeGraph
from capman.knowledge.privacy import redact_derived_text


def _slug(value: str, fallback: str = "item") -> str:
    value = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return value[:80] or fallback


def _yaml_scalar(value: object) -> str:
    """Emit a safe YAML scalar without adding a YAML dependency."""
    return json.dumps(value, ensure_ascii=False)


def _timestamp(value: float | None) -> str:
    return datetime.fromtimestamp(value or time.time(), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CuratedKnowledgeVault:
    """Single-purpose writer for the portable, derived knowledge projection."""

    def __init__(self, root: str | Path, *, redact: bool = True) -> None:
        self.root = Path(root).expanduser()
        self.redact = redact

    def write_session(self, analysis: SessionAnalysis) -> Path | None:
        title = self._clean(analysis.problem_statement).strip()
        if not title:
            return None
        body = [f"# {title}", "", "## Approach", "", self._clean(analysis.approach_description), ""]
        self._section(body, "Methodology", analysis.methodology_tags)
        self._section(body, "Knowledge Applied", analysis.knowledge_applied)
        self._section(body, "Knowledge Acquired", analysis.knowledge_acquired)
        body += ["## Evidence", "", f"- [Captured session](capman://session/{analysis.session_id})", ""]
        path = self.root / "sessions" / f"{analysis.session_id}.md"
        self._write(
            path,
            kind="session",
            title=title,
            description=self._clean(analysis.approach_description)[:240],
            resource=f"capman://session/{analysis.session_id}",
            tags=list(analysis.methodology_tags or []),
            timestamp=analysis.analyzed_at,
            body=body,
        )
        self._append_log("session", title, path)
        self.refresh_index()
        return path

    def write_playbook(self, playbook: TroubleshootingPlaybook) -> Path:
        title = self._clean(playbook.title)
        body = [f"# {title}", "", "## When This Applies", ""]
        body += [f"- {self._clean(v)}" for v in playbook.symptoms] or ["- No symptoms recorded."]
        body += ["", "## Diagnostic Steps", ""]
        for step in playbook.diagnostic_steps:
            body.append(f"{step.sequence}. {self._clean(step.action)}")
            if step.rationale:
                body.append(f"   - Why: {self._clean(step.rationale)}")
            if step.expected_signal:
                body.append(f"   - Expected signal: {self._clean(step.expected_signal)}")
        if playbook.root_cause:
            body += ["", "## Root Cause", "", self._clean(playbook.root_cause)]
        self._section(body, "Fix", playbook.fix, numbered=True)
        self._section(body, "Verification", playbook.verification)
        body += ["## Evidence", "", f"- [Captured session](capman://session/{playbook.session_id})", ""]
        path = self.root / "playbooks" / _slug(playbook.domain, "general") / f"{_slug(title)}-{playbook.id[:8]}.md"
        self._write(
            path,
            kind="playbook",
            title=title,
            description=self._clean(playbook.root_cause)[:240],
            resource=f"capman://playbook/{playbook.id}",
            tags=[playbook.domain, "troubleshooting"],
            timestamp=playbook.created_at,
            body=body,
            extra={"capman_session": playbook.session_id, "reusability_score": playbook.reusability_score},
        )
        self._append_log("playbook", title, path)
        self.refresh_index()
        return path

    def write_concepts(self, graph: KnowledgeGraph) -> int:
        written = 0
        for node in graph.nodes.values():
            title = self._clean(node.title)
            if not title:
                continue
            body = [f"# {title}", "", "## Relationships", ""]
            if node.outgoing_edges:
                for edge in node.outgoing_edges:
                    target = graph.nodes.get(edge.target_id)
                    label = self._clean(target.title if target else edge.target_id)
                    body.append(f"- {self._clean(edge.predicate)} → [{label}](../concepts/{edge.target_id}.md)")
            else:
                body.append("- No extracted relationships yet.")
            body += ["", "## Evidence", ""]
            body += [f"- [Captured session](capman://session/{sid})" for sid in node.source_sessions]
            body.append("")
            path = self.root / "concepts" / f"{node.id}.md"
            self._write(
                path, kind="concept", title=title, description=self._clean(node.summary)[:240],
                resource=f"capman://concept/{node.id}", tags=list(node.tags or []),
                timestamp=node.last_updated, body=body,
                extra={"capman_source_sessions": list(node.source_sessions)},
            )
            written += 1
        if written:
            self.refresh_index()
        return written

    def refresh_index(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        lines = ["---", "type: index", 'title: "Capman Knowledge Vault"',
                 'description: "Redacted, derived knowledge generated from captured work."',
                 f"timestamp: {_yaml_scalar(_timestamp(time.time()))}", "---", "",
                 "# Capman Knowledge Vault", "",
                 "This is a derived, evidence-linked projection of Capman data. Raw events, screenshots, page text, document text, keystrokes, and clipboard contents are intentionally absent.", ""]
        for label, directory in (("Playbooks", "playbooks"), ("Concepts", "concepts"), ("Session Summaries", "sessions")):
            files = sorted((self.root / directory).rglob("*.md")) if (self.root / directory).exists() else []
            lines += [f"## {label}", ""]
            lines += [f"- [{p.stem}]({p.relative_to(self.root).as_posix()})" for p in files] or ["- None yet."]
            lines.append("")
        path = self.root / "index.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def _write(self, path: Path, *, kind: str, title: str, description: str, resource: str,
               tags: Iterable[str], timestamp: float | None, body: list[str], extra: dict | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        clean_tags = [self._clean(str(tag)) for tag in tags if str(tag).strip()]
        frontmatter = ["---", f"type: {kind}", f"title: {_yaml_scalar(title)}",
                       f"description: {_yaml_scalar(description)}", f"resource: {_yaml_scalar(resource)}",
                       f"tags: {_yaml_scalar(clean_tags)}", f"timestamp: {_yaml_scalar(_timestamp(timestamp))}"]
        for key, value in (extra or {}).items():
            frontmatter.append(f"{key}: {_yaml_scalar(value)}")
        path.write_text("\n".join(frontmatter + ["---", ""] + body), encoding="utf-8")

    def _clean(self, value: str) -> str:
        return redact_derived_text(value) if self.redact else (value or "")

    def _section(self, body: list[str], title: str, values: Iterable[str], *, numbered: bool = False) -> None:
        values = [self._clean(str(value)) for value in values if str(value).strip()]
        if not values:
            return
        body += [f"## {title}", ""]
        body += [f"{index}. {value}" if numbered else f"- {value}" for index, value in enumerate(values, 1)]
        body.append("")

    def _append_log(self, action: str, title: str, path: Path) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        log = self.root / "log.md"
        relative = path.relative_to(self.root).as_posix()
        line = f"## [{_timestamp(time.time())}] {action} | [{title}]({relative})\n"
        if not log.exists() or line not in log.read_text(encoding="utf-8"):
            with log.open("a", encoding="utf-8") as handle:
                handle.write(line)


async def export_derived_vault(db, root: str | Path, *, redact: bool = True,
                               knowledge_dir: str | Path | None = None) -> dict[str, int]:
    """Rebuild a vault from durable derived records, never from raw events.

    This is intentionally idempotent so it can power both a manual export and
    recovery after the vault is moved or deleted.
    """
    from capman.events import DiagnosticStep

    vault = CuratedKnowledgeVault(root, redact=redact)
    counts = {"sessions": 0, "playbooks": 0, "concepts": 0}
    async with db._db.execute(
        "SELECT session_id, problem_statement, approach_description, methodology_tags, "
        "knowledge_applied, knowledge_acquired, confidence, model_used, analyzed_at "
        "FROM session_analyses ORDER BY analyzed_at"
    ) as cursor:
        for row in await cursor.fetchall():
            analysis = SessionAnalysis(
                session_id=row["session_id"], problem_statement=row["problem_statement"] or "",
                approach_description=row["approach_description"] or "",
                methodology_tags=json.loads(row["methodology_tags"] or "[]"),
                knowledge_applied=json.loads(row["knowledge_applied"] or "[]"),
                knowledge_acquired=json.loads(row["knowledge_acquired"] or "[]"),
                confidence=row["confidence"] or 0.0, model_used=row["model_used"] or "",
                analyzed_at=row["analyzed_at"] or time.time(),
            )
            if vault.write_session(analysis):
                counts["sessions"] += 1
    async with db._db.execute("SELECT * FROM playbooks ORDER BY created_at") as cursor:
        for row in await cursor.fetchall():
            steps = [DiagnosticStep(**step) for step in json.loads(row["diagnostic_steps"] or "[]")]
            playbook = TroubleshootingPlaybook(
                id=row["id"], session_id=row["session_id"] or "", title=row["title"] or "",
                domain=row["domain"] or "", symptoms=json.loads(row["symptoms"] or "[]"),
                context_signals=json.loads(row["context_signals"] or "[]"), diagnostic_steps=steps,
                root_cause=row["root_cause"] or "", fix=json.loads(row["fix"] or "[]"),
                verification=json.loads(row["verification"] or "[]"),
                references=json.loads(row["references_json"] or "[]"),
                related_playbooks=json.loads(row["related_playbooks"] or "[]"),
                reusability_score=row["reusability_score"] or 0.0,
                created_at=row["created_at"] or time.time(),
            )
            vault.write_playbook(playbook)
            counts["playbooks"] += 1
    if knowledge_dir:
        graph = KnowledgeGraph(str(knowledge_dir))
        graph.load()
        counts["concepts"] = vault.write_concepts(graph)
    vault.refresh_index()
    return counts
