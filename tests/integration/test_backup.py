"""`capman backup` — consistency, secret hygiene, and retention.

A backup is precisely the artifact that leaves the machine (NAS, off-site
copy), so what it contains matters as much as whether it restores.
"""
import json
import sqlite3
import tarfile
import time

import pytest
from click.testing import CliRunner

from capman.events import Event, EventType
from capman.storage.timeline import TimelineDB


@pytest.fixture
async def env(tmp_path, monkeypatch):
    """A populated data dir plus a config pointing at it."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "timeline.db"

    db = TimelineDB(str(db_path))
    await db.migrate()
    now = time.time()
    for i in range(50):
        e = Event(type=EventType.SHELL_COMMAND, app="Terminal",
                  payload={"command": f"echo {i}"})
        e.ts = now - i
        await db.queue_event(e, "s1")
    await db.upsert_document("node:1", "node", "Nginx buffering notes",
                             ts=now, title="Nginx")
    await db.close()

    (data_dir / "knowledge").mkdir()
    (data_dir / "knowledge" / "concept.md").write_text("# A concept\n")
    (data_dir / "screenshots").mkdir()
    (data_dir / "screenshots" / "shot.png").write_bytes(b"\x89PNG-not-really")
    (data_dir / "config.toml").write_text(
        '[core]\ndata_dir = "%s"\n\n[secrets]\nanthropic_api_key = "sk-ant-SUPERSECRET"\n'
        'openrouter_api_key = "sk-or-ALSOSECRET"\n' % data_dir
    )

    config = {
        "core": {"data_dir": str(data_dir)},
        "storage": {"sqlite_path": str(db_path), "knowledge_dir": str(data_dir / "knowledge")},
        "sensors": {"screenshot": {"save_dir": str(data_dir / "screenshots")}},
        "api": {"port": 7331},
    }
    monkeypatch.setattr("capman.main.load_config", lambda *a, **k: config)
    monkeypatch.setattr("capman.main.get_data_dir", lambda c: data_dir)
    return {"data_dir": data_dir, "dest": tmp_path / "backups"}


def _run(args):
    from capman.main import cli
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output + str(result.exception)
    return result


def _latest(dest):
    return sorted(dest.glob("capman-backup-*"))[-1]


async def test_backup_produces_a_consistent_database(env):
    _run(["backup", "--to", str(env["dest"])])
    out = _latest(env["dest"])

    db = sqlite3.connect(out / "timeline.db")
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 50
    assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    db.close()


async def test_backup_includes_the_knowledge_vault(env):
    _run(["backup", "--to", str(env["dest"])])
    assert (_latest(env["dest"]) / "knowledge" / "concept.md").exists()


async def test_backup_never_contains_api_keys(env):
    """The whole point of a backup is that it goes somewhere else."""
    _run(["backup", "--to", str(env["dest"])])
    out = _latest(env["dest"])

    blob = "".join(p.read_text(errors="ignore")
                   for p in out.rglob("*") if p.is_file() and p.suffix in (".toml", ".json"))
    assert "SUPERSECRET" not in blob
    assert "ALSOSECRET" not in blob

    cfg = (out / "config.toml").read_text()
    assert "[secrets]" not in cfg
    assert "data_dir" in cfg, "non-secret config should survive"

    manifest = json.loads((out / "MANIFEST.json").read_text())
    assert set(manifest["contents"]["config.toml"]["secrets_stripped"]) == {
        "anthropic_api_key", "openrouter_api_key"}


async def test_screenshots_are_opt_in(env):
    """Screenshots are the most sensitive and largest artifact — never by default."""
    _run(["backup", "--to", str(env["dest"])])
    assert not (_latest(env["dest"]) / "screenshots").exists()


async def test_screenshots_included_on_request(env):
    _run(["backup", "--to", str(env["dest"]), "--include-screenshots"])
    assert (_latest(env["dest"]) / "screenshots" / "shot.png").exists()


async def test_manifest_records_integrity_and_row_counts(env):
    _run(["backup", "--to", str(env["dest"])])
    manifest = json.loads((_latest(env["dest"]) / "MANIFEST.json").read_text())
    db_entry = manifest["contents"]["timeline.db"]
    assert db_entry["integrity_check"] == "ok"
    assert db_entry["rows"]["events"] == 50
    assert db_entry["schema_version"] > 0
    assert "restore" in manifest


async def test_archive_mode_produces_one_tarball(env):
    _run(["backup", "--to", str(env["dest"]), "--archive"])
    tarballs = list(env["dest"].glob("*.tar.gz"))
    assert len(tarballs) == 1
    assert not list(env["dest"].glob("capman-backup-*/")), "directory should be removed"
    with tarfile.open(tarballs[0]) as tf:
        names = tf.getnames()
    assert any(n.endswith("timeline.db") for n in names)
    assert any(n.endswith("MANIFEST.json") for n in names)


async def test_keep_prunes_older_backups(env):
    for _ in range(3):
        _run(["backup", "--to", str(env["dest"])])
        time.sleep(1.05)  # backup names are second-resolution
    assert len(list(env["dest"].glob("capman-backup-*"))) == 3

    _run(["backup", "--to", str(env["dest"]), "--keep", "2"])
    remaining = sorted(env["dest"].glob("capman-backup-*"))
    assert len(remaining) == 2, f"expected 2 kept, got {[p.name for p in remaining]}"


async def test_backup_runs_against_a_live_database(env):
    """Must not require stopping the daemon."""
    db = TimelineDB(str(env["data_dir"] / "timeline.db"))
    await db.migrate()
    try:
        e = Event(type=EventType.URL_VISIT, app="Chrome", payload={"url": "https://x.dev"})
        await db.queue_event(e, "s2")
        await db.flush()

        _run(["backup", "--to", str(env["dest"])])  # connection still open

        out = _latest(env["dest"])
        copy = sqlite3.connect(out / "timeline.db")
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert copy.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 51
        copy.close()
    finally:
        await db.close()
