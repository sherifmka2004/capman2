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


if __name__ == "__main__":
    cli()
