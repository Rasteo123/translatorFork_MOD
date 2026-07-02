from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .client_install import handle_install_tool
from .client import DaemonClientError, load_client
from .daemon import McpDaemon
from .paths import default_state_dir

CLIENT_CHOICES = ("codex", "claude", "antigravity", "generic")
INSTALL_MODE_CHOICES = ("auto", "print", "write")


def _print(payload) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _print_result(payload) -> int:
    _print(payload)
    return 1 if isinstance(payload, dict) and payload.get("ok") is False else 0


def _state_dir(value) -> Path:
    if value is None:
        return default_state_dir()
    return Path(value).expanduser().resolve()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="translator-mcp")
    parser.add_argument("--state-dir", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    daemon_parser = subparsers.add_parser("daemon")
    daemon_subparsers = daemon_parser.add_subparsers(dest="daemon_command", required=True)
    daemon_subparsers.add_parser("serve")
    daemon_subparsers.add_parser("status")
    daemon_subparsers.add_parser("stop")

    subparsers.add_parser("server")

    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--client", choices=CLIENT_CHOICES, default="generic")
    install_parser.add_argument("--mode", choices=INSTALL_MODE_CHOICES, default="auto")
    install_parser.add_argument("--config-path", default=None)
    install_parser.add_argument("--server-name", default="translatorFork")

    config_parser = subparsers.add_parser("config")
    config_parser.add_argument("--client", choices=CLIENT_CHOICES, default="generic")
    config_parser.add_argument("--server-name", default="translatorFork")
    return parser


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    requested_state_dir = str(_state_dir(args.state_dir)) if args.state_dir is not None else None

    if args.command == "install":
        return _print_result(
            handle_install_tool(
                "install_mcp_client",
                {
                    "client": args.client,
                    "mode": args.mode,
                    "config_path": args.config_path,
                    "server_name": args.server_name,
                    "state_dir": requested_state_dir,
                },
            )
        )

    if args.command == "config":
        return _print_result(
            handle_install_tool(
                "print_mcp_config",
                {
                    "client": args.client,
                    "server_name": args.server_name,
                    "state_dir": requested_state_dir,
                },
            )
        )

    state_dir = _state_dir(args.state_dir)

    if args.command == "daemon":
        if args.daemon_command == "serve":
            McpDaemon(state_dir).serve_forever()
            return 0

        try:
            client = load_client(state_dir)
            if args.daemon_command == "status":
                _print(client.status())
                return 0
            if args.daemon_command == "stop":
                _print(client.shutdown())
                return 0
        except DaemonClientError as exc:
            _print({"ok": False, "error": str(exc)})
            return 1

    if args.command == "server":
        from .server import run_stdio_server

        run_stdio_server(state_dir=state_dir)
        return 0

    parser.error("unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
