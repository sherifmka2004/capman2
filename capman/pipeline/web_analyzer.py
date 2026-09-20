"""Web-episode LLM analyzer — reuses SessionAnalyzer's backend plumbing.

Pass structure (from the plan):
  Pass 1 (haiku, every selected episode)  → intent / attempt path / friction points
  Pass 2 (sonnet, severity >= 0.5 AND cluster frequency >= 3) → visitor chain of thought
  Pass 3 (haiku, after Pass 2)            → knowledge triples
  Pass 4 (sonnet, frequency x severity above threshold) → friction playbook

All LLM output strings pass through redact_derived_text() before being
returned — second line of defence behind the manifest's no-free-text boundary.
"""
from __future__ import annotations

import json
import logging

from capman.knowledge.privacy import redact_derived_text
from capman.pipeline.analyzer import SessionAnalyzer
from capman.pipeline.prompts_web import (
    WEB_PASS1_INTENT,
    WEB_PASS2_CHAIN,
    WEB_PASS3_TRIPLES,
    WEB_PASS4_FRICTION_PLAYBOOK,
    build_web_narrative,
)
from capman.web.manifest import ProductManifest

logger = logging.getLogger(__name__)


def _redact_deep(obj):
    if isinstance(obj, str):
        return redact_derived_text(obj)
    if isinstance(obj, list):
        return [_redact_deep(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _redact_deep(v) for k, v in obj.items()}
    return obj


class WebEpisodeAnalyzer(SessionAnalyzer):
    """Inherits backend detection, _call_llm, JSON parsing from SessionAnalyzer."""

    def __init__(self, config: dict, manifest: ProductManifest):
        super().__init__(config)
        self._manifest = manifest
        web = config.get("pipeline", {}).get("web_analysis", {})
        self._pass2_min_severity = float(web.get("pass2_min_severity", 0.5))
        self._pass2_min_frequency = int(web.get("pass2_min_frequency", 3))
        self._pass4_min_score = float(web.get("pass4_min_score", 1.5))

    def _product_context(self) -> dict:
        glossary = "\n".join(f"- {k}: {v}" for k, v in self._manifest.glossary.items()) or "(none)"
        return {
            "product_description": self._manifest.description or "(none)",
            "glossary": glossary,
            "steps_order": " -> ".join(self._manifest.steps) or "(none)",
        }

    async def analyze_episode(self, episode: dict, cluster_frequency: int = 1,
                              outcome_distribution: dict | None = None) -> dict:
        """Run the web passes over one episode. Returns a dict with keys:
        pass1, chain_of_thought, triples, playbook (each None when skipped)."""
        result: dict = {"pass1": None, "chain_of_thought": None,
                        "triples": None, "playbook": None}
        if self._backend == "none":
            logger.debug("Skipping web analysis — no LLM backend configured")
            return result

        narrative = build_web_narrative(episode, self._manifest)
        ctx = self._product_context()

        try:
            pass1 = await self._call_llm(self._pass1_model, WEB_PASS1_INTENT.format(
                **ctx,
                outcome=episode.get("outcome", ""),
                step_reached=episode.get("step_reached", ""),
                friction_flags=json.dumps(episode.get("friction_flags", [])),
                duration_s=(episode.get("ended_at", 0) - episode.get("started_at", 0)),
                n_events=episode.get("n_events", 0),
                episode_narrative=narrative[:8000],
            ))
            result["pass1"] = _redact_deep(pass1) if isinstance(pass1, dict) else None
        except Exception as e:
            logger.error("Web pass 1 failed for episode %s: %s", episode.get("id"), e)
            return result

        severity = float((result["pass1"] or {}).get("severity_estimate", 0.0))

        if severity >= self._pass2_min_severity and cluster_frequency >= self._pass2_min_frequency:
            try:
                p1 = result["pass1"] or {}
                cot = await self._call_llm(self._pass2_model, WEB_PASS2_CHAIN.format(
                    **ctx,
                    intent_statement=p1.get("intent_statement", ""),
                    attempt_path=p1.get("attempt_path", ""),
                    friction_points=json.dumps(p1.get("friction_points", [])),
                    episode_narrative=narrative[:8000],
                ))
                result["chain_of_thought"] = _redact_deep(cot) if isinstance(cot, dict) else None
            except Exception as e:
                logger.error("Web pass 2 failed for episode %s: %s", episode.get("id"), e)

            if result["chain_of_thought"]:
                try:
                    p1 = result["pass1"] or {}
                    cot = result["chain_of_thought"]
                    triples = await self._call_llm(self._pass3_model, WEB_PASS3_TRIPLES.format(
                        product_description=ctx["product_description"],
                        intent_statement=p1.get("intent_statement", ""),
                        attempt_path=p1.get("attempt_path", ""),
                        methodology_pattern=cot.get("methodology_pattern", ""),
                        friction_points=json.dumps(p1.get("friction_points", [])),
                        episode_narrative=narrative[:3000],
                    ))
                    if isinstance(triples, list):
                        result["triples"] = _redact_deep([
                            t for t in triples
                            if isinstance(t, dict) and t.get("confidence", 0) >= 0.7
                        ])
                except Exception as e:
                    logger.error("Web pass 3 failed for episode %s: %s", episode.get("id"), e)

        if severity * cluster_frequency >= self._pass4_min_score and result["chain_of_thought"]:
            try:
                p1 = result["pass1"] or {}
                cot = result["chain_of_thought"]
                pb = await self._call_llm(self._pass2_model, WEB_PASS4_FRICTION_PLAYBOOK.format(
                    **ctx,
                    frequency=cluster_frequency,
                    methodology_pattern=cot.get("methodology_pattern", ""),
                    intent_statement=p1.get("intent_statement", ""),
                    attempt_path=p1.get("attempt_path", ""),
                    friction_points=json.dumps(p1.get("friction_points", [])),
                    friction_flags=json.dumps(episode.get("friction_flags", [])),
                    outcome_distribution=json.dumps(outcome_distribution or {}),
                    episode_narrative=narrative[:8000],
                ))
                if isinstance(pb, dict) and not pb.get("skip"):
                    result["playbook"] = _redact_deep(pb)
            except Exception as e:
                logger.error("Web pass 4 failed for episode %s: %s", episode.get("id"), e)

        return result
