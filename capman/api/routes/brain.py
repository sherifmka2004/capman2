"""GET /brain — real-time categorised knowledge map derived from all captured activity."""
from __future__ import annotations

import json
import time
from collections import defaultdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/brain", tags=["brain"])

# Coordinates are for the 900×480 SVG viewBox used by the brain UI.
# hotspot: [x, y] centre of the brain region
# label_anchor: [x, y] top-left of the margin label card
# These NEVER change — they are anatomical positions on the SVG.
DOMAINS: dict[str, dict] = {
    "research": {
        "name": "Research & Discovery",
        "region": "frontal",
        "color": "#c084fc",
        "glow": "#7c3aed",
        "hotspot": [335, 188],
        "label_anchor": [4, 72],
        "keywords": [
            "research", "docs", "documentation", "exploration", "github", "readme",
            "wiki", "search-driven", "docs-first", "top-down-research", "top-down",
            "repository", "platform-exploration", "reading", "manual",
        ],
    },
    "debugging": {
        "name": "Problem Solving",
        "region": "central",
        "color": "#f472b6",
        "glow": "#be185d",
        "hotspot": [442, 208],
        "label_anchor": [740, 148],
        "keywords": [
            "debug", "error", "fix", "troubleshoot", "issue", "problem",
            "oauth", "pkce", "login", "auth", "trial-and-error",
            "interactive-debugging", "manual-troubleshooting", "system-interaction",
        ],
    },
    "apis": {
        "name": "APIs & Integration",
        "region": "parietal",
        "color": "#34d399",
        "glow": "#059669",
        "hotspot": [525, 118],
        "label_anchor": [474, 4],
        "keywords": [
            "api", "apify", "cryptopanic", "scraper", "endpoint", "authentication",
            "token", "webhook", "rest", "integration", "actor", "http",
            "request", "curl", "openapi", "api-management",
        ],
    },
    "ml_data": {
        "name": "ML & Data Science",
        "region": "occipital",
        "color": "#818cf8",
        "glow": "#4338ca",
        "hotspot": [662, 195],
        "label_anchor": [740, 268],
        "keywords": [
            "machine learning", "lightgbm", "model", "training", "prediction",
            "data science", "pandas", "sklearn", "neural", "dataset", "ml",
            "algorithm", "feature", "accuracy", "classification",
        ],
    },
    "devops": {
        "name": "DevOps & Infra",
        "region": "cerebellum",
        "color": "#fb923c",
        "glow": "#c2410c",
        "hotspot": [660, 415],
        "label_anchor": [740, 374],
        "keywords": [
            "railway", "docker", "deploy", "server", "config", "environment",
            "devops", "infrastructure", "ci", "cloud", "deployment",
            "service", "container", "heroku", "vercel", "web-ui-config",
        ],
    },
    "trading": {
        "name": "Trading & Finance",
        "region": "temporal",
        "color": "#22d3ee",
        "glow": "#0e7490",
        "hotspot": [382, 330],
        "label_anchor": [4, 355],
        "keywords": [
            "trading", "binance", "crypto", "bitcoin", "finance", "stock",
            "market", "tradingview", "trading101", "bloomberg", "coin",
            "price", "candlestick", "portfolio", "cryptopanic",
        ],
    },
    "communication": {
        "name": "Communication",
        "region": "temporal_lower",
        "color": "#86efac",
        "glow": "#16a34a",
        "hotspot": [322, 294],
        "label_anchor": [4, 250],
        "keywords": [
            "whatsapp", "message", "chat", "communicate", "social",
            "messaging", "telegram", "discord", "slack", "email",
        ],
    },
}


def _score(text: str, keywords: list[str]) -> float:
    t = text.lower()
    return sum(1.0 for kw in keywords if kw in t)


def _classify(problem: str, tags: list[str], acquired: list[str],
               domains: dict) -> dict[str, float]:
    combined = " ".join([problem] + tags + acquired).lower()
    return {did: s for did, d in domains.items() if (s := _score(combined, d["keywords"])) > 0}


def _build_topic_label(tag: str) -> str:
    """Convert a methodology tag like 'top-down-research' → 'Top Down Research'."""
    return tag.replace("-", " ").replace("_", " ").title()


async def _resolve_domains(db) -> tuple[dict, float | None]:
    """Return effective domain dict (DB overrides hardcoded) + last computed_at."""
    if db is None:
        return DOMAINS, None
    stored = await db.load_brain_domains()
    if not stored:
        return DOMAINS, None

    stored_map = {d["id"]: d for d in stored}
    computed_at = max(d.get("computed_at", 0) for d in stored)
    merged: dict[str, dict] = {}
    for did, base in DOMAINS.items():
        if did in stored_map:
            s = stored_map[did]
            merged[did] = {**base, "name": s["name"], "keywords": s["keywords"],
                           "color": s.get("color", base["color"]),
                           "glow": s.get("glow", base["glow"])}
        else:
            merged[did] = base
    return merged, computed_at


