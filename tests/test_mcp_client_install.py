import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from gemini_translator.mcp.client_install import (
    build_config_snippet,
    handle_install_tool,
    install_claude_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_build_claude_snippet_uses_module_entrypoint():
    snippet = build_config_snippet("claude", server_name="translatorFork")

    assert snippet["mcpServers"]["translatorFork"]["args"][-1:] == ["server"]
    assert "-m" in snippet["mcpServers"]["translatorFork"]["args"]
    assert "gemini_translator.mcp" in snippet["mcpServers"]["translatorFork"]["args"]


def test_build_codex_snippet_uses_toml_shape():
    snippet = build_config_snippet("codex", server_name="translatorFork")

    assert "[mcp_servers.translatorFork]" in snippet["text"]
    assert "gemini_translator.mcp" in snippet["text"]


@pytest.mark.parametrize("client", ["codex", "claude", "generic", "antigravity"])
def test_build_config_snippet_rejects_unsafe_server_name(client):
    with pytest.raises(ValueError, match="server_name"):
        build_config_snippet(client, server_name="bad]\n[mcp_servers.evil]")


@pytest.mark.parametrize("client", ["codex", "generic", "antigravity"])
def test_install_tool_rejects_unsafe_server_name(client):
    result = handle_install_tool(
        "print_mcp_config",
        {"client": client, "server_name": "bad]\n[mcp_servers.evil]"},
    )

    assert result["ok"] is False
    assert result["written"] is False
    assert "server_name" in result["error"]


def test_build_generic_snippet_launches_from_non_repo_cwd(tmp_path):
    state_dir = tmp_path / "state"
    snippet = build_config_snippet("generic", server_name="translatorFork", state_dir=state_dir)
    command = snippet["mcpServers"]["translatorFork"]

    assert command["env"]["PYTHONPATH"] == str(REPO_ROOT)
    assert command["args"][-3:] == ["--state-dir", str(state_dir), "server"]

    env = os.environ.copy()
    env.update(command["env"])
    safe_args = command["args"][:-1] + ["daemon", "status"]
    result = subprocess.run(
        [command["command"], *safe_args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 1
    assert "ModuleNotFoundError" not in result.stderr
    assert json.loads(result.stdout)["ok"] is False


def test_build_codex_snippet_includes_env_and_state_dir():
    snippet = build_config_snippet("codex", server_name="translatorFork", state_dir="/tmp/state")
    text = snippet["text"]

    assert "env" in text
    assert "PYTHONPATH" in text
    assert str(REPO_ROOT) in text
    assert "--state-dir" in text
    assert "/tmp/state" in text


def test_install_claude_config_creates_backup_and_preserves_existing(tmp_path):
    path = tmp_path / "claude.json"
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "old"}}}), encoding="utf-8")

    result = install_claude_config(path, server_name="translatorFork", mode="write")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert result["written"] is True
    assert result["backup_path"]
    assert "other" in payload["mcpServers"]
    assert "translatorFork" in payload["mcpServers"]


def test_install_tool_print_mode_does_not_write(tmp_path):
    path = tmp_path / "claude.json"
    result = handle_install_tool(
        "install_mcp_client",
        {"client": "claude", "mode": "print", "config_path": str(path), "server_name": "translatorFork"},
    )

    assert result["written"] is False
    assert path.exists() is False
    assert result["snippet"]


def test_cli_config_passes_global_state_dir(tmp_path):
    state_dir = tmp_path / "state"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gemini_translator.mcp",
            "--state-dir",
            str(state_dir),
            "config",
            "--client",
            "codex",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert "--state-dir" in payload["snippet"]
    assert str(state_dir) in payload["snippet"]


def test_cli_install_bad_claude_json_returns_json_error(tmp_path):
    path = tmp_path / "claude.json"
    path.write_text("{bad json", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gemini_translator.mcp",
            "install",
            "--client",
            "claude",
            "--mode",
            "write",
            "--config-path",
            str(path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["written"] is False
    assert "error" in payload
