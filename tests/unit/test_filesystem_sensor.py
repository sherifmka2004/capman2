"""Unit tests for the user-driven FilesystemSensor: exclusions, attribution, diffs."""
import asyncio
import time

import pytest

from capman.sensors.filesystem import (
    DEFAULT_EXCLUDE, DEFAULT_INTERACTIVE_APPS, DEFAULT_INTERACTIVE_CLI,
    DEFAULT_MACHINE_PROCS, FilesystemSensor, _command_head, _norm_proc,
)
from capman.sensors import activity_context as ac


def _sensor(tmp_path):
    s = FilesystemSensor(config={}, queue=asyncio.Queue())
    s._allowed_ext = {".py", ".md", ".txt"}
    s._exclude = list(DEFAULT_EXCLUDE)
    s._snapshot_dir = tmp_path / "snap"
    s._snapshot_dir.mkdir(parents=True, exist_ok=True)
    s._capture_diffs = True
    s._diff_max_bytes = 1 << 20
    s._git_root_cache = {}
    s._capman_dir = tmp_path / "_capman_data"
    s._user_only = True
    s._keep_unknown = False
    s._fg_grace = 4
    s._shell_grace = 8
    s._interactive_apps = {a.lower() for a in DEFAULT_INTERACTIVE_APPS}
    s._interactive_cli = {_norm_proc(c) for c in DEFAULT_INTERACTIVE_CLI}
    s._machine_procs = {_norm_proc(c) for c in DEFAULT_MACHINE_PROCS}
    return s


# --- command parsing --------------------------------------------------------

def test_norm_proc_strips_path_and_suffix():
    assert _norm_proc("/usr/bin/python3") == "python3"
    assert _norm_proc("node.exe") == "node"
    assert _norm_proc("VIM") == "vim"
    assert _norm_proc("") == ""


def test_command_head_skips_env_and_sudo():
    assert _command_head("sudo VAR=1 npm install") == "npm"
    assert _command_head("VAR=x ./run.sh") == "run.sh"
    assert _command_head("vim ~/code/x.py") == "vim"
    assert _command_head("nohup time cargo build") == "cargo"
    assert _command_head("   ") == ""


# --- exclusions -------------------------------------------------------------

@pytest.mark.parametrize("path,excluded", [
    ("/home/u/code/proj/src/app.py", False),
    ("/home/u/code/proj/node_modules/lib/index.js", True),
    ("/home/u/code/proj/.git/objects/ab/cdef", True),
    ("/home/u/code/proj/__pycache__/x.pyc", True),
    ("/home/u/code/proj/target/debug/bin", True),
    ("/home/u/code/proj/.mypy_cache/x", True),
    ("/home/u/code/proj/src/.app.py.swp", True),
    ("/home/u/code/proj/notes.md", False),
    ("/home/u/code/proj/build/out.js", True),
])
def test_excluded(tmp_path, path, excluded):
    s = _sensor(tmp_path)
    assert s._excluded(path) is excluded


def test_excludes_capman_data_dir(tmp_path):
    s = _sensor(tmp_path)
    inside = str(s._capman_dir / "timeline.db")
    assert s._excluded(inside) is True


# --- attribution ------------------------------------------------------------

def _clear_context():
    ac._recent_commands.clear()
    ac.set_foreground("", "")


def test_attribution_shell_interactive_cli_is_user(tmp_path):
    _clear_context()
    s = _sensor(tmp_path)
    ac.record_shell_command("vim main.py", cwd=str(tmp_path), pid=4321)
    verdict, actor, via = s._attribute(str(tmp_path / "main.py"))
    assert verdict == "user"
    assert actor["comm"] == "vim"
    assert via and via["command"] == "vim main.py"


def test_attribution_shell_build_tool_is_machine(tmp_path):
    _clear_context()
    s = _sensor(tmp_path)
    ac.record_shell_command("npm install", cwd=str(tmp_path), pid=999)
    verdict, actor, via = s._attribute(str(tmp_path / "node_modules" / "x" / "index.js"))
    assert verdict == "machine"