@router.get("")
async def get_brain_data(request: Request):
    db = request.app.state.db
    if db is None:
        return _empty()

    now = time.time()
    domains, last_categorized = await _resolve_domains(db)

    try:
        async with db._db.execute(
            """SELECT sa.problem_statement, sa.methodology_tags, sa.knowledge_acquired,
                      s.started_at
               FROM session_analyses sa JOIN sessions s ON s.id = sa.session_id
               WHERE sa.problem_statement != ''
                 AND sa.problem_statement NOT LIKE '%LLM not configured%'
                 AND sa.problem_statement NOT LIKE '%Unable to determine%'
               ORDER BY s.started_at DESC LIMIT 120"""
        ) as cur:
            sessions = await cur.fetchall()
    except Exception:
        sessions = []

    try:
        async with db._db.execute(
            "SELECT subject, predicate, object, confidence FROM knowledge_triples ORDER BY confidence DESC LIMIT 200"
        ) as cur:
            triples = await cur.fetchall()
    except Exception:
        triples = []

    try:
        async with db._db.execute(
            "SELECT concept, domain, lookup_count FROM knowledge_gaps ORDER BY lookup_count DESC LIMIT 50"
        ) as cur:
            gaps = await cur.fetchall()
    except Exception:
        gaps = []

    scores: dict[str, float] = defaultdict(float)
    n_sessions: dict[str, int] = defaultdict(int)
    topics: dict[str, list[str]] = defaultdict(list)
    recency: dict[str, float] = defaultdict(float)

    for row in sessions:
        try:
            tags = json.loads(row["methodology_tags"] or "[]")
            acquired = json.loads(row["knowledge_acquired"] or "[]")
            problem = row["problem_statement"] or ""
            domain_scores = _classify(problem, tags, acquired, domains)
            age_s = now - (row["started_at"] or now)
            w = 3.0 if age_s < 86400 else (2.0 if age_s < 7 * 86400 else 1.0)
            for did, s in domain_scores.items():
                scores[did] += s * w
                n_sessions[did] += 1
                recency[did] = max(recency[did], row["started_at"] or 0)
                # Sub-topics: methodology tags first (concise), then knowledge acquired
                for tag in tags:
                    label = _build_topic_label(tag)
                    if label and label not in topics[did] and len(topics[did]) < 6:
                        topics[did].append(label)
                for item in acquired:
                    item = item.strip()
                    if item and item not in topics[did] and len(topics[did]) < 6:
                        topics[did].append(item[:40])
        except Exception:
            continue

    for row in triples:
        text = f"{row['subject']} {row['object']}"
        for did, d in domains.items():
            s = _score(text, d["keywords"])
            if s > 0:
                scores[did] += s * 0.4
                triple_text = f"{row['subject']} → {row['object']}"
                if len(topics[did]) < 6 and triple_text not in topics[did]:
                    topics[did].append(triple_text[:48])

    for row in gaps:
        text = f"{row['concept']} {row['domain'] or ''}"
        for did, d in domains.items():
            s = _score(text, d["keywords"])
            if s > 0:
                scores[did] += s * row["lookup_count"] * 0.25
                concept = row["concept"].strip()
                if concept and len(topics[did]) < 6 and concept not in topics[did]:
                    topics[did].append(concept[:40])

    max_score = max(scores.values(), default=1.0) or 1.0
    total_score = sum(scores.values()) or 1.0

    categories = []
    for did, d in domains.items():
        raw = scores.get(did, 0.0)
        weight = min(1.0, raw / max_score)       # for visual sizing (0–1)
        pct = round(raw / total_score * 100, 1)   # proportional share (sums to 100)
        ts = recency.get(did, 0)
        age_h = round((now - ts) / 3600, 1) if ts else None
        categories.append({
            "id": did,
            "name": d["name"],
            "color": d["color"],
            "glow": d["glow"],
            "hotspot": d["hotspot"],
            "label_anchor": d["label_anchor"],
            "weight": round(weight, 3),
            "pct": pct,
            "session_count": n_sessions.get(did, 0),
            "topics": topics.get(did, [])[:5],
            "last_active_h": age_h,
        })

    # Co-occurrence connections
    connections = []
    domain_ids = list(domains.keys())
    for i, d1 in enumerate(domain_ids):
        for d2 in domain_ids[i + 1:]:
            shared = 0
            for row in sessions:
                try:
                    tags = json.loads(row["methodology_tags"] or "[]")
                    acquired = json.loads(row["knowledge_acquired"] or "[]")
                    sc = _classify(row["problem_statement"] or "", tags, acquired, domains)
                    if d1 in sc and d2 in sc:
                        shared += 1
                except Exception:
                    pass
            if shared > 0:
                denom = max(n_sessions.get(d1, 1), n_sessions.get(d2, 1))
                connections.append({
                    "from": d1, "to": d2,
                    "strength": round(min(1.0, shared / denom), 3),
                    "shared": shared,
                })

    connections.sort(key=lambda c: c["strength"], reverse=True)

    return {
        "categories": categories,
        "connections": connections[:12],
        "total_sessions": len(sessions),
        "last_updated": now,
        "last_categorized": last_categorized,
    }


@router.post("/recategorize")
async def trigger_recategorize(request: Request):
    """Manually trigger an LLM-based domain recategorization."""
    db = request.app.state.db
    config = request.app.state.config
    if db is None:
        return JSONResponse({"ok": False, "error": "no database"}, status_code=503)

    import asyncio
    from capman.pipeline.brain_recategorizer import recategorize

    try:
        ok = await asyncio.wait_for(recategorize(db, config), timeout=90.0)
        if ok:
            return {"ok": True, "message": "Recategorization complete"}
        return JSONResponse({"ok": False, "error": "LLM returned no result"}, status_code=500)
    except asyncio.TimeoutError:
        return JSONResponse({"ok": False, "error": "LLM call timed out"}, status_code=504)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


def _empty():
    return {
        "categories": [], "connections": [], "total_sessions": 0,
        "last_updated": time.time(), "last_categorized": None,
    }
