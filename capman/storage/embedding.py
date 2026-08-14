"""Text embedding via model2vec static embeddings.

Replaces Chroma's default ONNX all-MiniLM-L6-v2. Three reasons:

1. **Footprint.** The Chroma stack measured 162 MB on disk (chromadb_rust_bindings
   57M, onnxruntime 50M, kubernetes 18M, grpc 17M, tokenizers 11M, opentelemetry
   4.1M) — essentially the whole desktop bundle, to serve a few hundred vectors.
2. **Offline.** Chroma's embedder downloads its model at first use from
   `chroma-onnx-models.s3.amazonaws.com` into `~/.cache/chroma` (167 MB
   measured). For a tool whose entire promise is that data never leaves the
   machine, a silent network fetch on first run is the wrong default — and
   when it fails the exception was swallowed, leaving search permanently empty.
3. **Speed.** Static embeddings are a lookup, not a forward pass: ~200
   embeddings in 2 ms on CPU, against ~100 ms for the ONNX path. That is what
   lets embedding happen inline instead of in a fire-and-forget task.

The trade is quality: potion-base-8M scores ~50 MTEB against MiniLM-L6's ~56.
That is repaid by BM25 (see search.py) — and for this corpus, exact lexical
matching was the bigger gap by far.
"""
from __future__ import annotations

import logging
import struct
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "minishlab/potion-base-8M"
EMBEDDING_DIM = 256

_lock = threading.Lock()
_model = None
_model_name: str | None = None


def _bundled_model_dir() -> Path | None:
    """Weights shipped inside the PyInstaller bundle, if present."""
    import sys
    if getattr(sys, "frozen", False):
        candidate = Path(sys._MEIPASS) / "capman" / "assets" / "embedding_model"  # type: ignore[attr-defined]
        if candidate.is_dir():
            return candidate
    candidate = Path(__file__).parent.parent / "assets" / "embedding_model"
    return candidate if candidate.is_dir() else None


def get_model(name: str = DEFAULT_MODEL):
    """Load the static embedding model once per process.

    Prefers weights bundled with the app so a fresh install works with no
    network at all; falls back to the HF cache otherwise.
    """
    global _model, _model_name
    with _lock:
        if _model is not None and _model_name == name:
            return _model
        from model2vec import StaticModel

        bundled = _bundled_model_dir()
        if bundled is not None:
            logger.info("Loading bundled embedding model from %s", bundled)
            _model = StaticModel.from_pretrained(str(bundled))
        else:
            logger.info("Loading embedding model %s", name)
            _model = StaticModel.from_pretrained(name)
        _model_name = name
        return _model


def embed(texts: list[str], name: str = DEFAULT_MODEL):
    """Embed a batch. Returns a float32 numpy array of shape (n, EMBEDDING_DIM)."""
    if not texts:
        import numpy as np
        return np.zeros((0, EMBEDDING_DIM), dtype="float32")

    import numpy as np
    vectors = get_model(name).encode(texts)
    vectors = np.asarray(vectors, dtype="float32")
    # Normalise so a dot product is cosine similarity, which is what both the
    # sqlite-vec index and the numpy fallback assume.
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def embed_one(text: str, name: str = DEFAULT_MODEL):
    return embed([text], name)[0]


def to_blob(vector) -> bytes:
    """Pack a float32 vector for the durable `documents.embedding` column."""
    return struct.pack(f"{len(vector)}f", *(float(x) for x in vector))


def from_blob(blob: bytes):
    import numpy as np
    return np.frombuffer(blob, dtype="float32")
