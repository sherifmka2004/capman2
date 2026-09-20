"""Verification verdict + worker dry-run tests."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from capman.web.manifest import load_manifest
from verify import MIN_VOLUME, classify


class TestClassify:
    def test_insufficient_volume(self):
        assert classify(0.5, 0.1, MIN_VOLUME - 1, 100) == "insufficient_volume"
        assert classify(0.5, 0.1, 100, MIN_VOLUME - 1) == "insufficient_volume"

    def test_improved(self):
        assert classify(0.40, 0.20, 100, 100) == "improved"
        assert classify(0.40, 0.2799, 100, 100) == "improved"  # just under 0.7x

    def test_unchanged(self):
        assert classify(0.40, 0.35, 100, 100) == "unchanged"
        assert classify(0.40, 0.28, 100, 100) == "unchanged"  # 0.4*0.7 rounds below 0.28
        assert classify(0.40, 0.28, 100, 100) == "unchanged"  # 0.4*0.7 rounds below 0.28

    def test_regressed(self):
        assert classify(0.40, 0.60, 100, 100) == "regressed"
        assert classify(0.40, 0.52, 100, 100) == "regressed"  # exactly 1.3x

    def test_zero_baseline(self):
        assert classify(0.0, 0.0, 100, 100) == "unchanged"
        assert classify(0.0, 0.1, 100, 100) == "regressed"


MANIFEST_TOML = """
[product]
key = "acme"
description = "A generic widget product."

[session]
min_events = 3

[steps]
order = ["land", "done"]

[outcomes]
success_when = ["export_result.ok == true"]

[[events]]
name = "page_view"
step = "land"

[[events]]
name = "export_result"
step = "done"
props = { ok = { type = "bool", optional = false } }
"""


class FakeAnalystDB:
    def __init__(self, rows):
        self._rows = rows
        self.persisted = None

    async def fetch_unassigned_events(self, tenant):
        return self._rows

    async def fetch_known_signatures(self, tenant):
        return set()

    async def fetch_clusters_analyzed_this_week(self, tenant, now=None):
        return set()

    async def persist_episodes(self, tenant, episodes):
        self.persisted = episodes
        return len(episodes)


@pytest.fixture()
def manifest(tmp_path: Path):
    p = tmp_path / "acme.toml"
    p.write_text(textwrap.dedent(MANIFEST_TOML))
    return load_manifest(p)


T0 = 1_700_000_000.0
SID = "aaaa1111-2222-3333-4444-555566667777"


def _rows():
    return [
        {"id": f"ev-{i}", "ts": T0 + i * 5, "name": name,
         "payload": {"sid": SID, "seq": i, "t_rel_ms": i * 5000, "props": props}}
        for i, (name, props) in enumerate([
            ("page_view", {}), ("page_view", {}), ("export_result", {"ok": True}),
        ])
    ]


class TestRunCycleDry:
    async def test_dry_run_reports_without_writing(self, manifest, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        from capman.pipeline.web_analyzer import WebEpisodeAnalyzer
        from run import run_cycle

        db = FakeAnalystDB(_rows())
        analyzer = WebEpisodeAnalyzer({}, manifest)
        stats = await run_cycle("product:acme", manifest, db, analyzer, dry_run=True)

        assert db.persisted is None
        assert stats["unassigned_events"] == 3
        assert stats["new_episodes"] == 1
        assert stats["selection"]["selected"] == 1
        assert stats["llm_calls_estimate"]["pass1_haiku"] == 1
        assert "analyzed" not in stats or stats.get("analyzed") == 0
