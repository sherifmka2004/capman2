"""Episode signatures — run-length-encoded event sequences, hashed.

Two signatures per episode: exact (full RLE) for identity, and coarse
(first3+last3 RLE) for fuzzy clustering of near-identical attempts.
"""
from __future__ import annotations

import hashlib


def run_length_encode(names: list[str]) -> str:
    if not names:
        return ""
    parts: list[str] = []
    prev = names[0]
    count = 1
    for name in names[1:]:
        if name == prev:
            count += 1
        else:
            parts.append(f"{prev}x{count}")
            prev, count = name, 1
    parts.append(f"{prev}x{count}")
    return ">".join(parts)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def signature(names: list[str]) -> str:
    return _hash(run_length_encode(names))


def coarse_signature(names: list[str]) -> str:
    if len(names) <= 6:
        return _hash(run_length_encode(names))
    return _hash(run_length_encode(names[:3] + names[-3:]))
