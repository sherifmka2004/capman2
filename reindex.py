
import asyncio
import json
import sqlite3
from pathlib import Path
from capman.storage.vector import VectorStore
from capman.events import EventType

async def reindex():
    chroma_path = "~/.capman/chroma"
    db_path = "~/.capman/timeline.db"
    
    # 1. Initialize VectorStore (will create new quantized collection)
    vector_store = VectorStore(chroma_path=chroma_path)
    # This call initializes the client and collection
    vector_store._ensure_connected()
    print("New quantized ChromaDB collection created.")

    # 2. Connect to SQLite to fetch data
    conn = sqlite3.connect(Path(db_path).expanduser())
    conn.row_factory = sqlite3.Row
    
    # 3. Re-index DOC_CONTENT events
    print("Re-indexing DOC_CONTENT...")
    cursor = conn.execute("SELECT payload FROM events WHERE type = 'doc_content'")
    for row in cursor:
        payload = json.loads(row['payload'])
        vector_store.add_doc_text(
            doc_path=payload.get("doc_path", ""),
            doc_name=payload.get("doc_name", ""),
            item_kind=payload.get("item_kind", "unit"),
            item_index=payload.get("item_index", 0),
            item_label=payload.get("item_label", ""),
            text=payload.get("text", ""),
            app=payload.get("app", "")
        )
    print("DOC_CONTENT re-indexed.")

    # 4. Re-index PAGE_TEXT events
    print("Re-indexing PAGE_TEXT...")
    cursor = conn.execute("SELECT payload FROM events WHERE type = 'page_text'")
    for row in cursor:
        payload = json.loads(row['payload'])
        vector_store.add_page_text(
            url=payload.get("url", ""),
            title=payload.get("title", ""),
            text=payload.get("excerpt", ""),
            headings=payload.get("headings", [])
        )
    print("PAGE_TEXT re-indexed.")
    
    # 5. Re-index Knowledge Nodes
    print("Re-indexing Knowledge Nodes...")
    knowledge_dir = Path("~/.capman/knowledge").expanduser()
    for md_file in knowledge_dir.glob("**/*.md"):
        # Very simple parser to get title and body
        content = md_file.read_text()
        # Frontmatter might be present, skip it
        lines = content.splitlines()
        start_line = 0
        if lines and lines[0] == '---':
            for i, line in enumerate(lines[1:], 1):
                if line == '---':
                    start_line = i + 1
                    break
        
        body = "\n".join(lines[start_line:]).strip()
        # title is usually the first H1
        title = md_file.name
        for line in lines:
            if line.startswith("# "):
                title = line[2:].strip()
                break
        
        vector_store.add_knowledge_node(node_id=md_file.stem, title=title, text=body)
    print("Knowledge Nodes re-indexed.")
    
    conn.close()
    print("Re-indexing complete.")

if __name__ == "__main__":
    asyncio.run(reindex())
