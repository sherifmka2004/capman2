"""
4-pass LLM analysis orchestrator.

Pass 1: Summarize (always, cheap)
Pass 2: Chain-of-Thought extraction (high-value sessions only)
Pass 3: Triple extraction (after Pass 2)
Pass 4: Troubleshooting playbook (debugging sessions only)

Supports two LLM backends (auto-detected from environment):
  - Anthropic SDK  → set ANTHROPIC_API_KEY
  - OpenRouter     → set OPENROUTER_API_KEY  (uses httpx, OpenAI-compatible format)
"""
from __future__ import annotations

import json
import logging
import os
import time

from capman.events import (
    Session, SessionAnalysis, ChainOfThought, CognitiveStep,
    DecisionPoint, Triple, TroubleshootingPlaybook, DiagnosticStep,
)
from capman.pipeline.prompts import (
    PASS1_SUMMARIZE, PASS2_CHAIN_OF_THOUGHT, PASS3_TRIPLE_EXTRACT,
    PASS4_TROUBLESHOOTING_PLAYBOOK, build_event_narrative,
)

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1/chat/completions"

# Map our internal model IDs to OpenRouter model IDs
_OR_MODEL_MAP = {
    "claude-haiku-4-5-20251001": "anthropic/claude-haiku-4-5",
    "claude-haiku-4-5":          "anthropic/claude-haiku-4-5",
    "claude-sonnet-4-6":         "anthropic/claude-sonnet-4-6",
    "claude-opus-4-7":           "anthropic/claude-opus-4-7",
}


