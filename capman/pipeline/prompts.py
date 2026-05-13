"""
Versioned LLM prompt templates for the 3-pass analysis strategy.

Pass 1 — Summarize (fast, cheap, every session):
  Input:  condensed event log (URLs, searches, commands)
  Output: problem_statement, approach, methodology_tags, reusability_estimate

Pass 2 — Chain-of-Thought Extract (expensive, high-value sessions only):
  Input:  Pass 1 output + full enriched event narrative
  Output: ChainOfThought (steps, decision_points, methodology_pattern)

Pass 3 — Triple Extract (targeted, runs after Pass 2):
  Input:  Pass 2 output (already distilled)
  Output: list[Triple] for the knowledge graph
"""
from __future__ import annotations

PASS1_SUMMARIZE = """You are analyzing a computer activity log to extract what problem a person was solving.

## Activity Summary
Duration: {duration_minutes:.1f} minutes
Primary app: {dominant_app}
Primary domain: {primary_domain}

## Key Events (condensed)
Search queries: {search_queries}
URLs visited: {urls_visited}
Commands run: {commands_run}
Files interacted: {files}

## Task
Analyze this activity and return JSON with this exact structure:
{{
  "problem_statement": "One sentence: what was the user doing/solving?",
  "approach_description": "2-4 sentences describing the sequence of actions taken",
  "methodology_tags": ["tag1", "tag2"],
  "knowledge_applied": ["prior knowledge concept 1", "concept 2"],
  "knowledge_acquired": ["new thing learned 1", "new thing 2"],
  "confidence": 0.85,
  "reusability_estimate": 0.7
}}

Methodology tags should be short phrases like: "docs-first", "search-driven", "trial-and-error",
"binary-search-debug", "copy-paste-adapt", "top-down-research", "bottom-up-build".

Be concise. Only include things directly evidenced by the activity log.
Return only valid JSON. No markdown fences."""


PASS2_CHAIN_OF_THOUGHT = """You are analyzing a domain expert's computer activity to extract their cognitive workflow
so it can be replicated by an AI for similar future problems.

## Session Context
Duration: {duration_minutes:.1f} minutes
Primary application: {dominant_app}
Problem (pre-analyzed): {problem_statement}
Approach summary: {approach_description}

## Enriched Event Narrative
(Format: [+MM:SS] EVENT_TYPE | app | detail)
{event_narrative}

## Your Task
Extract the expert's chain of thought as a structured, replicable workflow. Focus on:
1. WHAT they did first when they didn't know the answer
2. HOW they refined their approach when the first attempt failed
3. WHAT signals told them they were on the right track
4. WHERE they ended up and what that reveals about their mental model
5. Decision points where they could have gone multiple ways but chose one path

## Output Format (strict JSON)
{{
  "problem_type": "debugging|research|implementation|review|learning|planning",
  "trigger": "One sentence: what event/error/need started this session",
  "steps": [
    {{
      "sequence": 1,
      "action": "searched|read|copied|executed|pivoted|compared|annotated|wrote",
      "target": "specific thing acted on (URL, query text, command, filename)",
      "reasoning": "WHY they did this — infer from context. Say 'unknown' if not determinable.",
      "duration_estimate_s": 45
    }}
  ],
  "decision_points": [
    {{
      "at_step": 3,
      "options_considered": ["option inferred from tabs opened then closed", "option 2"],
      "chosen": "what they actually stayed with",
      "signals": ["what evidence drove the decision"]
    }}
  ],
  "outcome": "How the session resolved — success, abandoned, partial",
  "methodology_pattern": "short label: e.g. 'symptom-search → docs → SO → verify'",
  "reusability_score": 0.85,
  "knowledge_gaps_revealed": ["things they had to look up that an expert would know cold"]
}}

IMPORTANT: Be specific. Use concrete details from the log.
If you cannot infer reasoning, write "unknown" — never fabricate.
Only include decision_points where there is clear evidence of alternatives considered.
Return only valid JSON. No markdown fences."""