def test_attribution_unknown_shell_command_is_likely_user(tmp_path):
    _clear_context()
    s = _sensor(tmp_path)
    ac.record_shell_command("./my_custom_tool --flag", cwd=str(tmp_path), pid=111)
    verdict, _, _ = s._attribute(str(tmp_path / "out.txt"))
    assert verdict == "likely_user"


def test_attribution_foreground_editor_is_user(tmp_path):
    _clear_context()
    s = _sensor(tmp_path)
    ac.set_foreground("Code", "main.py — proj")
    verdict, actor, via = s._attribute("/home/u/code/proj/main.py")
    assert verdict == "user"
    assert actor == {"app": "Code"}
    assert via is None


def test_attribution_no_signal_is_unknown(tmp_path):
    _clear_context()
    s = _sensor(tmp_path)
    ac.set_foreground("Slack", "general")
    verdict, _, _ = s._attribute("/home/u/code/proj/main.py")
    assert verdict == "unknown"


def test_pass_filter_drops_machine_and_unknown(tmp_path):
    s = _sensor(tmp_path)
    assert s._pass_filter("user") is True
    assert s._pass_filter("likely_user") is True
    assert s._pass_filter("machine") is False
    assert s._pass_filter("unknown") is False
    s._keep_unknown = True
    assert s._pass_filter("unknown") is True
    s._user_only = False
    assert s._pass_filter("machine") is True


def test_attribution_old_shell_command_ignored(tmp_path):
    _clear_context()
    s = _sensor(tmp_path)
    # command 30s ago, grace is 8s → must not match
    ac.record_shell_command("vim main.py", cwd=str(tmp_path), pid=1, ts=time.time() - 30)
    ac.set_foreground("Firefox", "")
    verdict, _, _ = s._attribute(str(tmp_path / "main.py"))
    assert verdict == "unknown"


# --- diffing ----------------------------------------------------------------

def test_count_diff():
    patch = (
        "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,3 @@\n"
        " import os\n-x = 1\n+x = 2\n+y = 3\n"
    )
    added, removed = FilesystemSensor._count_diff(patch)
    assert added == 2
    assert removed == 1


def test_cap_diff_truncates():
    big = "+" * 20000
    out = FilesystemSensor._cap_diff(big)
    assert len(out) < len(big)
    assert "truncated" in out


@pytest.mark.asyncio
async def test_compute_diff_snapshot_roundtrip(tmp_path):
    s = _sensor(tmp_path)
    f = tmp_path / "hello.py"
    f.write_text("a = 1\nb = 2\n")
    # first sighting → no diff, snapshot stored
    first = await s._compute_diff(str(f), f.stat().st_size)
    assert first is None
    # change it
    f.write_text("a = 1\nb = 22\nc = 3\n")
    second = await s._compute_diff(str(f), f.stat().st_size)
    assert second is not None
    assert second["added"] == 2  # b=22 + c=3
    assert second["removed"] == 1
    assert "c = 3" in second["text"]
    # no change → None
    third = await s._compute_diff(str(f), f.stat().st_size)
    assert third is None


@pytest.mark.asyncio
async def test_compute_diff_skips_disallowed_extension(tmp_path):
    s = _sensor(tmp_path)
    f = tmp_path / "data.bin"
    f.write_text("whatever")
    assert await s._compute_diff(str(f), f.stat().st_size) is None


@pytest.mark.asyncio
async def test_compute_diff_skips_binary(tmp_path):
    s = _sensor(tmp_path)
    f = tmp_path / "x.txt"  # allowed ext, but binary content
    f.write_bytes(b"\x00\x01\x02\xff\xfe")
    assert await s._compute_diff(str(f), f.stat().st_size) is None


@pytest.mark.asyncio
async def test_compute_diff_skips_too_large(tmp_path):
    s = _sensor(tmp_path)
    s._diff_max_bytes = 10
    f = tmp_path / "big.py"
    f.write_text("x = 1\n" * 100)
    assert await s._compute_diff(str(f), f.stat().st_size) is None
