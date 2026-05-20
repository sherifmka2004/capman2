"""
PipelineRunner — connects the async event queue through all pipeline stages:
buffer → session detection → storage → (async) analysis
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from capman.events import Event, Session
from capman.pipeline.buffer import AsyncEventBuffer
from capman.pipeline.enricher import Enricher
from capman.pipeline.session import SessionDetector
from capman.storage.timeline import TimelineDB

logger = logging.getLogger(__name__)


class PipelineRunner:
    def __init__(self, buffer: AsyncEventBuffer, db: TimelineDB, config: dict):
        self._buffer = buffer
        self._db = db
        self._config = config
        self._detector = SessionDetector(config)
        self._enricher = Enricher(config)
        self._stop = asyncio.Event()
        self._analysis_queue: asyncio.Queue[Session] = asyncio.Queue()
        self._batch_delay_s: float = config.get("pipeline", {}).get("analysis", {}).get("batch_delay_s", 300)
        _runner_cfg = config.get("pipeline", {}).get("runner", {})
        self._excerpt_chars: int = int(_runner_cfg.get("excerpt_chars", 300))
        _vec_cfg = config.get("storage", {}).get("vector", {})
        self._chunk_chars: int = int(_vec_cfg.get("chunk_chars", 800))
        self._chunk_overlap: int = int(_vec_cfg.get("chunk_overlap", 100))

    async def run(self) -> None:
        """Main pipeline loop + analysis consumer."""
        analysis_task = asyncio.create_task(self._analysis_loop())
        timeout_task = asyncio.create_task(self._timeout_checker())
        recategorize_task = asyncio.create_task(self._daily_recategorize_loop())

        try:
            while not self._stop.is_set():
                try:
                    event = await asyncio.wait_for(self._buffer.get(), timeout=1.0)
                    await self._process_event(event)
                except asyncio.TimeoutError:
                    pass
        finally:
            # Drain remaining events
            remaining = await self._buffer.drain()
            for event in remaining:
                await self._process_event(event)

            # Flush current session
            session = self._detector.flush()
            if session:
                await self._close_session(session)

            analysis_task.cancel()
            timeout_task.cancel()
            recategorize_task.cancel()
            try:
                await analysis_task
                await timeout_task
                await recategorize_task
            except asyncio.CancelledError:
                pass

    async def _process_event(self, event: Event) -> None:
        from capman.events import EventType

        # Publish interactive shell commands so the filesystem sensor can attribute
        # file ops that those commands caused to direct user action.
        if event.type == EventType.SHELL_COMMAND:
            try:
                from capman.sensors.activity_context import record_shell_command
                pid = event.payload.get("pid")
                record_shell_command(
                    command=event.payload.get("command", ""),
                    cwd=event.payload.get("cwd", ""),
                    pid=int(pid) if isinstance(pid, (int, str)) and str(pid).isdigit() else None,
                    command_id=event.payload.get("command_id", "") or event.id,
                    ts=event.ts,
                )
            except Exception:
                pass

        # Page text → embed full content into ChromaDB, store slim ref in SQLite
        if event.type == EventType.PAGE_TEXT:
            full = event.payload.get("excerpt", "") or ""
            asyncio.create_task(self._embed_page_text(
                url=event.payload.get("url", ""),
                title=event.payload.get("title", ""),
                text=full,
                ts=event.ts,
                headings=event.payload.get("headings", []),
            ))
            event.payload = {
                **event.payload,
                "excerpt": full[:self._excerpt_chars],
                "full_chars_indexed": len(full),
            }

        # Document content the user actually read → embed full text, slim SQLite
        if event.type == EventType.DOC_CONTENT:
            full = event.payload.get("text", "") or ""
            asyncio.create_task(self._embed_doc_text(
                doc_path=event.payload.get("doc_path", ""),
                doc_name=event.payload.get("doc_name", ""),
                item_kind=event.payload.get("item_kind", ""),
                item_index=int(event.payload.get("item_index", 0) or 0),
                item_label=event.payload.get("item_label", ""),
                text=full,
                ts=event.ts,
                app=event.payload.get("app", "") or event.app or "",
            ))
            event.payload = {
                **event.payload,
                "text": full[:self._excerpt_chars],
                "full_chars_indexed": len(full),
            }

        await self._db.insert_event(event)

        completed, _ = self._detector.ingest(event)
        if completed:
            await self._close_session(completed)

    async def _embed_page_text(self, url: str, title: str, text: str,
                               ts: float, headings: list) -> None:
        """Push page text into the vector store for semantic retrieval."""
        if not text or not text.strip():
            return
        try:
            from capman.storage.vector import VectorStore
            chroma_path = self._config.get("storage", {}).get("chroma_path", "~/.capman/chroma")
            vs = VectorStore(chroma_path, self._chunk_chars, self._chunk_overlap)
            count = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: vs.add_page_text(url=url, title=title, text=text, ts=ts, headings=headings),
            )
            if count:
                logger.info("Embedded page %s (%d chunks, %d chars)", url[:60], count, len(text))
        except Exception as e:
            logger.debug("Page text embedding skipped: %s", e)

    async def _embed_doc_text(self, doc_path: str, doc_name: str, item_kind: str,
                              item_index: int, item_label: str, text: str,
                              ts: float, app: str) -> None:
        """Embed read-document text into the vector store."""
        if not text or not text.strip():
            return
        try:
            from capman.storage.vector import VectorStore
            chroma_path = self._config.get("storage", {}).get("chroma_path", "~/.capman/chroma")
            vs = VectorStore(chroma_path, self._chunk_chars, self._chunk_overlap)
            count = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: vs.add_doc_text(
                    doc_path=doc_path, doc_name=doc_name,
                    item_kind=item_kind, item_index=item_index, item_label=item_label,
                    text=text, ts=ts, app=app,
                ),
            )
            if count:
                logger.info("Embedded %s %d of %s (%d chunks, %d chars)",
                            item_kind, item_index, doc_name or doc_path or app,
                            count, len(text))
        except Exception as e:
            logger.debug("Doc text embedding skipped: %s", e)

    async def _close_session(self, session: Session) -> None:
        """Persist session and schedule analysis."""
        await self._db.upsert_session(session)
        await self._db.assign_session_bulk(
            [e.id for e in session.events], session.id
        )
        min_events = self._config.get("pipeline", {}).get("session", {}).get("min_session_events", 5)
        min_duration = self._config.get("pipeline", {}).get("session", {}).get("min_session_duration_s", 120)
        duration = (session.ended_at or time.time()) - session.started_at

        # Always write document nodes immediately — no minimum event/duration gate
        try:
            await self._write_document_nodes(session)
        except Exception as e:
            logger.debug("Document node write skipped: %s", e)

        if len(session.events) >= min_events and duration >= min_duration:
            await self._analysis_queue.put(session)
        else:
            await self._db.upsert_session(session)
            # Mark as skipped
            await self._db._db.execute(
                "UPDATE sessions SET analyzed = 2 WHERE id = ?", (session.id,)
            )
            await self._db._db.commit()
            logger.debug("Session %s too short/small for analysis, skipped", session.id)

    async def _write_document_nodes(self, session: Session) -> None:
        """Extract all document navigation events from session and write doc markdown nodes."""
        from capman.events import EventType
        from capman.knowledge.markdown import save_document_node
        from pathlib import Path

        doc_event_types = {
            EventType.DOC_OPEN, EventType.DOC_SLIDE_CHANGE,
            EventType.DOC_PAGE_CHANGE, EventType.DOC_SHEET_CHANGE, EventType.DOC_NOTE_OPEN,
        }
        doc_events = [e.payload for e in session.events if e.type in doc_event_types]
        if not doc_events:
            return

        knowledge_dir = Path(
            self._config.get("storage", {}).get("knowledge_dir", "~/.capman/knowledge")
        ).expanduser()

        try:
            path = save_document_node(
                doc_events=doc_events,
                session_id=session.id,
                session_started_at=session.started_at,
                knowledge_dir=knowledge_dir,
            )
            if path:
                logger.info("Document node written: %s", path)
        except Exception as e:
            logger.error("Failed to write document node: %s", e)

    async def _analysis_loop(self) -> None:
        """Consume completed sessions and run LLM analysis after batch_delay."""
        while True:
            session = await self._analysis_queue.get()
            # Wait batch_delay before analyzing (allows session data to settle)
            await asyncio.sleep(self._batch_delay_s)
            await self._analyze_session(session)

    async def _analyze_session(self, session: Session) -> None:
        analysis_enabled = self._config.get("pipeline", {}).get("analysis", {}).get("enabled", True)
        if not analysis_enabled:
            return

        try:
            enriched_session = self._enricher.enrich_session(session)
            from capman.pipeline.analyzer import SessionAnalyzer
            analyzer = SessionAnalyzer(self._config)
            analysis = await analyzer.analyze(enriched_session)
            await self._db.save_analysis(analysis)

            # Save triples to DB and markdown
            if analysis.triples:
                for triple in analysis.triples:
                    await self._db.save_triple(triple)
                await self._update_knowledge_graph(analysis)

            # Save troubleshooting playbook (Pass 4 output)
            if analysis.playbook:
                await self._save_playbook(analysis)

            # Update knowledge gaps tracker
            await self._update_knowledge_gaps(session, analysis)

            logger.info("Analysis complete for session %s: %s",
                        session.id[:8], analysis.problem_statement[:60])
        except Exception as e:
            logger.error("Analysis failed for session %s: %s", session.id, e)

    async def _update_knowledge_graph(self, analysis) -> None:
        try:
            knowledge_dir = self._config.get("storage", {}).get("knowledge_dir", "~/.capman/knowledge")
            from capman.knowledge.graph import KnowledgeGraph
            from capman.knowledge.merger import GraphMerger
            graph = KnowledgeGraph(knowledge_dir=knowledge_dir)
            graph.load()
            merger = GraphMerger()
            merger.merge(graph, analysis.triples, session_id=analysis.session_id)
            if analysis.chain_of_thought:
                graph.add_chain_of_thought(analysis.chain_of_thought)
            graph.save()
        except Exception as e:
            logger.error("Knowledge graph update failed: %s", e)

    async def _save_playbook(self, analysis) -> None:
        """Persist a troubleshooting playbook to DB, markdown, and vector store."""
        try:
            knowledge_dir = self._config.get("storage", {}).get("knowledge_dir", "~/.capman/knowledge")
            chroma_path = self._config.get("storage", {}).get("chroma_path", "~/.capman/chroma")

            await self._db.save_playbook(analysis.playbook)

            from capman.knowledge.playbooks import save_playbook_markdown, index_playbook_in_vector_store
            from pathlib import Path
            path = save_playbook_markdown(analysis.playbook, Path(knowledge_dir).expanduser())
            logger.info("Playbook saved: %s", path)

            # Index for /context/suggest semantic retrieval (off the loop)
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: index_playbook_in_vector_store(analysis.playbook, str(Path(chroma_path).expanduser())),
            )
        except Exception as e:
            logger.error("Playbook save failed: %s", e)

    async def _update_knowledge_gaps(self, session, analysis) -> None:
        try:
            from capman.knowledge.gaps import (
                update_gaps_from_analysis, update_gaps_from_search_queries,
            )
            await update_gaps_from_analysis(self._db, analysis)
            await update_gaps_from_search_queries(self._db, session.id, session.search_queries)
        except Exception as e:
            logger.debug("Knowledge gap update failed: %s", e)

    async def _timeout_checker(self) -> None:
        """Periodically flush sessions that timed out without new events."""
        while True:
            await asyncio.sleep(30)
            completed, _ = self._detector.check_timeouts()
            if completed:
                await self._close_session(completed)

    async def _daily_recategorize_loop(self) -> None:
        """Run LLM brain recategorization once per day (checks every hour)."""
        import datetime
        last_run_day: int | None = None
        await asyncio.sleep(300)  # give startup a few minutes to settle
        while True:
            today = datetime.date.today().toordinal()
            if last_run_day != today:
                try:
                    from capman.pipeline.brain_recategorizer import recategorize
                    ok = await recategorize(self._db, self._config)
                    if ok:
                        last_run_day = today
                        logger.info("Daily brain recategorization complete")
                except Exception as e:
                    logger.debug("Daily brain recategorization skipped: %s", e)
            await asyncio.sleep(3600)

    def stop(self) -> None:
        self._stop.set()
