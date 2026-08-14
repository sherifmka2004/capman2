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
        retention_task = asyncio.create_task(self._retention_loop())

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
            await self._db.flush()

            # Flush current session
            session = self._detector.flush()
            if session:
                await self._close_session(session)

            analysis_task.cancel()
            timeout_task.cancel()
            recategorize_task.cancel()
            retention_task.cancel()
            try:
                await analysis_task
                await timeout_task
                await recategorize_task
                await retention_task
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

        # Page text → persist full text as a searchable document, slim the payload
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

        # Resolve session membership BEFORE the write, so the row lands once
        # with its session_id instead of being rewritten by assign_session_bulk.
        completed, current = self._detector.ingest(event)
        session_id = None
        if current is not None and current.events and current.events[-1] is event:
            session_id = current.id

        await self._db.queue_event(event, session_id)

        if completed:
            await self._close_session(completed)

    async def _embed_page_text(self, url: str, title: str, text: str,
                               ts: float, headings: list) -> None:
        """Persist page text for keyword search, and embed it for semantic search."""
        if not text or not text.strip():
            return

        # SQLite owns the full text. Previously it lived only in the vector
        # store, with a 300-char excerpt in the event payload, so keyword
        # search was impossible and the vector store could not be replaced
        # without losing data.
        try:
            import hashlib
            doc_id = "page:" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
            await self._db.upsert_document(
                doc_id, "page", text, ts=ts, title=title, uri=url,
            )
        except Exception as e:
            logger.warning("Page document persist failed for %s: %s", url[:60], e)

        await self._embed_pending()

    async def _embed_doc_text(self, doc_path: str, doc_name: str, item_kind: str,
                              item_index: int, item_label: str, text: str,
                              ts: float, app: str) -> None:
        """Persist read-document text for keyword search, and embed it."""
        if not text or not text.strip():
            return

        try:
            import hashlib
            key = f"{doc_path}|{item_kind}|{item_index}|{item_label}"
            doc_id = "doc:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
            await self._db.upsert_document(
                doc_id, "doc", text, ts=ts,
                title=f"{doc_name or doc_path} — {item_kind} {item_index}".strip(),
                uri=doc_path,
            )
        except Exception as e:
            logger.warning("Doc document persist failed for %s: %s", doc_path[:60], e)

        await self._embed_pending()

    async def _close_session(self, session: Session) -> None:
        """Persist session and schedule analysis."""
        # Buffered events must hit disk before anything reads the session back.
        await self._db.flush()
        await self._db.upsert_session(session)
        # Membership is stamped at insert; this only repairs rows that missed it.
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
            await self._persist_screenshots(enriched_session)
            from capman.pipeline.analyzer import SessionAnalyzer
            analyzer = SessionAnalyzer(self._config)
            analysis = await analyzer.analyze(enriched_session)
            await self._db.save_analysis(analysis)
            await self._index_session_summary(analysis)

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

    async def _embed_pending(self) -> None:
        """Embed any documents that do not yet have a vector.

        Static embeddings are fast enough (~200 texts in 2ms) to do inline
        rather than in a fire-and-forget task whose failures nobody sees.
        """
        try:
            from capman.storage.vectors import VectorIndex
            await VectorIndex(self._db).index_documents(limit=64)
        except Exception as e:
            logger.warning("Embedding pending documents failed: %s", e)

    async def _persist_screenshots(self, session: Session) -> None:
        """Record screenshots and their OCR text.

        Two long-standing gaps closed here. The `screenshots` table has existed
        since the first commit and was never written to — three UIs rendered a
        count that was always zero while the disk filled. And OCR text was only
        ever mutated onto the in-memory Event, after its row had already been
        written, so it was recomputed from the image every time and never
        searchable.
        """
        from capman.events import EventType

        rows, docs = [], []
        for event in session.events:
            if event.type != EventType.SCREENSHOT:
                continue
            path = event.payload.get("path", "")
            if not path:
                continue
            ocr = event.payload.get("ocr_text", "") or ""
            rows.append((event.id, event.id, path, event.ts, ocr, session.id))
            if ocr.strip():
                docs.append({
                    "id": f"ocr:{event.id}", "kind": "ocr", "body": ocr, "ts": event.ts,
                    "title": event.window_title or "", "uri": path,
                    "ref_id": event.id, "session_id": session.id,
                })

        if not rows:
            return
        try:
            await self._db.save_screenshots(rows)
            if docs:
                await self._db.upsert_documents_bulk(docs)
            logger.info("Persisted %d screenshots (%d with OCR text) for session %s",
                        len(rows), len(docs), session.id[:8])
        except Exception as e:
            logger.warning("Screenshot persist failed for session %s: %s", session.id, e)

    async def _index_session_summary(self, analysis) -> None:
        """Make an analysed session searchable.

        VectorStore.add_session_summary had no production caller, so the vector
        store held zero `session` documents and /context/suggest's "similar past
        sessions" was permanently empty. Index into both stores here.
        """
        parts = [analysis.problem_statement or "", analysis.approach_description or ""]
        for attr in ("methodology_tags", "knowledge_applied", "knowledge_acquired"):
            vals = getattr(analysis, attr, None) or []
            if isinstance(vals, list):
                parts.append(" ".join(str(v) for v in vals))
        summary = "\n".join(p for p in parts if p and p.strip())
        if not summary.strip():
            return

        try:
            await self._db.upsert_document(
                f"session:{analysis.session_id}", "session", summary,
                ts=getattr(analysis, "analyzed_at", None) or time.time(),
                title=(analysis.problem_statement or "")[:120],
                ref_id=analysis.session_id, session_id=analysis.session_id,
            )
        except Exception as e:
            logger.warning("Session summary persist failed for %s: %s", analysis.session_id, e)

        await self._embed_pending()

    async def _save_playbook(self, analysis) -> None:
        """Persist a troubleshooting playbook to DB, markdown, and vector store."""
        try:
            knowledge_dir = self._config.get("storage", {}).get("knowledge_dir", "~/.capman/knowledge")

            await self._db.save_playbook(analysis.playbook)

            # Keyword-searchable independently of whether embedding succeeds.
            pb = analysis.playbook
            body = "\n".join(
                str(p) for p in (
                    [pb.title, pb.domain, pb.root_cause]
                    + list(pb.symptoms or []) + list(pb.context_signals or [])
                    + list(pb.fix or []) + list(pb.verification or [])
                ) if p
            )
            if body.strip():
                await self._db.upsert_document(
                    f"playbook:{pb.id}", "playbook", body,
                    ts=getattr(pb, "created_at", None) or time.time(),
                    title=pb.title or "", ref_id=pb.id, session_id=analysis.session_id,
                )

            from capman.knowledge.playbooks import save_playbook_markdown
            from pathlib import Path
            path = save_playbook_markdown(analysis.playbook, Path(knowledge_dir).expanduser())
            logger.info("Playbook saved: %s", path)

            await self._embed_pending()
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

    async def _retention_loop(self) -> None:
        """Prune expired low-signal events on a fixed interval.

        Deliberately not run at startup: a first prune should not compete with
        sensor initialisation, and nothing is urgent on a timeline that has
        already been growing unbounded.
        """
        cfg = self._config.get("storage", {}).get("retention", {})
        if not cfg.get("enabled", True):
            logger.info("Retention disabled by config — event timeline will grow unbounded")
            return

        interval = max(1, int(cfg.get("check_interval_hours", 24))) * 3600
        await asyncio.sleep(600)  # let startup settle

        while True:
            try:
                from capman.storage.retention import prune_events
                await self._db.flush()
                deleted = await prune_events(self._db, self._config)
                if deleted:
                    logger.info("Retention removed %d events: %s", sum(deleted.values()), deleted)
            except Exception as e:
                logger.warning("Retention pass failed: %s", e, exc_info=True)
            await asyncio.sleep(interval)

    def stop(self) -> None:
        self._stop.set()