PASS4_TROUBLESHOOTING_PLAYBOOK = """You are extracting a REPLAYABLE troubleshooting playbook from a debugging session.
Future-them (or an AI assistant) needs to be able to follow this playbook step-by-step
on a similar problem WITHOUT having to redo all the searching and pivoting.

## Session Context
Problem: {problem_statement}
Approach: {approach_description}
Methodology pattern: {methodology_pattern}
Knowledge gaps revealed: {knowledge_gaps}
Outcome: {outcome}

## Full event narrative
{event_narrative}

## Your Task
Extract the troubleshooting playbook. Be CONCRETE — every step should be runnable
or checkable, not vague advice.

## Output Format (strict JSON)
{{
  "title":            "Short descriptive title (e.g. 'L3VPN traffic interruption diagnosis on Huawei NCE')",
  "domain":           "single word domain — networking|kubernetes|react|database|os|security|...",
  "symptoms":         ["concrete observable symptom 1", "error message text", "behavior pattern"],
  "context_signals":  ["this playbook applies when X is true", "user is on platform Y", "stack involves Z"],
  "diagnostic_steps": [
    {{
      "sequence":         1,
      "action":           "Specific check to run (command, log to read, page to load)",
      "rationale":        "Why this check — what hypothesis it confirms or rules out",
      "expected_signal":  "What result tells you to keep going down this path",
      "tool":             "command or tool used (e.g. 'kubectl logs', 'tcpdump', 'browser DevTools')"
    }}
  ],
  "root_cause":   "One-sentence diagnosis of the actual underlying issue",
  "fix":          ["Concrete action step 1", "step 2", ...],
  "verification": ["How to verify the fix worked — command/check/observation"],
  "references":   ["URL of doc that helped", "https://..."],
  "reusability_score": 0.85
}}

CRITICAL RULES:
- Every diagnostic step must be RUNNABLE (a command, a check, a URL to load) — not abstract advice.
- Symptoms must be RECOGNIZABLE so future-you knows when to apply this playbook.
- If the user pivoted (tried something that didn't work, then changed approach), include
  the failed path as a diagnostic step with expected_signal = "if you see X, this is NOT the issue, move on".
- root_cause must explain the actual underlying mechanism, not just restate the symptom.
- If the session was NOT a debugging/troubleshooting session, return {{"skip": true}}.

Return only valid JSON. No markdown fences."""


PASS3_TRIPLE_EXTRACT = """Extract knowledge graph triples from this session analysis.

## Session Analysis
Problem: {problem_statement}
Approach: {approach_description}
Tags: {methodology_tags}
Knowledge acquired: {knowledge_acquired}
Chain of thought methodology: {methodology_pattern}

## Task
Extract factual (subject, predicate, object) triples representing knowledge from this session.
Focus on:
- Technical facts (A causes B, A requires B, A is part of B)
- Tool/technology relationships
- Problem-solution pairs
- Methodology patterns (pattern X is useful for problem type Y)

## Output Format (strict JSON array)
[
  {{
    "subject": "React hydration error",
    "predicate": "is_caused_by",
    "object": "server/client HTML mismatch",
    "confidence": 0.9
  }},
  {{
    "subject": "suppressHydrationWarning",
    "predicate": "resolves",
    "object": "React hydration error",
    "confidence": 0.95
  }}
]

Valid predicates: is_caused_by, resolves, requires, enables, part_of, related_to,
                 contradicts, occurs_in, mitigated_by, indicates, precedes, follows

Limit to 15 triples maximum. Only include triples with confidence >= 0.7.
Return only valid JSON array. No markdown fences."""


