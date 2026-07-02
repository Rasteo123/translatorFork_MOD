from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any

CLIENTS_WITH_MCP_SERVERS = {"claude", "generic", "antigravity"}
SAFE_INSTALL_MODES = {"auto", "print", "write"}
SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _server_command(*, state_dir: str | Path | None = None) -> dict[str, Any]:
    args = ["-m", "gemini_translator.mcp"]
    if state_dir:
        args.extend(["--state-dir", str(Path(state_dir).expanduser().resolve())])
    args.append("server")
    return {
        "command": sys.executable,
        "args": args,
        "env": {"PYTHONPATH": str(repo_root())},
    }


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _validate_server_name(server_name: str) -> str:
    value = str(server_name or "translatorFork")
    if not SERVER_NAME_PATTERN.fullmatch(value):
        raise ValueError("server_name must contain only letters, digits, underscores, or hyphens")
    return value


def build_config_snippet(
    client: str,
    *,
    server_name: str = "translatorFork",
    state_dir: str | Path | None = None,
) -> dict[str, Any]:
    normalized_client = str(client).lower()
    server_name = _validate_server_name(server_name)
    command = _server_command(state_dir=state_dir)

    if normalized_client in CLIENTS_WITH_MCP_SERVERS:
        return {"mcpServers": {server_name: command}}

    if normalized_client == "codex":
        args = ", ".join(json.dumps(arg) for arg in command["args"])
        env = ", ".join(f"{key} = {json.dumps(value)}" for key, value in command["env"].items())
        return {
            "text": (
                f"[mcp_servers.{server_name}]\n"
                f"command = {json.dumps(command['command'])}\n"
                f"args = [{args}]\n"
                f"env = {{ {env} }}\n"
            )
        }

    raise ValueError(f"Unsupported client: {client}")


def _snippet_text(client: str, *, server_name: str, state_dir: str | Path | None = None) -> str:
    snippet = build_config_snippet(client, server_name=server_name, state_dir=state_dir)
    if "text" in snippet:
        return snippet["text"]
    return json.dumps(snippet, ensure_ascii=False, indent=2, sort_keys=True)


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup_path = path.with_name(f"{path.name}.bak-{_timestamp()}")
    shutil.copy2(path, backup_path)
    return backup_path


def install_claude_config(
    path: str | Path,
    *,
    server_name: str,
    mode: str,
    state_dir: str | Path | None = None,
) -> dict[str, Any]:
    config_path = Path(path).expanduser()
    snippet = _snippet_text("claude", server_name=server_name, state_dir=state_dir)

    if mode == "print":
        return {
            "ok": True,
            "client": "claude",
            "config_path": str(config_path),
            "written": False,
            "backup_path": None,
            "snippet": snippet,
        }

    if mode != "write":
        raise ValueError(f"Unsupported install mode for Claude config: {mode}")

    payload: dict[str, Any] = {}
    if config_path.exists():
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Claude config must be a JSON object: {config_path}")

    backup_path = _backup(config_path)
    mcp_servers = payload.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}
    mcp_servers[server_name] = _server_command(state_dir=state_dir)
    payload["mcpServers"] = mcp_servers

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return {
        "ok": True,
        "client": "claude",
        "config_path": str(config_path),
        "written": True,
        "backup_path": str(backup_path) if backup_path else None,
        "snippet": snippet,
    }


def handle_install_tool(tool_name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    arguments = dict(arguments or {})
    client = str(arguments.get("client") or "generic").lower()
    requested_server_name = arguments.get("server_name") or "translatorFork"
    try:
        server_name = _validate_server_name(str(requested_server_name))
    except ValueError as exc:
        return {
            "ok": False,
            "client": client,
            "server_name": str(requested_server_name),
            "written": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
    state_dir = arguments.get("state_dir")

    if tool_name == "print_mcp_config":
        return {
            "ok": True,
            "client": client,
            "server_name": server_name,
            "written": False,
            "snippet": _snippet_text(client, server_name=server_name, state_dir=state_dir),
        }

    if tool_name != "install_mcp_client":
        return {"ok": False, "error": f"Unsupported tool: {tool_name}", "written": False}

    mode = str(arguments.get("mode") or "auto").lower()
    if mode not in SAFE_INSTALL_MODES:
        return {
            "ok": False,
            "client": client,
            "server_name": server_name,
            "written": False,
            "error": f"Unsupported install mode: {mode}",
            "supported_modes": sorted(SAFE_INSTALL_MODES),
        }

    config_path = arguments.get("config_path")
    if client == "claude" and config_path:
        safe_mode = "print" if mode == "auto" else mode
        try:
            return install_claude_config(config_path, server_name=server_name, mode=safe_mode, state_dir=state_dir)
        except (OSError, ValueError) as exc:
            return {
                "ok": False,
                "client": client,
                "server_name": server_name,
                "written": False,
                "backup_path": None,
                "config_path": str(Path(config_path).expanduser()),
                "error": str(exc),
                "error_type": type(exc).__name__,
            }

    return {
        "ok": True,
        "client": client,
        "server_name": server_name,
        "written": False,
        "warning": "Safe installer writes are currently only available for Claude with config_path; printing snippet.",
        "snippet": _snippet_text(client, server_name=server_name, state_dir=state_dir),
    }
