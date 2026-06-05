from cryptography.fernet import Fernet
import os
import json
from capman.storage.interfaces import VectorStoreAdapter, TimelineDBAdapter

class EncryptionDecorator:
    def __init__(self, key_env: str = "CAPMAN_MASTER_KEY"):
        key = os.environ.get(key_env)
        if not key:
            raise ValueError(f"Encryption key not set in environment variable {key_env}")
        self._fernet = Fernet(key.encode())

    def encrypt(self, data: str) -> str:
        return self._fernet.encrypt(data.encode()).decode()

    def decrypt(self, data: str) -> str:
        return self._fernet.decrypt(data.encode()).decode()

class EncryptedVectorStore(VectorStoreAdapter):
    def __init__(self, wrapped: VectorStoreAdapter, key_env: str = "CAPMAN_MASTER_KEY"):
        self._wrapped = wrapped
        self._enc = EncryptionDecorator(key_env)

    def add_session_summary(self, session_id: str, summary: str) -> None:
        self._wrapped.add_session_summary(session_id, self._enc.encrypt(summary))

    def add_knowledge_node(self, node_id: str, title: str, text: str) -> None:
        self._wrapped.add_knowledge_node(node_id, title, self._enc.encrypt(text))

    def add_page_text(self, url: str, title: str, text: str, ts: float | None = None, headings: list[str] | None = None) -> int:
        return self._wrapped.add_page_text(url, title, self._enc.encrypt(text), ts, headings)

    def add_doc_text(self, doc_path: str, doc_name: str, item_kind: str, item_index: int, item_label: str, text: str, ts: float | None = None, app: str = "") -> int:
        return self._wrapped.add_doc_text(doc_path, doc_name, item_kind, item_index, item_label, self._enc.encrypt(text), ts, app)

    def search(self, query: str, top_k: int = 10, types: list[str] | None = None) -> list[dict]:
        results = self._wrapped.search(query, top_k, types)
        for res in results:
            if "text" in res:
                res["text"] = self._enc.decrypt(res["text"])
        return results

    def count(self) -> int:
        return self._wrapped.count()
