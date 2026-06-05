from capman.config import load_config
from capman.storage.interfaces import VectorStoreAdapter, TimelineDBAdapter
from capman.storage.vector import VectorStore
from capman.storage.timeline import TimelineDB
from capman.storage.encryption import EncryptedVectorStore

def get_vector_store(config: dict) -> VectorStoreAdapter:
    storage_cfg = config.get("storage", {})
    path = storage_cfg.get("local", {}).get("vector_path", "~/.capman/chroma")
    adapter = VectorStore(chroma_path=path)
    
    if storage_cfg.get("encryption_enabled", False):
        key_env = storage_cfg.get("encryption_key_env", "CAPMAN_MASTER_KEY")
        return EncryptedVectorStore(adapter, key_env=key_env)
    return adapter


def get_timeline_db(config: dict) -> TimelineDBAdapter:
    storage_cfg = config.get("storage", {})
    path = storage_cfg.get("local", {}).get("timeline_path", "~/.capman/timeline.db")
    return TimelineDB(db_path=path)
