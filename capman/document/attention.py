"""
Attention policy — decides whether a particular `DocumentView` was *looked at*
(worth capturing its content) versus *scrolled past* (ignore).

This is deliberately a small, swappable interface (Open/Closed): the default
`DwellAttentionPolicy` keys off dwell time + re-visits, but a smarter policy
(eye-tracking, mouse activity, zoom level, …) can drop in without touching the
tracker.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from capman.document.model import DocumentView


class AttentionPolicy(ABC):
    @property
    @abstractmethod
    def dwell_threshold_s(self) -> float:
        """How long the user must stay on a unit before we even *try* to capture it.
        The tracker uses this to defer the (relatively expensive) extraction."""

    @abstractmethod
    def worth_capturing(self, view: DocumentView, dwell_so_far_s: float) -> bool:
        """Final go/no-go once the dwell timer has fired."""


class DwellAttentionPolicy(AttentionPolicy):
    """Capture a unit's content if the user dwelled on it long enough, or kept
    coming back to it. Quick scroll-throughs never reach the dwell threshold and
    are silently dropped."""

    def __init__(self, min_attention_s: float = 4.0, revisit_threshold: int = 2,
                 revisit_min_dwell_s: float = 1.5):
        self._min_attention_s = max(0.5, float(min_attention_s))
        self._revisit_threshold = max(2, int(revisit_threshold))
        self._revisit_min_dwell_s = max(0.2, float(revisit_min_dwell_s))

    @property
    def dwell_threshold_s(self) -> float:
        return self._min_attention_s

    def worth_capturing(self, view: DocumentView, dwell_so_far_s: float) -> bool:
        if dwell_so_far_s >= self._min_attention_s:
            return True
        # Returning to a unit repeatedly is itself a signal of importance, even
        # with a shorter dwell.
        if view.revisit_count >= self._revisit_threshold and dwell_so_far_s >= self._revisit_min_dwell_s:
            return True
        return False
