"""
Daily LLM recategorization of brain knowledge domains.

Reads recent session analyses, asks the LLM to derive the best names and
keywords for each of the 7 fixed brain regions, and stores the result in DB.
Falls back gracefully if LLM is unavailable.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1/chat/completions"

# Fixed layout metadata — hotspot/label_anchor never change (anatomical positions)
REGION_LAYOUT = {
    "research":      {"hotspot": [335, 188], "label_anchor": [4, 72]},
    "debugging":     {"hotspot": [442, 208], "label_anchor": [740, 148]},
    "apis":          {"hotspot": [525, 118], "label_anchor": [474, 4]},
    "ml_data":       {"hotspot": [662, 195], "label_anchor": [740, 268]},
    "devops":        {"hotspot": [660, 415], "label_anchor": [740, 374]},
    "trading":       {"hotspot": [382, 330], "label_anchor": [4, 355]},
    "communication": {"hotspot": [322, 294], "label_anchor": [4, 250]},
}

RECATEGORIZE_PROMPT = """\
You are updating a cognitive knowledge map for a developer/analyst.
Based on their recent work sessions, update the 7 brain knowledge domains so
the names and keywords better reflect what this person actually works on.

RECENT SESSIONS (up to 40, newest first):
{sessions_block}

CURRENT DOMAIN DEFINITIONS (7 fixed brain regions — keep same IDs):
{current_domains}

Return a JSON array of exactly 7 objects. Each object must have:
  "id"       - same as the input (do NOT change)
  "name"     - short human-readable label (2-4 words, title case)
  "color"    - same hex as input (do NOT change)
  "glow"     - same hex as input (do NOT change)
  "keywords" - 12-18 lowercase strings that best match this user's actual work

Rules:
- Keep all 7 domain IDs unchanged.
- If a domain has little/no activity, keep its generic name and keywords.
- Keywords should be drawn from actual URLs, tool names, technologies, and
  problem types seen in the sessions above — not generic placeholders.
- Return ONLY the JSON array, no commentary, no markdown fences.
"""


async def recategorize(db, config: dict) -> bool:
    """
    Run LLM recategorization and store results in DB.
    Returns True on success, False if skipped/failed.
    """
    from capman.api.routes.brain import DOMAINS  # import hardcoded defaults

    # Pull last 40 analyzed sessions
    try:
        async with db._db.execute(
            """SELECT sa.problem_statement, sa.methodology_tags, sa.knowledge_acquired,
                      s.started_at
               FROM session_analyses sa JOIN sessions s ON s.id = sa.session_id
               WHERE sa.problem_statement != ''
                 AND sa.problem_statement NOT LIKE '%LLM not configured%'
               ORDER BY s.started_at DESC LIMIT 40"""
        ) as cur:
            rows = await cur.fetchall()
    except Exception as e:
        logger.error("Brain recategorize: DB read failed: %s", e)
        return False

    if not rows:
        logger.info("Brain recategorize: no analyzed sessions yet, skipping")
        return False

    # Build sessions block for prompt
    lines = []
    for r in rows:
        tags = json.loads(r["methodology_tags"] or "[]")
        acquired = json.loads(r["knowledge_acquired"] or "[]")
        problem = (r["problem_statement"] or "").replace("\n", " ")[:120]
        tag_str = ", ".join(tags[:6])
        acq_str = "; ".join(a[:40] for a in acquired[:3])
        lines.append(f"- [{problem}] tags=[{tag_str}] learned=[{acq_str}]")
    sessions_block = "\n".join(lines)

    # Build current domain definitions (omit layout data)
    current = [
        {
            "id": did,
            "name": d["name"],
            "color": d["color"],
            "glow": d["glow"],
            "keywords": d["keywords"],
        }
        for did, d in DOMAINS.items()
    ]
    # Overlay with any existing DB definitions
    stored = await db.load_brain_domains()
    if stored:
        stored_map = {d["id"]: d for d in stored}
        for item in current:
            if item["id"] in stored_map:
                item["name"] = stored_map[item["id"]]["name"]
                item["keywords"] = stored_map[item["id"]]["keywords"]

    prompt = RECATEGORIZE_PROMPT.format(
        sessions_block=sessions_block,
        current_domains=json.dumps(current, indent=2),
    )

    result = await _call_llm(prompt, config)
    if not result:
        return False

    # Merge LLM output with fixed layout
    computed_at = time.time()
    try:
        domains_to_save = []
        for item in result:
            did = item.get("id", "")
            if did not in REGION_LAYOUT:
                continue
            layout = REGION_LAYOUT[did]
            base = DOMAINS.get(did, {})
            domains_to_save.append({
                "id": did,
                "computed_at": computed_at,
                "name": item.get("name", base.get("name", did)),
                "color": item.get("color", base.get("color", "#c084fc")),
                "glow": item.get("glow", base.get("glow", "#7c3aed")),
                "keywords": item.get("keywords", base.get("keywords", [])),
                **layout,
            })
        await db.save_brain_domains(domains_to_save, computed_at)
        logger.info("Brain recategorized: %d domains updated", len(domains_to_save))
        return True
    except Exception as e:
        logger.error("Brain recategorize: save failed: %s", e)
        return False


async def _call_llm(prompt: str, config: dict) -> list | None:
    """Call LLM and return parsed JSON list, or None on failure."""
    cfg = config.get("pipeline", {}).get("analysis", {})
    model = cfg.get("pass1_model", "claude-haiku-4-5-20251001")
    timeout = float(cfg.get("http_timeout_s", 60.0))

    if os.environ.get("OPENROUTER_API_KEY"):
        return await asyncio.get_event_loop().run_in_executor(
            None, _openrouter_call, prompt, model, timeout
        )
    elif os.environ.get("ANTHROPIC_API_KEY"):
        return await asyncio.get_event_loop().run_in_executor(
            None, _anthropic_call, prompt, model
        )
    else:
        logger.debug("Brain recategorize: no LLM backend configured")
        return None


def _openrouter_call(prompt: str, model: str, timeout: float) -> list | None:
    import httpx
    _OR_MAP = {
        "claude-haiku-4-5-20251001": "anthropic/claude-haiku-4-5",
        "claude-haiku-4-5": "anthropic/claude-haiku-4-5",
        "claude-sonnet-4-6": "anthropic/claude-sonnet-4-6",
    }
    try:
        resp = httpx.post(
            OPENROUTER_BASE,
            headers={
                "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/capman2",
                "X-Title": "capman2",
            },
            json={
                "model": _OR_MAP.get(model, f"anthropic/{model}"),
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(raw)
    except Exception as e:
        logger.error("Brain recategorize LLM call failed: %s", e)
        return None


def _anthropic_call(prompt: str, model: str) -> list | None:
    try:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(raw)
    except Exception as e:
        logger.error("Brain recategorize LLM call failed: %s", e)
        return None
