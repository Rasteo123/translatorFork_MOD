"""Cluster-51 dedup: client_install.repo_root() must be paths.repo_root(), not a local copy.

Characterization + routing tests for the repo_root() duplication between
gemini_translator/mcp/paths.py (canonical) and gemini_translator/mcp/client_install.py
(local copy, byte-identical body).
"""
from __future__ import annotations

from pathlib import Path

from gemini_translator.mcp import client_install
from gemini_translator.mcp.paths import repo_root


def test_paths_repo_root_points_to_checkout():
    """Characterization: canonical repo_root() resolves to the actual repo root."""
    root = repo_root()

    assert (root / "gemini_translator").is_dir()
    assert (root / "README.md").is_file()


def test_client_install_server_command_reads_module_level_repo_root(monkeypatch):
    """_server_command() must consult the module-level name `repo_root`, not an inlined Path() call.

    This guards against a future regression that hardcodes Path(__file__)... again
    inside _server_command() instead of calling the (imported) repo_root symbol.
    Because client_install uses `from .paths import repo_root`, patching this name
    has to target client_install's own namespace (that is where the call is looked
    up) — patching gemini_translator.mcp.paths.repo_root would not propagate here,
    which is exactly why the identity test below is the real duplication check.
    """
    sentinel = Path("/sentinel/repo/root")
    monkeypatch.setattr(client_install, "repo_root", lambda: sentinel)

    command = client_install._server_command()

    assert command["env"]["PYTHONPATH"] == str(sentinel)


def test_client_install_has_no_local_repo_root_definition():
    """Routing/anti-duplication check: client_install.repo_root must be the exact
    canonical function object from paths.py (imported), not a separately defined
    copy with identical body.

    Before the fix, client_install.py defines its own repo_root() -> a distinct
    function object -> this assertion fails (RED). After the fix, client_install
    does `from .paths import repo_root`, binding the very same object -> passes.
    """
    assert client_install.repo_root is repo_root
