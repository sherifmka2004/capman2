"""Unit tests for the capman-fsmon deep-monitor helper: process attribution + path filter.

`/proc` reads are monkeypatched so the classifier can be exercised on synthetic
process trees.
"""
import importlib.util
import pathlib

import pytest

_FSMON_PATH = pathlib.Path(__file__).resolve().parents[2] / "tools" / "capman-fsmon" / "fsmon.py"
_spec = importlib.util.spec_from_file_location("capman_fsmon", _FSMON_PATH)
fsmon = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fsmon)


def _settings():
    s = dict(fsmon._DEFAULTS)
    for k in ("open_recorders", "interactive_apps", "interactive_cli", "machine_procs"):
        s[k] = {fsmon._norm(x) for x in s[k]}
    return s


def _patch_procs(monkeypatch, table):
    """table: {pid: {"comm":..., "exe":..., "cmd":..., "ppid":..., "tty":...}}"""
    monkeypatch.setattr(fsmon, "_proc_comm", lambda pid: table.get(pid, {}).get("comm", ""))
    monkeypatch.setattr(fsmon, "_proc_exe", lambda pid: table.get(pid, {}).get("exe", ""))
    monkeypatch.setattr(fsmon, "_proc_cmdline", lambda pid: table.get(pid, {}).get("cmd", ""))

    def _stat(pid):
        e = table.get(pid)
        if not e:
            return None
        return (e.get("ppid", 1), e.get("tty", 0), e.get("session", 0))
    monkeypatch.setattr(fsmon, "_proc_stat", _stat)


# --- _norm ------------------------------------------------------------------

def test_norm():
    assert fsmon._norm("/usr/bin/python3") == "python3"
    assert fsmon._norm("Code.exe") == "code"
    assert fsmon._norm("") == ""


# --- Attributor -------------------------------------------------------------

def test_classify_editor_with_tty_is_user(monkeypatch):
    _patch_procs(monkeypatch, {
        100: {"comm": "vim", "exe": "/usr/bin/vim", "ppid": 50, "tty": 34816},
        50:  {"comm": "bash", "ppid": 1, "tty": 34816},
    })
    a = fsmon.Attributor(_settings())
    verdict, actor = a.classify(100)
    assert verdict == "user"
    assert actor["comm"] == "vim"
    assert actor["pid"] == 100
    assert actor.get("tty")


def test_classify_gui_editor_no_tty_is_user(monkeypatch):
    _patch_procs(monkeypatch, {
        200: {"comm": "code", "exe": "/usr/share/code/code", "ppid": 1, "tty": 0},
    })
    a = fsmon.Attributor(_settings())
    verdict, actor = a.classify(200)
    assert verdict == "user"


def test_classify_build_tool_is_machine(monkeypatch):
    _patch_procs(monkeypatch, {
        300: {"comm": "node", "exe": "/usr/bin/node", "ppid": 50, "tty": 34816},
        50:  {"comm": "bash", "ppid": 1, "tty": 34816},
    })
    a = fsmon.Attributor(_settings())
    verdict, _ = a.classify(300)
    assert verdict == "machine"


def test_classify_language_server_is_machine(monkeypatch):
    _patch_procs(monkeypatch, {
        301: {"comm": "pyright", "ppid": 200, "tty": 0},
        200: {"comm": "code", "ppid": 1, "tty": 0},
    })
    a = fsmon.Attributor(_settings())
    verdict, _ = a.classify(301)
    assert verdict == "machine"


def test_classify_descendant_of_capman_is_machine(monkeypatch):
    _patch_procs(monkeypatch, {
        400: {"comm": "git", "ppid": 401, "tty": 0},
        401: {"comm": "python3", "ppid": 402, "tty": 0},
        402: {"comm": "capman", "ppid": 1, "tty": 0},
    })
    a = fsmon.Attributor(_settings())
    verdict, _ = a.classify(400)
    assert verdict == "machine"


def test_classify_our_own_pid_is_machine(monkeypatch):
    monkeypatch.setattr(fsmon, "_OUR_PIDS", {12345})
    a = fsmon.Attributor(_settings())
    verdict, actor = a.classify(12345)
    assert verdict == "machine"
    assert actor["comm"] == "fsmon"


def test_classify_unknown_tty_binary_from_shell_is_likely_user(monkeypatch):
    _patch_procs(monkeypatch, {
        500: {"comm": "mytool", "ppid": 50, "tty": 34816},
        50:  {"comm": "zsh", "ppid": 1, "tty": 34816},
    })
    a = fsmon.Attributor(_settings())
    verdict, _ = a.classify(500)
    assert verdict == "likely_user"


def test_classify_daemon_no_tty_unknown(monkeypatch):
    _patch_procs(monkeypatch, {
        600: {"comm": "weirddaemon", "ppid": 1, "tty": 0},
    })
    a = fsmon.Attributor(_settings())
    verdict, _ = a.classify(600)
    assert verdict == "unknown"


def test_classify_python_script_no_tty_is_machine(monkeypatch):
    _patch_procs(monkeypatch, {
        700: {"comm": "python3", "cmd": "python3 build.py", "ppid": 1, "tty": 0},
    })
    a = fsmon.Attributor(_settings())
    verdict, _ = a.classify(700)
    assert verdict == "machine"


# --- PathFilter -------------------------------------------------------------

def test_path_filter(tmp_path):
    root = tmp_path / "code" / "proj"
    root.mkdir(parents=True)
    data_dir = tmp_path / ".capman"
    pf = fsmon.PathFilter([str(tmp_path / "code")], fsmon._DEFAULTS["exclude"], str(data_dir))

    assert pf.wanted(str(root / "main.py")) is True
    assert pf.wanted(str(root / "node_modules" / "x.js")) is False
    assert pf.wanted(str(root / ".git" / "config")) is False
    assert pf.wanted(str(root / "x.pyc")) is False
    assert pf.wanted(str(tmp_path / "elsewhere" / "y.py")) is False     # outside roots
    assert pf.wanted(str(data_dir / "timeline.db")) is False            # capman data
