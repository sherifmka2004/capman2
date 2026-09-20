"""Web-behavior prompt templates — the product-analytics variant of prompts.py.

prompts.py (desktop profile) is untouched. These templates contain NO product
nouns: {product_description}, {glossary} and {steps_order} are interpolated
from the product manifest, so the same code serves any product.
"""
from __future__ import annotations

WEB_PASS1_INTENT = """You are analyzing one anonymous visitor's task attempt inside a web product.

PRODUCT CONTEXT (from the product's manifest):
{product_description}

GLOSSARY:
{glossary}

FUNNEL STEP ORDER:
{steps_order}

EPISODE FACTS:
- outcome label: {outcome}
- furthest step reached: {step_reached}
- friction flags detected: {friction_flags}
- duration: {duration_s:.0f}s, events: {n_events}

EVENT TIMELINE (relative timestamps; names and props come from a closed,
pre-declared vocabulary — no free text exists in this data):
{episode_narrative}

Infer what this visitor was trying to accomplish and how their attempt went.
Judge only from the timeline. If the data is too thin, say so with low confidence.

Return JSON:
{{
  "intent_statement":    "One sentence: what the visitor was trying to do",
  "attempt_path":        "One sentence: the path they actually took",
  "outcome_assessment":  "succeeded | abandoned | errored | bounced | unclear, plus one sentence why",
  "friction_points":     ["Each concrete point where the product stopped matching the visitor's intent"],
  "confidence":          0.0,
  "severity_estimate":   0.0
}}

severity_estimate scales 0 (no friction) to 1 (attempt destroyed).
Return only valid JSON. No markdown fences."""


WEB_PASS2_CHAIN = """You are reconstructing the decision chain behind one anonymous visitor's
task attempt in a web product.

PRODUCT CONTEXT:
{product_description}

GLOSSARY:
{glossary}

FUNNEL STEP ORDER:
{steps_order}

PASS 1 SUMMARY:
intent: {intent_statement}
attempt path: {attempt_path}
friction points: {friction_points}

EVENT TIMELINE:
{episode_narrative}

Reconstruct the visitor's chain of thought: what they decided at each point,
where their mental model of the product diverged from its actual behavior,
and which paths they abandoned.

Return JSON:
{{
  "problem_type": "task_completion | exploration | error_recovery | evaluation",
  "trigger": "What started this attempt",
  "steps": [
    {{
      "sequence": 1,
      "action": "clicked | navigated | submitted | waited | retried | abandoned",
      "target": "event name / step involved",
      "reasoning": "inferred why — 'unknown' if undeterminable",
      "duration_estimate_s": 0.0
    }}
  ],
  "decision_points": [
    {{
      "at_step": 1,
      "options_considered": ["paths the behavior suggests they weighed"],
      "chosen": "the path taken",
      "signals": ["what in the timeline shows this"]
    }}
  ],
  "mental_model_divergence": "Where the product behaved differently than the visitor expected, or '' if none",
  "dead_ends": ["paths tried and abandoned, with the reason"],
  "outcome": "one sentence",
  "methodology_pattern": "compact pattern, e.g. 'land -> probe -> retry -> abandon'",
  "reusability_score": 0.0
}}

Return only valid JSON. No markdown fences."""


WEB_PASS3_TRIPLES = """Extract knowledge-graph triples from this analyzed web-behavior episode.

PRODUCT CONTEXT:
{product_description}

INTENT: {intent_statement}
APPROACH: {attempt_path}
METHODOLOGY PATTERN: {methodology_pattern}
FRICTION POINTS: {friction_points}

EVENT TIMELINE (excerpt):
{episode_narrative}

Predicates: causes | requires | related_to | contradicts | enables | resolves | part_of | blocks.
Subjects/objects are short concept names (event names, step names, UI surfaces,
inferred goals) — never personal data.

Return a JSON array:
[
  {{
    "subject": "upload_flow",
    "predicate": "blocks",
    "object": "first_export",
    "confidence": 0.85
  }}
]

Only include triples with confidence >= 0.7.
Return only valid JSON. No markdown fences."""


WEB_PASS4_FRICTION_PLAYBOOK = """Synthesize a FRICTION PLAYBOOK from a cluster of similar anonymous task
attempts in a web product. A friction playbook tells the product team what is
broken for users, why, what to change, and how to verify the change worked.

PRODUCT CONTEXT:
{product_description}

GLOSSARY:
{glossary}

CLUSTER FACTS:
- occurrences: {frequency}
- shared signature pattern: {methodology_pattern}
- representative intent: {intent_statement}
- representative attempt path: {attempt_path}
- friction points seen across the cluster: {friction_points}
- friction flags: {friction_flags}
- outcome distribution: {outcome_distribution}

REPRESENTATIVE EVENT TIMELINE:
{episode_narrative}

Return JSON:
{{
  "title": "Short name for this friction pattern",
  "domain": "funnel area, e.g. a step name",
  "symptoms": ["Observable signals in behavior data that this friction is occurring"],
  "context_signals": ["routes / entry points / devices where it concentrates"],
  "diagnostic_steps": [
    {{
      "sequence": 1,
      "action": "The query or check that confirms this pattern",
      "rationale": "Why it confirms or rules out the cause",
      "expected_signal": "What the data looks like when this friction is real",
      "tool": "SQL / dashboard / session sample"
    }}
  ],
  "root_cause": "The inferred product-side cause",
  "fix": ["Concrete proposed change(s)"],
  "verification": ["The signature-rate metric that must move, and in which direction"],
  "reusability_score": 0.0
}}

CRITICAL RULES:
- Symptoms are OBSERVABLE BEHAVIOR (signature rates, flags, outcomes), not guesses.
- verification must name a measurable rate over the cluster signature.
- reusability_score = expected impact: frequency x severity, normalized 0..1.
- If the cluster is too thin or benign to warrant a playbook, return {{"skip": true}}.

Return only valid JSON. No markdown fences."""


def build_web_narrative(episode: dict, manifest) -> str:
    """Condense one episode into a readable timeline for the LLM.

    Mirrors build_event_narrative() (capman/pipeline/prompts.py) for web
    episodes. episode["events"] items: {name, props, t_rel_ms, seq}.
    """
    lines: list[str] = []
    base_ms = episode["events"][0]["t_rel_ms"] if episode.get("events") else 0
    for e in episode.get("events", []):
        offset_s = max(0, (e.get("t_rel_ms", base_ms) - base_ms)) / 1000.0
        mm = int(offset_s // 60)
        ss = int(offset_s % 60)
        name = e.get("name", "?")
        spec = manifest.events.get(name)
        step = spec.step if spec and spec.step else "-"
        props = e.get("props") or {}
        props_str = " ".join(f"{k}={v}" for k, v in sorted(props.items()))
        lines.append(f"[+{mm:02d}:{ss:02d}] {name:<28} | {step:<10} | {props_str[:120]}")
    return "\n".join(lines)