def build_event_narrative(session) -> str:
    """
    Condense session events into a readable narrative for the LLM.
    Merges consecutive keystrokes, deduplicates rapid URL switches,
    truncates long shell output.
    """
    from capman.events import EventType

    lines = []
    start_ts = session.started_at
    prev_url = ""
    url_first_seen: float = 0.0

    for event in session.events:
        offset_s = event.ts - start_ts
        mm = int(offset_s // 60)
        ss = int(offset_s % 60)
        prefix = f"[+{mm:02d}:{ss:02d}]"

        etype = event.type
        p = event.payload
        app = event.app or "?"

        if etype == EventType.SEARCH_QUERY:
            engine = p.get("engine", "search")
            query = p.get("query", "")
            lines.append(f"{prefix} SEARCH  | {app:<12} | query: \"{query}\" on {engine}")

        elif etype == EventType.URL_VISIT:
            url = p.get("url", "")
            title = p.get("title", "")
            duration = p.get("visit_duration_s", 0)
            # Deduplicate rapid back-and-forth on same URL
            if url == prev_url and (event.ts - url_first_seen) < 10:
                continue
            prev_url = url
            url_first_seen = event.ts
            dur_str = f" ({int(duration)}s)" if duration > 5 else ""
            lines.append(f"{prefix} URL     | {app:<12} | {url[:80]}{dur_str}")
            if title:
                lines.append(f"          {'':12}   title: {title[:60]}")

        elif etype == EventType.SEARCH_QUERY:
            pass  # Already handled above

        elif etype == EventType.CLIPBOARD_COPY:
            content = p.get("content", "")[:80]
            lines.append(f"{prefix} COPY    | {app:<12} | \"{content}\"")

        elif etype == EventType.CLIPBOARD_PASTE:
            content = p.get("content", "")[:60]
            lines.append(f"{prefix} PASTE   | {app:<12} | \"{content}\"")

        elif etype == EventType.SHELL_COMMAND:
            cmd = p.get("command", "")
            lines.append(f"{prefix} CMD     | {app:<12} | {cmd}")

        elif etype == EventType.SHELL_OUTPUT:
            stdout = p.get("stdout", "")
            exit_code = p.get("exit_code", "?")
            truncated = stdout[:200] + ("..." if len(stdout) > 200 else "")
            lines.append(f"{prefix} OUTPUT  | {app:<12} | exit {exit_code}  {truncated}")

        elif etype == EventType.FILE_OPEN:
            path = p.get("path", "")
            lines.append(f"{prefix} OPEN    | {app:<12} | {path}")

        elif etype == EventType.FILE_SAVE:
            path = p.get("path", "")
            lines.append(f"{prefix} SAVE    | {app:<12} | {path}")

        elif etype == EventType.FILE_DELETE:
            path = p.get("path", "")
            lines.append(f"{prefix} DELETE  | {app:<12} | {path}")

        elif etype == EventType.FILE_RENAME:
            src = p.get("src_path", "")
            dest = p.get("dest_path", "")
            lines.append(f"{prefix} RENAME  | {app:<12} | {src} → {dest}")

        elif etype == EventType.CODE_DIFF:
            path = p.get("path", "")
            added = p.get("lines_added", 0)
            removed = p.get("lines_removed", 0)
            repo = p.get("repo", "")
            repo_str = f" [{repo}]" if repo else ""
            lines.append(f"{prefix} DIFF    | {app:<12} | {path}{repo_str}  (+{added}/-{removed})")
            diff_text = (p.get("diff", "") or "").strip()
            if diff_text:
                excerpt = diff_text[:600]
                for dl in excerpt.splitlines()[:14]:
                    lines.append(f"          {'':12}   {dl[:100]}")

        elif etype == EventType.KEYSTROKE:
            text = p.get("text", "").strip()
            if len(text) > 20:
                lines.append(f"{prefix} TYPE    | {app:<12} | \"{text[:80]}\"")

        elif etype == EventType.SCREENSHOT:
            ocr = p.get("ocr_text", "")
            if ocr and len(ocr) > 20:
                lines.append(f"{prefix} SCREEN  | {app:<12} | (OCR: {ocr[:100]})")

        elif etype == EventType.DOC_OPEN:
            doc_name = p.get("doc_name", "")
            doc_type = p.get("doc_type", "")
            lines.append(f"{prefix} DOC     | {app:<12} | opened {doc_type}: \"{doc_name}\"")

        elif etype == EventType.DOC_SLIDE_CHANGE:
            curr = p.get("current_slide", "?")
            total = p.get("total_slides", "?")
            title = p.get("slide_title", "")
            direction = p.get("nav_direction", "")
            dwell = p.get("dwell_s", 0)
            prev = p.get("prev_slide", 0)
            title_str = f" \"{title}\"" if title else ""
            prev_str = f" (was slide {prev}, dwell {dwell:.0f}s)" if prev else ""
            lines.append(
                f"{prefix} SLIDE   | {app:<12} | slide {curr}/{total}{title_str}"
                f" [{direction}]{prev_str}"
            )

        elif etype == EventType.DOC_PAGE_CHANGE:
            curr = p.get("current_page", "?")
            total = p.get("total_pages", "?")
            heading = p.get("section_heading", "")
            direction = p.get("nav_direction", "")
            dwell = p.get("dwell_s", 0)
            heading_str = f" \"{heading}\"" if heading else ""
            lines.append(
                f"{prefix} PAGE    | {app:<12} | page {curr}/{total}{heading_str}"
                f" [{direction}, dwell {dwell:.0f}s]"
            )

        elif etype == EventType.DOC_SHEET_CHANGE:
            sheet = p.get("sheet_name", "")
            prev_sheet = p.get("prev_sheet", "")
            doc_name = p.get("doc_name", "")
            prev_str = f" (from \"{prev_sheet}\")" if prev_sheet else ""
            lines.append(f"{prefix} SHEET   | {app:<12} | \"{sheet}\"{prev_str} in {doc_name}")

        elif etype == EventType.DOC_NOTE_OPEN:
            note = p.get("note_title", "")
            notebook = p.get("notebook", "")
            nb_str = f" [{notebook}]" if notebook else ""
            lines.append(f"{prefix} NOTE    | {app:<12} | opened \"{note}\"{nb_str}")

        elif etype == EventType.MOUSE_CLICK:
            element = p.get("element") or {}
            label = (element.get("label") or "").strip()
            # Only render clicks where we know what was clicked. Raw (x,y)
            # adds no useful signal for the LLM.
            if label:
                role = element.get("role") or ""
                role_str = f"{role} " if role else ""
                lines.append(f"{prefix} CLICK   | {app:<12} | {role_str}\"{label[:80]}\"")

        elif etype == EventType.MOUSE_SCROLL:
            ticks = int(p.get("ticks", 0) or 0)
            dur = float(p.get("duration_s", 0) or 0)
            # Only render meaningful scrolls — tiny taps add noise.
            if ticks > 20 or dur > 5:
                direction = p.get("direction", "")
                delta = int(p.get("delta_total", 0) or 0)
                lines.append(
                    f"{prefix} SCROLL  | {app:<12} | {direction} burst "
                    f"({dur:.1f}s, {ticks} ticks, Δ{delta}px)"
                )

        elif etype == EventType.IDLE_START:
            lines.append(f"{prefix} AFK     | {'—':<12} | user went idle")

        elif etype == EventType.IDLE_END:
            dur = float(p.get("idle_duration_s", 0) or 0)
            lines.append(f"{prefix} AFK     | {'—':<12} | user returned (away {dur:.0f}s)")

        elif etype == EventType.DOC_CONTENT:
            doc_name = p.get("doc_name", "") or p.get("doc_path", "") or "?"
            kind = p.get("item_kind", "unit")
            idx = p.get("item_index", 0)
            label = p.get("item_label", "")
            label_str = f' "{label}"' if label else ""
            src = p.get("source", "")
            dwell = p.get("dwell_s", 0)
            full_chars = p.get("full_chars_indexed", p.get("text_chars", 0))
            text = (p.get("text", "") or "").strip()
            excerpt = text[:300].replace("\n", " ")
            lines.append(
                f"{prefix} READ    | {app:<12} | {doc_name} {kind} {idx}{label_str}"
                f" (dwell {dwell:.0f}s, {full_chars} chars via {src}): {excerpt}"
            )

        elif etype == EventType.USER_CLICK:
            el = p.get("element", {}) or {}
            text = el.get("text", "")[:50] or el.get("aria", "")[:50]
            tag = el.get("tag", "")
            href = el.get("href", "")
            target = f' "{text}"' if text else ""
            extra = f" → {href[:60]}" if href else ""
            lines.append(f"{prefix} CLICK   | {app:<12} | {tag}{target}{extra}")

        elif etype == EventType.FORM_INPUT:
            el = p.get("element", {}) or {}
            name = el.get("name", "") or el.get("id", "") or el.get("aria", "")
            val = p.get("value", "")[:60]
            lines.append(f"{prefix} INPUT   | {app:<12} | {name}={val!r}")

        elif etype == EventType.FORM_SUBMIT:
            action = (p.get("action", "") or "")[:60]
            fields = p.get("fields", []) or []
            field_summary = ", ".join(
                f"{f.get('name','?')}={'[REDACTED]' if f.get('value') == '[REDACTED]' else (str(f.get('value',''))[:30])}"
                for f in fields[:5]
            )
            lines.append(f"{prefix} SUBMIT  | {app:<12} | → {action}  [{field_summary}]")

        elif etype == EventType.DOM_MUTATION:
            kind = p.get("kind", "")
            if kind == "iframe_appeared":
                host = p.get("host", "")
                lines.append(f"{prefix} IFRAME  | {app:<12} | {host} loaded (likely 3rd-party widget)")
            elif kind == "modal_opened":
                text = (p.get("text", "") or "")[:60]
                lines.append(f"{prefix} MODAL   | {app:<12} | {text}")

    return "\n".join(lines) if lines else "(no significant events recorded)"
