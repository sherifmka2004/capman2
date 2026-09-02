"""Guard against the API package failing to import.

A broken import in capman.api.server is swallowed by _start_api_server, so the
daemon keeps running sensors while the API, web UI, /query and chat silently
never start. That failure mode shipped undetected once already (a route module
was imported that had never existed); these tests make it loud.
"""
import pytest


def test_routes_package_imports():
    """Every router module named by the server must actually import."""
    from capman.api import server  # noqa: F401


def test_create_app_builds():
    from capman.api.server import create_app

    app = create_app({}, None)
    assert app is not None


def test_expected_routes_registered():
    from capman.api.server import create_app

    app = create_app({}, None)
    paths = {r.path for r in app.routes}
    for expected in ("/health", "/", "/chat", "/events", "/sessions", "/query", "/storage"):
        assert expected in paths, f"route {expected} missing from app"


def test_desktop_shell_constrains_scrollable_views_to_viewport():
    from capman.api.chat_ui import CHAT_HTML

    assert ".app-shell { height: 100vh; height: 100dvh; min-height: 0;" in CHAT_HTML
    assert ".workspace { min-width: 0; min-height: 0;" in CHAT_HTML
    assert ".home-content { flex: 1; min-height: 0; overflow-y: auto;" in CHAT_HTML
