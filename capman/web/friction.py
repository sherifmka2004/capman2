"""Product-independent friction primitives.

Every product gets these without declaring them: the reserved event names in
FRICTION_EVENTS are accepted by every manifest, and derive_friction()
re-derives the same flags server-side from an episode's ordered events so a
malicious or buggy client cannot invent friction that did not happen (and
cannot hide friction the server can see).
"""
from __future__ import annotations

from capman.web.manifest import PropSpec

RETRY_WINDOW_MS = 10_000
RETRY_MIN_COUNT = 3
BACKTRACK_MIN_DROP = 2
SLOW_FIRST_ACTION_MS = 30_000

_SHORT = dict(type="short_string", optional=False)
_INT = dict(type="int", max=100, optional=False)

FRICTION_EVENTS: dict[str, dict[str, PropSpec]] = {
    "rage_click": {"element": PropSpec(**_SHORT), "n": PropSpec(type="int", max=100, optional=False)},
    "dead_click": {"element": PropSpec(**_SHORT)},
    "retry": {"action": PropSpec(**_SHORT), "n": PropSpec(type="int", max=100, optional=False)},
    "backtrack": {"from_step": PropSpec(**_SHORT), "to_step": PropSpec(**_SHORT)},
    "zero_results": {"surface": PropSpec(**_SHORT)},
    "error_shown": {"code_class": PropSpec(**_SHORT)},
    "field_abandon": {"field": PropSpec(**_SHORT)},
    "time_to_first_action": {"ms": PropSpec(type="int", max=3_600_000, optional=False)},
}

_CLIENT_FLAGS = frozenset({
    "rage_click", "dead_click", "zero_results", "error_shown", "field_abandon",
})


def derive_friction(
    ordered_events: list[dict],
    event_steps: dict[str, str],
    steps: list[str],
) -> list[str]:
    """Derive friction flags for one episode. Deterministic; returns sorted unique flags.

    Each event dict: {"name": str, "props": dict, "t_rel_ms": int, "seq": int}.
    event_steps maps event name -> funnel step; steps is the funnel order.
    """
    events = sorted(
        ordered_events,
        key=lambda e: (e.get("t_rel_ms", 0), e.get("seq", 0)),
    )
    if not events:
        return []

    flags: set[str] = set()

    for e in events:
        name = e.get("name", "")
        if name in _CLIENT_FLAGS:
            flags.add(name)

    if events[0].get("t_rel_ms", 0) > SLOW_FIRST_ACTION_MS:
        flags.add("slow_first_action")

    occurrences: dict[str, list[int]] = {}
    for e in events:
        name = e.get("name", "")
        if name in FRICTION_EVENTS:
            continue
        occurrences.setdefault(name, []).append(e.get("t_rel_ms", 0))
    for times in occurrences.values():
        times.sort()
        for i in range(len(times) - RETRY_MIN_COUNT + 1):
            if times[i + RETRY_MIN_COUNT - 1] - times[i] <= RETRY_WINDOW_MS:
                flags.add("retry")
                break

    step_index = {s: i for i, s in enumerate(steps)}
    max_reached = -1
    for e in events:
        step = event_steps.get(e.get("name", ""), "")
        idx = step_index.get(step)
        if idx is None:
            continue
        if max_reached >= 0 and max_reached - idx >= BACKTRACK_MIN_DROP:
            flags.add("backtrack")
        max_reached = max(max_reached, idx)

    return sorted(flags)