class SessionAnalyzer:
    def __init__(self, config: dict):
        cfg = config.get("pipeline", {}).get("analysis", {})
        self._pass1_model = cfg.get("pass1_model", "claude-haiku-4-5-20251001")
        self._pass2_model = cfg.get("pass2_model", "claude-sonnet-4-6")
        self._pass3_model = cfg.get("pass3_model", "claude-haiku-4-5-20251001")
        self._cot_threshold = cfg.get("cot_reusability_threshold", 0.5)

        # Auto-detect backend from environment
        if os.environ.get("OPENROUTER_API_KEY"):
            self._backend = "openrouter"
            self._api_key = os.environ["OPENROUTER_API_KEY"]
            logger.info("LLM backend: OpenRouter")
        elif os.environ.get("ANTHROPIC_API_KEY"):
            self._backend = "anthropic"
            import anthropic as _anthropic
            self._client = _anthropic.Anthropic()
            logger.info("LLM backend: Anthropic")
        else:
            self._backend = "none"
            logger.warning("No LLM API key set — analysis disabled. "
                           "Set ANTHROPIC_API_KEY or OPENROUTER_API_KEY.")

    async def analyze(self, session: Session) -> SessionAnalysis:
        """Run all analysis passes and return a populated SessionAnalysis."""
        if self._backend == "none":
            logger.debug("Skipping analysis — no LLM backend configured")
            return SessionAnalysis(session_id=session.id,
                                   problem_statement="[LLM not configured]",
                                   analyzed_at=time.time())

        duration_m = ((session.ended_at or time.time()) - session.started_at) / 60.0
        analysis = SessionAnalysis(session_id=session.id)

        # --- Pass 1: Summarize ---
        try:
            pass1 = await self._pass1(session, duration_m)
            analysis.problem_statement = pass1.get("problem_statement", "")
            analysis.approach_description = pass1.get("approach_description", "")
            analysis.methodology_tags = pass1.get("methodology_tags", [])
            analysis.knowledge_applied = pass1.get("knowledge_applied", [])
            analysis.knowledge_acquired = pass1.get("knowledge_acquired", [])
            analysis.confidence = pass1.get("confidence", 0.0)
            analysis.model_used = self._pass1_model
            reusability = pass1.get("reusability_estimate", 0.0)
            logger.info("Pass 1 done: %s (reusability=%.2f)", analysis.problem_statement[:60], reusability)
        except Exception as e:
            logger.error("Pass 1 failed for session %s: %s", session.id, e)
            analysis.problem_statement = "Analysis failed"
            analysis.analyzed_at = time.time()
            return analysis

        # --- Pass 2: Chain-of-Thought (only for high-value sessions) ---
        if reusability >= self._cot_threshold:
            try:
                narrative = build_event_narrative(session)
                cot_data = await self._pass2(session, duration_m, analysis, narrative)
                analysis.chain_of_thought = self._parse_cot(session.id, cot_data, duration_m)
                logger.info("Pass 2 done: %s", analysis.chain_of_thought.methodology_pattern)
            except Exception as e:
                logger.error("Pass 2 failed for session %s: %s", session.id, e)
        else:
            logger.info("Skipping Pass 2 for session %s (reusability=%.2f < %.2f)",
                        session.id, reusability, self._cot_threshold)

        # --- Pass 3: Triple extraction (after Pass 2) ---
        if analysis.chain_of_thought:
            try:
                triples_data = await self._pass3(analysis)
                analysis.triples = self._parse_triples(session.id, triples_data)
                logger.info("Pass 3 done: %d triples extracted", len(analysis.triples))
            except Exception as e:
                logger.error("Pass 3 failed for session %s: %s", session.id, e)

        # --- Pass 4: Troubleshooting playbook (debugging sessions only) ---
        if (
            analysis.chain_of_thought
            and analysis.chain_of_thought.problem_type.lower() in {"debugging", "troubleshooting", "review"}
        ):
            try:
                narrative = build_event_narrative(session)
                pb_data = await self._pass4(analysis, narrative)
                if pb_data and not pb_data.get("skip"):
                    analysis.playbook = self._parse_playbook(session.id, pb_data)
                    logger.info("Pass 4 done: playbook '%s' (%d diagnostic steps)",
                                analysis.playbook.title[:60], len(analysis.playbook.diagnostic_steps))
            except Exception as e:
                logger.error("Pass 4 failed for session %s: %s", session.id, e)

        analysis.analyzed_at = time.time()
        return analysis

    async def _pass1(self, session: Session, duration_m: float) -> dict:
        prompt = PASS1_SUMMARIZE.format(
            duration_minutes=duration_m,
            dominant_app=session.dominant_app or "unknown",
            primary_domain=session.primary_domain or "unknown",
            search_queries=json.dumps(session.search_queries[:10]),
            urls_visited=json.dumps(session.urls_visited[:15]),
            commands_run=json.dumps(session.commands_run[:10]),
            files=json.dumps([
                e.payload.get("path", "")
                for e in session.events
                if e.type.value in ("file_open", "file_save")
            ][:10]),
        )
        return await self._call_llm(self._pass1_model, prompt)

    async def _pass2(self, session: Session, duration_m: float,
                     analysis: SessionAnalysis, narrative: str) -> dict:
        prompt = PASS2_CHAIN_OF_THOUGHT.format(
            duration_minutes=duration_m,
            dominant_app=session.dominant_app or "unknown",
            problem_statement=analysis.problem_statement,
            approach_description=analysis.approach_description,
            event_narrative=narrative[:8000],  # Token budget
        )
        return await self._call_llm(self._pass2_model, prompt)

    async def _pass3(self, analysis: SessionAnalysis) -> list:
        cot = analysis.chain_of_thought
        prompt = PASS3_TRIPLE_EXTRACT.format(
            problem_statement=analysis.problem_statement,
            approach_description=analysis.approach_description,
            methodology_tags=json.dumps(analysis.methodology_tags),
            knowledge_acquired=json.dumps(analysis.knowledge_acquired),
            methodology_pattern=cot.methodology_pattern if cot else "",
        )
        result = await self._call_llm(self._pass3_model, prompt)
        # Pass 3 returns a list directly
        if isinstance(result, list):
            return result
        return []

    async def _pass4(self, analysis: SessionAnalysis, narrative: str) -> dict:
        """Extract a structured troubleshooting playbook (debugging sessions only)."""
        cot = analysis.chain_of_thought
        prompt = PASS4_TROUBLESHOOTING_PLAYBOOK.format(
            problem_statement=analysis.problem_statement,
            approach_description=analysis.approach_description,
            methodology_pattern=cot.methodology_pattern if cot else "",
            knowledge_gaps=json.dumps(cot.knowledge_gaps_revealed if cot else []),
            outcome=cot.outcome if cot else "",
            event_narrative=narrative[:8000],
        )
        # Use the strongest model for playbook extraction
        result = await self._call_llm(self._pass2_model, prompt)
        return result if isinstance(result, dict) else {}

    def _parse_playbook(self, session_id: str, data: dict) -> TroubleshootingPlaybook:
        steps = [
            DiagnosticStep(
                sequence=s.get("sequence", i + 1),
                action=s.get("action", ""),
                rationale=s.get("rationale", ""),
                expected_signal=s.get("expected_signal", ""),
                tool=s.get("tool", ""),
            )
            for i, s in enumerate(data.get("diagnostic_steps", []))
        ]
        return TroubleshootingPlaybook(
            session_id=session_id,
            title=data.get("title", "Untitled playbook"),
            domain=data.get("domain", ""),
            symptoms=data.get("symptoms", []),
            context_signals=data.get("context_signals", []),
            diagnostic_steps=steps,
            root_cause=data.get("root_cause", ""),
            fix=data.get("fix", []),
            verification=data.get("verification", []),
            references=data.get("references", []),
            related_playbooks=data.get("related_playbooks", []),
            reusability_score=data.get("reusability_score", 0.0),
        )

    async def _call_llm(self, model: str, prompt: str) -> dict | list:
        import asyncio
        loop = asyncio.get_event_loop()

        if self._backend == "openrouter":
            return await loop.run_in_executor(None, self._call_openrouter, model, prompt)
        else:
            return await loop.run_in_executor(None, self._call_anthropic, model, prompt)

    def _call_anthropic(self, model: str, prompt: str) -> dict | list:
        resp = self._client.messages.create(
            model=model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(raw)

    def _call_openrouter(self, model: str, prompt: str) -> dict | list:
        import httpx
        # Map internal model names to OpenRouter IDs
        or_model = _OR_MODEL_MAP.get(model, f"anthropic/{model}")
        resp = httpx.post(
            OPENROUTER_BASE,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/capman2",
                "X-Title": "capman2",
            },
            json={
                "model": or_model,
                "max_tokens": 2048,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(raw)

    def _parse_cot(self, session_id: str, data: dict, duration_m: float) -> ChainOfThought:
        steps = [
            CognitiveStep(
                sequence=s.get("sequence", i + 1),
                action=s.get("action", ""),
                target=s.get("target", ""),
                reasoning=s.get("reasoning", "unknown"),
                duration_estimate_s=s.get("duration_estimate_s", 0.0),
            )
            for i, s in enumerate(data.get("steps", []))
        ]
        dps = [
            DecisionPoint(
                at_step=d.get("at_step", 0),
                options_considered=d.get("options_considered", []),
                chosen=d.get("chosen", ""),
                signals=d.get("signals", []),
            )
            for d in data.get("decision_points", [])
        ]
        return ChainOfThought(
            session_id=session_id,
            problem_type=data.get("problem_type", ""),
            trigger=data.get("trigger", ""),
            steps=steps,
            decision_points=dps,
            outcome=data.get("outcome", ""),
            methodology_pattern=data.get("methodology_pattern", ""),
            reusability_score=data.get("reusability_score", 0.0),
            knowledge_gaps_revealed=data.get("knowledge_gaps_revealed", []),
            duration_seconds=duration_m * 60,
        )

    def _parse_triples(self, session_id: str, data: list) -> list[Triple]:
        triples = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if item.get("confidence", 0) < 0.7:
                continue
            triples.append(Triple(
                subject=item.get("subject", ""),
                predicate=item.get("predicate", ""),
                object=item.get("object", ""),
                confidence=item.get("confidence", 1.0),
                source_session=session_id,
                observed_at=time.time(),
            ))
        return triples
