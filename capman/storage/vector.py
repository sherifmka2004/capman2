"""
ChromaDB adapter for semantic search over sessions, knowledge nodes,
and visited page content.

Page text is chunked into ~1000-char windows and embedded so that the
chat retrieval can pull only the most semantically relevant slices —
saves LLM context tokens AND gives sharper answers.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

CHUNK_CHARS = 1600
CHUNK_OVERLAP = 100


def _chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks on sentence/word boundaries."""
    if not text or len(text) <= size:
        return [text] if text else []
    text = re.sub(r"\s+", " ", text).strip()
    chunks = []
    i = 0
    while i < len(text):
        end = min(i + size, len(text))
        # Try to break on sentence end
        if end < len(text):
            for delim in [". ", "? ", "! ", "\n", " "]:
                k = text.rfind(delim, i + size // 2, end)
                if k != -1:
                    end = k + len(delim)
                    break
        chunks.append(text[i:end].strip())
        i = end - overlap if end - overlap > i else end
    return [c for c in chunks if c]


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()[:16]


from capman.storage.interfaces import VectorStoreAdapter

class VectorStore(VectorStoreAdapter):
    COLLECTION = "capman_knowledge"

    def __init__(self, chroma_path: str, chunk_chars: int = CHUNK_CHARS, chunk_overlap: int = CHUNK_OVERLAP):
        self._path = str(Path(chroma_path).expanduser())
        self._chunk_chars = chunk_chars
        self._chunk_overlap = chunk_overlap
        self._client = None
        self._collection = None

    def _ensure_connected(self):
        if self._client is not None:
            return
        try:
            import chromadb  # type: ignore
            self._client = chromadb.PersistentClient(path=self._path)
            self._collection = self._client.get_or_create_collection(
                name=self.COLLECTION,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as e:
            logger.error("ChromaDB init failed: %s", e)
            raise

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def add_session_summary(self, session_id: str, summary: str) -> None:
        self._ensure_connected()
        try:
            self._collection.upsert(
                ids=[f"session:{session_id}"],
                documents=[summary],
                metadatas=[{"type": "session", "session_id": session_id}],
            )
        except Exception as e:
            logger.warning("Failed to index session %s: %s", session_id, e)

    def add_knowledge_node(self, node_id: str, title: str, text: str) -> None:
        self._ensure_connected()
        try:
            self._collection.upsert(
                ids=[f"node:{node_id}"],
                documents=[f"{title}\n\n{text}"],
                metadatas=[{"type": "knowledge_node", "node_id": node_id, "title": title}],
            )
        except Exception as e:
            logger.warning("Failed to index node %s: %s", node_id, e)

    def add_page_text(
        self,
        url: str,
        title: str,
        text: str,
        ts: float | None = None,
        headings: list[str] | None = None,
    ) -> int:
        """
        Embed a visited page's full text. Chunked + overlapped so that long
        pages remain searchable at the paragraph level.
        Returns the number of chunks indexed.
        """
        self._ensure_connected()
        if not text:
            return 0

        ts = ts or time.time()
        url_hash = _hash(url)
        chunks = _chunk_text(text, self._chunk_chars, self._chunk_overlap)
        if not chunks:
            return 0

        ids = []
        docs = []
        metas = []
        for i, chunk in enumerate(chunks):
            doc = chunk
            if i == 0 and (title or headings):
                # Prepend title + headings to first chunk for stronger anchoring
                header = (title or "").strip()
                if headings:
                    header += " | " + " | ".join(h[:60] for h in headings[:5])
                doc = header + "\n\n" + chunk
            ids.append(f"page:{url_hash}:{i}")
            docs.append(doc)
            metas.append({
                "type": "page",
                "url": url,
                "title": title or "",
                "ts": float(ts),
                "chunk": i,
                "total_chunks": len(chunks),
            })

        try:
            self._collection.upsert(ids=ids, documents=docs, metadatas=metas)
            return len(chunks)
        except Exception as e:
            logger.warning("Failed to index page %s: %s", url, e)
            return 0

    def add_doc_text(
        self,
        doc_path: str,
        doc_name: str,
        item_kind: str,
        item_index: int,
        item_label: str,
        text: str,
        ts: float | None = None,
        app: str = "",
    ) -> int:
        """
        Embed text the user actually *read* on a single slide / page / sheet /
        note. Mirrors `add_page_text` (chunked + overlapped) so semantic search
        can pull paragraph-sized slices out of long pages without ballooning
        SQLite payloads. Returns chunks indexed.
        """
        self._ensure_connected()
        if not text:
            return 0

        ts = ts or time.time()
        doc_id = doc_path or doc_name or "unknown"
        unit_hash = _hash(f"{doc_id}|{item_kind}|{item_index}|{item_label}")
        chunks = _chunk_text(text, self._chunk_chars, self._chunk_overlap)
        if not chunks:
            return 0

        ids, docs, metas = [], [], []
        anchor = f"{doc_name or doc_id} — {item_kind} {item_index}".strip()
        if item_label:
            anchor += f": {item_label}"

        for i, chunk in enumerate(chunks):
            doc = anchor + "\n\n" + chunk if i == 0 else chunk
            ids.append(f"doc:{unit_hash}:{i}")
            docs.append(doc)
            metas.append({
                "type": "doc",
                "doc_path": doc_path or "",
                "doc_name": doc_name or "",
                "item_kind": item_kind or "",
                "item_index": int(item_index or 0),
                "item_label": item_label or "",
                "app": app or "",
                "title": anchor,
                "ts": float(ts),
                "chunk": i,
                "total_chunks": len(chunks),
            })

        try:
            self._collection.upsert(ids=ids, documents=docs, metadatas=metas)
            return len(chunks)
        except Exception as e:
            logger.warning("Failed to index doc unit %s: %s", anchor, e)
            return 0

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 10, types: list[str] | None = None) -> list[dict]:
        self._ensure_connected()
        try:
            n = self.count()
            if n == 0:
                return []
            kwargs = {"query_texts": [query], "n_results": min(top_k, n)}
            if types:
                kwargs["where"] = {"type": {"$in": types}}
            results = self._collection.query(**kwargs)
            output = []
            for i, doc_id in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i]
                distance = results["distances"][0][i] if results.get("distances") else 0
                score = round(1.0 - distance, 4)
                output.append({
                    "id": doc_id,
                    "type": meta.get("type", ""),
                    "title": meta.get("title") or meta.get("session_id") or meta.get("url") or doc_id,
                    "url": meta.get("url", ""),
                    "score": score,
                    "text": results["documents"][0][i],
                    "metadata": meta,
                })
            return output
        except Exception as e:
            logger.error("Vector search failed: %s", e)
            return []

    def count(self) -> int:
        self._ensure_connected()
        try:
            return self._collection.count()
        except Exception:
            return 0


# ----------------------------------------------------------------------
# Process-wide instance cache
# ----------------------------------------------------------------------
_INSTANCES: dict[tuple[str, int, int], "VectorStore"] = {}


def get_vector_store(chroma_path: str,
                     chunk_chars: int = CHUNK_CHARS,
                     chunk_overlap: int = CHUNK_OVERLAP) -> "VectorStore":
    """Return a shared VectorStore for a path.

    Constructing one per call is expensive in a way that is easy to miss: the
    embedding function holds its ONNX InferenceSession and tokenizer as
    per-instance cached properties, so each new store loads its own copy of the
    model (~90 MB RSS). A single /chat/message request used to build four.
    """
    key = (str(Path(chroma_path).expanduser()), chunk_chars, chunk_overlap)
    inst = _INSTANCES.get(key)
    if inst is None:
        inst = VectorStore(*key)
        _INSTANCES[key] = inst
    return inst
