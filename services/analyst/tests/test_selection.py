"""Selection budget-policy tests."""
from __future__ import annotations

from selection import CLUSTER_MIN, FRICTION_DAILY_CAP, NOVEL_DAILY_CAP, select_episodes


def ep(i: int, sig: str, coarse: str, flags: list[str] | None = None) -> dict:
    return {"id": f"ep-{i}", "signature": sig, "coarse_signature": coarse,
            "friction_flags": flags or []}


class TestSelectEpisodes:
    def test_novel_signature_selected_once_per_batch(self):
        episodes = [ep(0, "sig-a", "c-a"), ep(1, "sig-a", "c-a"), ep(2, "sig-a", "c-a")]
        selected, stats = select_episodes(episodes, known_signatures=set(),
                                          clusters_analyzed_this_week=set())
        assert len(selected) == 1
        assert stats["by_reason"] == {"novel": 1}

    def test_cluster_representative_when_novel_exhausted_or_known(self):
        episodes = [ep(i, f"sig-{i}", "c-shared") for i in range(CLUSTER_MIN)]
        known = {f"sig-{i}" for i in range(CLUSTER_MIN)}
        selected, stats = select_episodes(episodes, known, set())
        assert len(selected) == 1
        assert stats["by_reason"] == {"cluster_representative": 1}

    def test_small_known_cluster_skipped(self):
        episodes = [ep(0, "sig-a", "c-a"), ep(1, "sig-b", "c-a")]
        selected, stats = select_episodes(episodes, {"sig-a", "sig-b"}, set())
        assert selected == []
        assert stats["skipped"] == 2

    def test_cluster_analyzed_this_week_skipped(self):
        episodes = [ep(i, f"sig-{i}", "c-shared") for i in range(CLUSTER_MIN)]
        known = {f"sig-{i}" for i in range(CLUSTER_MIN)}
        selected, _ = select_episodes(episodes, known, {"c-shared"})
        assert selected == []

    def test_friction_always_selected(self):
        episodes = [ep(0, "sig-a", "c-a", ["rage_click"])]
        selected, stats = select_episodes(episodes, {"sig-a"}, {"c-a"})
        assert len(selected) == 1
        assert stats["by_reason"] == {"friction": 1}

    def test_friction_cap(self):
        episodes = [ep(i, f"sig-{i}", f"c-{i}", ["retry"]) for i in range(FRICTION_DAILY_CAP + 5)]
        selected, stats = select_episodes(episodes, set(), set())
        assert stats["by_reason"]["friction"] == FRICTION_DAILY_CAP

    def test_novel_cap(self):
        episodes = [ep(i, f"sig-{i}", f"c-{i}") for i in range(NOVEL_DAILY_CAP + 5)]
        selected, stats = select_episodes(episodes, set(), set())
        assert stats["by_reason"]["novel"] == NOVEL_DAILY_CAP
        assert len(selected) == NOVEL_DAILY_CAP

    def test_friction_episode_does_not_double_select_cluster(self):
        episodes = [
            ep(0, "sig-a", "c-shared", ["retry"]),
            ep(1, "sig-b", "c-shared"),
            ep(2, "sig-c", "c-shared"),
        ]
        selected, stats = select_episodes(episodes, {"sig-a", "sig-b", "sig-c"}, set())
        assert len(selected) == 1
        assert stats["by_reason"] == {"friction": 1}

    def test_stats_shape(self):
        episodes = [ep(0, "sig-a", "c-a")]
        _, stats = select_episodes(episodes, set(), set())
        assert stats["total_episodes"] == 1
        assert stats["selected"] == 1
        assert stats["skipped"] == 0
