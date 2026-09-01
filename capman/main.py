"""
capman2 — Personal Cognitive Workflow Capture Engine
CLI entry point and daemon orchestrator.

Commands:
  capman start    — Start the capture daemon
  capman stop     — Stop the daemon (sends SIGTERM to PID file)
  capman status   — Show current status
  capman query    — Semantic search over captured knowledge
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from capman.config import load_config, get_data_dir

console = Console()
logger = logging.getLogger("capman")


def _inject_secrets(config: dict) -> None:
    """Load API keys from ~/.capman/config.toml [secrets] into environment if not already set."""
    import tomllib
    user_config_path = Path("~/.capman/config.toml").expanduser()
    if not user_config_path.exists():
        return
    try:
        with open(user_config_path, "rb") as f:
            user_cfg = tomllib.load(f)
        secrets = user_cfg.get("secrets", {})
        for env_key, cfg_key in [
            ("ANTHROPIC_API_KEY", "anthropic_api_key"),
            ("OPENROUTER_API_KEY", "openrouter_api_key"),
        ]:
            if not os.environ.get(env_key) and secrets.get(cfg_key):
                os.environ[env_key] = secrets[cfg_key]
    except Exception:
        pass


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
def cli():
    """capman2 — cognitive workflow capture engine"""


def _detect_headless() -> bool:
    """Return True if running without a display (server/SSH without X11)."""
    if sys.platform == "linux":
        return not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")
    return False


@cli.command()
@click.option("--config-dir", default=None, help="Path to config directory")
@click.option("--batch-delay", default=None, type=int, help="Override analysis batch delay (seconds)")
@click.option("--headless", is_flag=True, default=False, help="Force headless mode (no display sensors)")
def start(config_dir, batch_delay, headless):
    """Start the capman capture daemon."""
    extra_overrides = []
    if headless or _detect_headless():
        extra_overrides.append("headless")
        console.print("[dim]Headless mode detected — display sensors disabled[/dim]")

    config = load_config(Path(config_dir) if config_dir else None, extra_overrides=extra_overrides)
    if batch_delay is not None:
        config.setdefault("pipeline", {}).setdefault("analysis", {})["batch_delay_s"] = batch_delay

    _inject_secrets(config)
    setup_logging(config["core"]["log_level"])
    data_dir = get_data_dir(config)

    # Write PID file
    pid_file = data_dir / "capman.pid"
    pid_file.write_text(str(os.getpid()))

    console.print(f"[bold green]capman2 starting[/bold green]")
    console.print(f"  Data dir:  {data_dir}")
    console.print(f"  DB:        {config['storage']['sqlite_path']}")
    console.print(f"  API port:  {config['api']['port']}")

    try:
        asyncio.run(_run_daemon(config))
    except KeyboardInterrupt:
        pass
    finally:
        pid_file.unlink(missing_ok=True)
        console.print("[yellow]capman2 stopped.[/yellow]")


async def _run_daemon(config: dict) -> None:
    from capman.storage.timeline import TimelineDB
    from capman.pipeline.buffer import AsyncEventBuffer
    from capman.pipeline.runner import PipelineRunner
    from capman.sensors.registry import SensorRegistry

    # Initialize storage
    db = TimelineDB(config["storage"]["sqlite_path"])
    await db.migrate()
    logger.info("Storage initialized: %s", config["storage"]["sqlite_path"])

    # Rebuild the keyword index from content captured before FTS existed.
    # Idempotent and cheap once populated (~50ms on a week of data), so it is
    # safe to run on every start rather than gating it behind a flag.
    try:
        from capman.storage.backfill import backfill_documents
        counts = await backfill_documents(db, config["storage"].get("knowledge_dir"))
        if any(counts.values()):
            logger.info("Search index backfilled: %s", counts)
    except Exception as e:
        logger.warning("Search index backfill skipped: %s", e, exc_info=True)

    # Shared event queue
    queue: asyncio.Queue = asyncio.Queue(maxsize=10_000)
    buffer = AsyncEventBuffer.__new__(AsyncEventBuffer)
    buffer._queue = queue

    # Start API server in background
    api_task = asyncio.create_task(_start_api_server(config, db))

    # Discover and instantiate sensors
    registry = SensorRegistry()
    registry.discover()
    sensor_classes = registry.get_enabled(config)
    sensors = [cls(config, queue) for cls in sensor_classes]

    sensor_names = [cls.sensor_id for cls in sensor_classes]
    logger.info("Sensors enabled: %s", ", ".join(sensor_names))
    console.print(f"  Sensors:   {', '.join(sensor_names)}")

    # Deep file monitor (privileged helper) status hint
    deep = config.get("sensors", {}).get("filesystem", {}).get("deep_monitor", "off")
    if deep and deep != "off":
        from pathlib import Path as _P
        helper = _P(__file__).resolve().parents[1] / "tools" / "capman-fsmon" / "fsmon.py"
        console.print(f"  Deep FS:   [yellow]deep_monitor={deep}[/yellow] — start the privileged helper separately:")
        console.print(f"             [dim]sudo python3 {helper} --backend auto[/dim]")
        if sys.platform == "darwin":
            console.print("             [dim](macOS: backend=eslogger needs Full Disk Access; see docs/FILE_MONITORING.md)[/dim]")
        logger.info("deep_monitor=%s configured; run helper: sudo python3 %s --backend auto", deep, helper)
    else:
        logger.debug("deep_monitor disabled (file opens/reads + PID attribution not captured)")

    console.print(f"[bold green]Running...[/bold green] (Ctrl+C to stop)\n")

    # Setup sensors
    for sensor in sensors:
        await sensor.setup()

    # Pipeline runner
    pipeline = PipelineRunner(buffer, db, config)

    # Setup shutdown handler
    stop_event = asyncio.Event()

    def _shutdown(sig, frame):
        console.print("\n[yellow]Shutting down...[/yellow]")
        for sensor in sensors:
            sensor.stop()
        pipeline.stop()
        stop_event.set()

    import threading as _threading
    if _threading.current_thread() is _threading.main_thread():
        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

    # Run all sensors + pipeline concurrently
    sensor_tasks = [asyncio.create_task(s.run()) for s in sensors]
    pipeline_task = asyncio.create_task(pipeline.run())

    await stop_event.wait()

    # Graceful shutdown
    for task in sensor_tasks:
        task.cancel()
    pipeline_task.cancel()
    api_task.cancel()

    await asyncio.gather(*sensor_tasks, pipeline_task, api_task, return_exceptions=True)

    for sensor in sensors:
        await sensor.teardown()

    event_count = await db.get_event_count()
    console.print(f"[dim]Total events captured: {event_count}[/dim]")
    await db.close()


async def _start_api_server(config: dict, db) -> None:
    """
    Start uvicorn — runs HTTP on the configured port AND HTTPS on port+1.
    HTTPS uses an auto-generated self-signed cert (cached after first start).

    Why HTTPS too? Browsers block plain-HTTP requests from HTTPS pages
    (mixed content). Without HTTPS we can't capture interactions from sites
    like openrouter.ai, github.com, claude.ai, etc.
    """
    try:
        import uvicorn
        from capman.api.server import create_app
        from capman.api.tls import ensure_tls_cert

        app = create_app(config, db)
        host = config["api"]["host"]
        http_port = config["api"]["port"]
        https_port = config["api"].get("https_port", http_port + 1)

        data_dir = get_data_dir(config)
        cert_path, key_path = ensure_tls_cert(data_dir, host)

        # Spin up both servers in parallel
        servers = []
        http_cfg = uvicorn.Config(app, host=host, port=http_port,
                                   log_level="info", access_log=True)
        servers.append(asyncio.create_task(uvicorn.Server(http_cfg).serve()))
        console.print(f"  HTTP:      [cyan]http://{host}:{http_port}[/cyan]")

        if cert_path and key_path:
            https_cfg = uvicorn.Config(app, host=host, port=https_port,
                                        log_level="info", access_log=True,
                                        ssl_certfile=str(cert_path),
                                        ssl_keyfile=str(key_path))
            servers.append(asyncio.create_task(uvicorn.Server(https_cfg).serve()))
            console.print(f"  HTTPS:     [cyan]https://{host}:{https_port}[/cyan]  (self-signed cert)")
            logger.info("HTTPS server live on port %d (cert: %s)", https_port, cert_path)
        else:
            console.print("  [yellow]HTTPS disabled[/yellow] — install 'cryptography' for HTTPS support")

        await asyncio.gather(*servers, return_exceptions=True)
    except Exception as e:
        # A dead API means no web UI, no /query, no chat — never whisper about it.
        logger.error("API server failed to start: %s", e, exc_info=True)
        console.print(f"  [red]API server failed to start:[/red] {e}")


@cli.command()
def stop():
    """Stop a running capman daemon."""
    config = load_config()
    data_dir = get_data_dir(config)
    pid_file = data_dir / "capman.pid"

    if not pid_file.exists():
        console.print("[red]No capman daemon running (no PID file found)[/red]")
        sys.exit(1)

    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        console.print(f"[green]Sent SIGTERM to capman daemon (PID {pid})[/green]")
    except ProcessLookupError:
        console.print(f"[yellow]PID {pid} not found — daemon may have already stopped[/yellow]")
        pid_file.unlink(missing_ok=True)


@cli.command()
def status():
    """Show current capman daemon status."""
    config = load_config()
    data_dir = get_data_dir(config)
    pid_file = data_dir / "capman.pid"

    running = False
    if pid_file.exists():
        pid = int(pid_file.read_text().strip())
        try:
            os.kill(pid, 0)  # Signal 0 checks if process exists
            running = True
            console.print(f"[green]capman2 running[/green] (PID {pid})")
        except ProcessLookupError:
            console.print("[yellow]capman2 not running[/yellow] (stale PID file)")
    else:
        console.print("[yellow]capman2 not running[/yellow]")

    if running:
        # Show stats from DB
        try:
            import sqlite3
            conn = sqlite3.connect(config["storage"]["sqlite_path"])
            events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            analyzed = conn.execute("SELECT COUNT(*) FROM session_analyses").fetchone()[0]
            conn.close()
            console.print(f"  Events:   {events:,}")
            console.print(f"  Sessions: {sessions:,}")
            console.print(f"  Analyzed: {analyzed:,}")
        except Exception:
            pass


@cli.command()
def storage():
    """Show how much disk capman2 is using, broken down by component."""
    config = load_config()
    from capman.api.routes.storage import compute_storage
    r = compute_storage(config)

    console.print(f"\n[bold]capman2 storage[/bold]  —  {config.get('core', {}).get('data_dir', '~/.capman')}")
    console.print(f"  Total: [bold cyan]{r['total_human']}[/bold cyan]  ({r['total_files']:,} files)")
    if r.get("estimated_per_day_human"):
        console.print(f"  Growth: ~{r['estimated_per_day_human']}/day  (~{r['estimated_per_month_human']}/month, over {r['span_days']} days)")
    console.print("─" * 60)
    table = Table(show_header=True, header_style="dim")
    table.add_column("Component")
    table.add_column("Size", justify="right")
    table.add_column("%", justify="right")
    table.add_column("Files", justify="right")
    for c in r["components"]:
        if c["bytes"] == 0:
            continue
        table.add_row(c["name"], c["human"], f"{c['pct']}%", f"{c['files']:,}")
    console.print(table)

    db = r.get("db", {})
    if db:
        bits = []
        for k, label in [("events", "events"), ("sessions", "sessions"), ("session_analyses", "analyses"),
                         ("playbooks", "playbooks"), ("knowledge_triples", "facts"),
                         ("knowledge_gaps", "gaps"), ("screenshots", "screenshots")]:
            if db.get(k) is not None:
                bits.append(f"{db[k]:,} {label}")
        if bits:
            console.print(f"  DB: {' · '.join(bits)}")
    console.print()


@cli.command()
@click.argument("query_text")
@click.option("--top-k", default=5, help="Number of results")
def query(query_text, top_k):
    """Semantic search over captured knowledge."""
    import requests

    config = load_config()
    port = config["api"]["port"]

    try:
        resp = requests.get(
            f"http://localhost:{port}/query",
            params={"q": query_text, "top_k": top_k},
            timeout=5,
        )
        data = resp.json()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print("[dim]Is capman running? Try: capman start[/dim]")
        return

    console.print(f'\n[bold]Query:[/bold] "{query_text}"')
    console.print("─" * 60)

    results = data.get("results", [])
    if not results:
        console.print("[dim]No results found.[/dim]")
        return

    for r in results:
        rtype = r.get("type", "")
        score = r.get("score", 0)
        title = r.get("title", r.get("id", ""))
        text = r.get("text", "")[:120]

        color = "cyan" if rtype == "knowledge_node" else "blue"
        label = "KNOWLEDGE" if rtype == "knowledge_node" else "SESSION"
        console.print(f"\n[[{color}]{score:.2f}[/{color}]] [bold]{label}:[/bold] {title}")
        if text:
            console.print(f"  [dim]{text}...[/dim]")

    console.print()


@cli.command()
@click.option("--to", "dest", default=None,
              help="Destination directory (default: <data_dir>/backups)")
@click.option("--include-screenshots", is_flag=True,
              help="Include the screenshot tree — large, and the most sensitive thing capman holds")
@click.option("--archive", is_flag=True, help="Write a .tar.gz instead of a directory")
@click.option("--keep", type=int, default=0,
              help="Delete all but the newest N backups in the destination (0 = keep all)")
def backup(dest, include_screenshots, archive, keep):
    """Take a consistent snapshot of the database and knowledge vault.

    Safe to run while the daemon is capturing: the database is copied with
    VACUUM INTO, which takes a read lock and produces a compacted, internally
    consistent file rather than a torn copy of a live WAL database.
    """
    import json
    import shutil
    import sqlite3
    import tarfile
    import time as _time

    config = load_config()
    storage = config.get("storage", {})
    data_dir = get_data_dir(config)

    dest_dir = Path(dest).expanduser() if dest else data_dir / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = _time.strftime("%Y%m%d-%H%M%S")
    out = dest_dir / f"capman-backup-{stamp}"
    out.mkdir(parents=True, exist_ok=True)

    manifest: dict = {"created_at": _time.time(), "created_at_human": stamp, "contents": {}}

    # --- database ---------------------------------------------------------
    db_path = Path(str(storage.get("sqlite_path", "~/.capman/timeline.db"))).expanduser()
    if db_path.exists():
        target = out / "timeline.db"
        try:
            src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            src.execute("PRAGMA busy_timeout=10000")
            src.execute("VACUUM INTO ?", (str(target),))
            src.close()

            check = sqlite3.connect(str(target))
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
            version = check.execute("PRAGMA user_version").fetchone()[0]
            counts = {}
            for tbl in ("events", "sessions", "session_analyses", "documents",
                        "playbooks", "knowledge_triples"):
                try:
                    counts[tbl] = check.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                except sqlite3.Error:
                    pass
            check.close()

            manifest["contents"]["timeline.db"] = {
                "bytes": target.stat().st_size, "integrity_check": integrity,
                "schema_version": version, "rows": counts,
            }
            colour = "green" if integrity == "ok" else "red"
            console.print(f"  timeline.db     [{colour}]{integrity}[/{colour}]  "
                          f"({target.stat().st_size / 1e6:.1f} MB, schema v{version})")
        except Exception as e:
            console.print(f"  [red]timeline.db failed: {e}[/red]")
            manifest["contents"]["timeline.db"] = {"error": str(e)}
    else:
        console.print(f"  [yellow]no database at {db_path}[/yellow]")

    # --- knowledge vault --------------------------------------------------
    knowledge_dir = Path(str(storage.get("knowledge_dir", "~/.capman/knowledge"))).expanduser()
    if knowledge_dir.is_dir():
        shutil.copytree(knowledge_dir, out / "knowledge", dirs_exist_ok=True)
        files = sum(1 for _ in (out / "knowledge").rglob("*") if _.is_file())
        size = sum(f.stat().st_size for f in (out / "knowledge").rglob("*") if f.is_file())
        manifest["contents"]["knowledge"] = {"files": files, "bytes": size}
        console.print(f"  knowledge/      {files} files ({size / 1e6:.1f} MB)")

    # --- config, minus secrets -------------------------------------------
    user_config = data_dir / "config.toml"
    if user_config.exists():
        try:
            import tomllib
            import tomli_w
            parsed = tomllib.loads(user_config.read_text(encoding="utf-8"))
            # API keys live here in plaintext; a backup is exactly the artifact
            # that ends up on a NAS or in an off-site copy, so they never go in.
            removed = sorted(parsed.pop("secrets", {}).keys())
            (out / "config.toml").write_text(tomli_w.dumps(parsed), encoding="utf-8")
            manifest["contents"]["config.toml"] = {"secrets_stripped": removed}
            note = f" ([dim]{len(removed)} secret(s) stripped[/dim])" if removed else ""
            console.print(f"  config.toml     copied{note}")
        except Exception as e:
            console.print(f"  [yellow]config.toml skipped: {e}[/yellow]")

    # --- screenshots (opt-in) --------------------------------------------
    shots = Path(str(config.get("sensors", {}).get("screenshot", {})
                     .get("save_dir", "~/.capman/screenshots"))).expanduser()
    if include_screenshots and shots.is_dir():
        shutil.copytree(shots, out / "screenshots", dirs_exist_ok=True)
        size = sum(f.stat().st_size for f in (out / "screenshots").rglob("*") if f.is_file())
        manifest["contents"]["screenshots"] = {"bytes": size}
        console.print(f"  screenshots/    {size / 1e6:.1f} MB")
    elif shots.is_dir():
        console.print("  [dim]screenshots/    skipped (--include-screenshots to add)[/dim]")

    manifest["restore"] = (
        "Stop the daemon, then copy timeline.db and knowledge/ into the data "
        "directory (default ~/.capman), or point [core] data_dir at this folder. "
        "Secrets are not included — re-enter API keys in Settings."
    )
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    final = out
    if archive:
        tar_path = out.with_suffix(".tar.gz")
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(out, arcname=out.name)
        shutil.rmtree(out)
        final = tar_path

    total = (final.stat().st_size if final.is_file()
             else sum(f.stat().st_size for f in final.rglob("*") if f.is_file()))
    console.print(f"\n[green]Backup complete:[/green] {final}  [dim]({total / 1e6:.1f} MB)[/dim]")

    # --- prune old backups ------------------------------------------------
    if keep > 0:
        existing = sorted(
            [p for p in dest_dir.glob("capman-backup-*") if p != final],
            key=lambda p: p.name, reverse=True,
        )
        for old in existing[max(keep - 1, 0):]:
            shutil.rmtree(old) if old.is_dir() else old.unlink()
            console.print(f"  [dim]pruned {old.name}[/dim]")

    console.print("[dim]Note: up to ~2s of buffered events may not be included "
                  "if the daemon is running.[/dim]")


@cli.command()
@click.option("--rebuild-index", is_flag=True,
              help="Rebuild the ANN index from stored embeddings without re-embedding")
def reindex(rebuild_index):
    """Rebuild the search index: backfill documents, then embed what is missing.

    Replaces the old top-level reindex.py script, which hardcoded paths and
    silently used different chunk settings from the daemon.
    """
    from capman.storage.timeline import TimelineDB
    from capman.storage.backfill import backfill_documents
    from capman.storage.vectors import VectorIndex

    config = load_config()

    async def _run():
        db = TimelineDB(config["storage"]["sqlite_path"])
        await db.migrate()
        try:
            index = VectorIndex(db)
            if rebuild_index:
                n = await index.rebuild()
                console.print(f"Rebuilt vector index from [cyan]{n}[/cyan] stored embeddings")
                return

            counts = await backfill_documents(db, config["storage"].get("knowledge_dir"))
            console.print(f"Documents backfilled: [cyan]{counts}[/cyan]")
            console.print("Embedding new documents…")
            embedded = await index.index_documents()
            console.print(
                f"Embedded [cyan]{embedded}[/cyan] documents "
                f"([dim]{await index.count()} total[/dim])"
            )
        finally:
            await db.close()

    asyncio.run(_run())


@cli.group("knowledge")
def knowledge():
    """Manage the portable, derived knowledge vault."""


@knowledge.command("export")
@click.option("--to", "destination", default=None,
              help="Vault directory (default: [knowledge.vault].path)")
@click.option("--no-redact", is_flag=True,
              help="Keep derived text unchanged. Use only for a private local vault.")
def export_knowledge(destination, no_redact):
    """Rebuild the OKF-compatible vault from derived records only."""
    from capman.knowledge.vault import export_derived_vault
    config = load_config()
    vault_cfg = config.get("knowledge", {}).get("vault", {})
    target = Path(destination or vault_cfg.get("path") or config["storage"].get("derived_vault_dir", "~/.capman/vault")).expanduser()

    async def _run():
        from capman.storage.timeline import TimelineDB
        db = TimelineDB(config["storage"]["sqlite_path"])
        await db.migrate()
        try:
            return await export_derived_vault(
                db, target, redact=not no_redact,
                knowledge_dir=config["storage"].get("knowledge_dir"),
            )
        finally:
            await db.close()

    counts = asyncio.run(_run())
    console.print(f"[green]Knowledge vault exported:[/green] {target}")
    console.print("  " + " · ".join(f"{value} {name}" for name, value in counts.items()))
    if no_redact:
        console.print("[yellow]Warning:[/yellow] --no-redact may preserve identifiers in derived text.")


@knowledge.command("qmd-setup")
@click.option("--vault", "vault_path", default=None,
              help="Existing vault directory (default: [knowledge.vault].path)")
@click.option("--name", default="capman", show_default=True, help="qmd collection name")
@click.option("--apply", "apply_changes", is_flag=True,
              help="Create the qmd collection instead of printing the command")
def setup_qmd(vault_path, name, apply_changes):
    """Configure qmd as an optional local CLI/MCP reader for the derived vault."""
    import shutil
    import subprocess

    config = load_config()
    vault = Path(vault_path or config.get("knowledge", {}).get("vault", {}).get("path")
                 or config["storage"].get("derived_vault_dir", "~/.capman/vault")).expanduser()
    command = ["qmd", "collection", "add", str(vault), "--name", name]
    shown = " ".join(repr(part) if " " in part else part for part in command)
    if not apply_changes:
        console.print("[bold]qmd setup is a preview; no qmd state was changed.[/bold]")
        console.print(f"  {shown}")
        console.print(f"  qmd context add qmd://{name} 'Capman derived, evidence-linked knowledge vault'")
        console.print("  qmd embed")
        console.print("\nRun again with [cyan]--apply[/cyan] to create the collection.")
        return
    if not vault.is_dir():
        raise click.ClickException(f"Vault does not exist: {vault}. Run 'capman knowledge export' first.")
    if not shutil.which("qmd"):
        raise click.ClickException("qmd is not installed or not on PATH. Install @tobilu/qmd, then retry.")
    completed = subprocess.run(command, check=False, text=True, capture_output=True)
    if completed.returncode:
        raise click.ClickException(completed.stderr.strip() or "qmd collection setup failed")
    console.print(f"[green]qmd collection created:[/green] {name}")
    console.print(f"Next: qmd context add qmd://{name} 'Capman derived, evidence-linked knowledge vault'")
    console.print("Then: qmd embed")


if __name__ == "__main__":
    cli()
