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
    s["editor_signing_id_hints"] = {h.lower() for h in s.get("editor_signing_id_hints", [])}
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


# --- macOS: classify_info (signing-id heuristic) ----------------------------

def test_classify_info_editor_signing_id_is_user():
    a = fsmon.Attributor(_settings())
    # VS Code writes via "Code Helper" subprocesses signed as com.microsoft.VSCode
    info = {"pid": 901, "comm": "Code Helper (Renderer)", "exe": "/Applications/Visual Studio Code.app/Contents/Frameworks/Code Helper (Renderer).app/Contents/MacOS/Code Helper (Renderer)",
            "cmdline": "", "tty": "", "ppid": 900, "signing_id": "com.microsoft.VSCode", "chain": []}
    verdict, actor = a.classify_info(info)
    assert verdict == "user"
    assert actor["signing_id"] == "com.microsoft.vscode"
    assert a.is_open_recorder(actor) is True


def test_classify_info_unknown_signed_app_no_tty_is_unknown():
    a = fsmon.Attributor(_settings())
    info = {"pid": 902, "comm": "SomeAgent", "exe": "/Applications/SomeAgent.app/Contents/MacOS/SomeAgent",
            "cmdline": "", "tty": "", "ppid": 1, "signing_id": "com.example.someagent", "chain": []}
    verdict, _ = a.classify_info(info)
    assert verdict == "unknown"


def test_classify_info_terminal_child_with_tty_is_likely_user():
    a = fsmon.Attributor(_settings())
    info = {"pid": 903, "comm": "mytool", "exe": "/usr/local/bin/mytool", "cmdline": "mytool x",
            "tty": "ttys003", "ppid": 800, "signing_id": "", "chain": ["zsh", "login"]}
    verdict, _ = a.classify_info(info)
    assert verdict == "likely_user"


def test_classify_info_capman_in_chain_is_machine():
    a = fsmon.Attributor(_settings())
    info = {"pid": 904, "comm": "git", "exe": "/usr/bin/git", "cmdline": "git gc",
            "tty": "ttys004", "ppid": 700, "signing_id": "", "chain": ["python3", "capman"]}
    verdict, _ = a.classify_info(info)
    assert verdict == "machine"


# --- macOS: eslogger JSON extraction ----------------------------------------

def test_es_extract_open():
    msg = {"event": {"open": {"file": {"path": "/Users/u/code/proj/main.py"}}},
           "process": {"executable": {"path": "/usr/bin/cat"}, "ppid": 501,
                       "signing_id": "com.apple.cat", "audit_token": {"pid": 5050},
                       "tty": {"path": "/dev/ttys003"}}}
    kind, src, dst, info = fsmon._es_extract(msg)
    assert kind == "file_open"
    assert src == "/Users/u/code/proj/main.py"
    assert info["pid"] == 5050
    assert info["comm"] == "cat"
    assert info["exe"] == "/usr/bin/cat"
    assert info["signing_id"] == "com.apple.cat"
    assert info["tty"] == "/dev/ttys003"


def test_es_extract_close_modified_is_save():
    msg = {"event": {"close": {"modified": True, "target": {"path": "/Users/u/code/proj/x.py"}}},
           "process": {"executable": {"path": "/Applications/Visual Studio Code.app/Contents/MacOS/Electron"},
                       "signing_id": "com.microsoft.VSCode", "audit_token": {"pid": 7}}}
    kind, src, dst, info = fsmon._es_extract(msg)
    assert kind == "file_save"
    assert src == "/Users/u/code/proj/x.py"
    assert info["signing_id"] == "com.microsoft.VSCode"


def test_es_extract_close_not_modified_skipped():
    msg = {"event": {"close": {"modified": False, "target": {"path": "/Users/u/x"}}}, "process": {}}
    kind, *_ = fsmon._es_extract(msg)
    assert kind is None


def test_es_extract_rename():
    msg = {"event": {"rename": {"source": {"path": "/Users/u/code/a.py"},
                                "destination": {"existing_file": {"path": "/Users/u/code/b.py"}}}},
           "process": {"executable": {"path": "/bin/mv"}, "audit_token": {"pid": 9}}}
    kind, src, dst, info = fsmon._es_extract(msg)
    assert kind == "file_rename"
    assert src == "/Users/u/code/a.py"
    assert dst == "/Users/u/code/b.py"
    assert info["comm"] == "mv"


def test_es_extract_rename_new_path_form():
    msg = {"event": {"rename": {"source": {"path": "/Users/u/code/a.py"},
                                "destination": {"new_path": {"dir": {"path": "/Users/u/code"}, "filename": "b.py"}}}},
           "process": {"executable": {"path": "/bin/mv"}, "audit_token": [0, 0, 0, 0, 0, 12, 0, 0]}}
    kind, src, dst, info = fsmon._es_extract(msg)
    assert kind == "file_rename"
    assert dst == "/Users/u/code/b.py"
    assert info["pid"] == 12


def test_es_extract_unlink():
    msg = {"event": {"unlink": {"target": {"path": "/Users/u/code/old.py"}}},
           "process": {"executable": {"path": "/bin/rm"}, "audit_token": {"pid": 3}}}
    kind, src, dst, info = fsmon._es_extract(msg)
    assert kind == "file_delete"
    assert src == "/Users/u/code/old.py"


def test_es_extract_unknown_event_returns_none():
    msg = {"event": {"exec": {"target": {}}}, "process": {}}
    kind, *_ = fsmon._es_extract(msg)
    assert kind is None


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
