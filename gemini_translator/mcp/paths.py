from __future__ import annotations

import os
from pathlib import Path

STATE_DIR_ENV = "TRANSLATOR_MCP_STATE_DIR"
DAEMON_PORT_ENV = "TRANSLATOR_MCP_PORT"
DEFAULT_DAEMON_PORT = 65016


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_state_dir() -> Path:
    override = os.environ.get(STATE_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".translatorFork" / "mcp"


def default_daemon_port() -> int:
    override = os.environ.get(DAEMON_PORT_ENV)
    if not override:
        return DEFAULT_DAEMON_PORT
    try:
        port = int(override)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_DAEMON_PORT
    if not 1 <= port <= 65535:
        return DEFAULT_DAEMON_PORT
    return port


def clients_dir(state_dir: Path | None = None) -> Path:
    return (state_dir or default_state_dir()) / "clients"


def validate_job_id(job_id: str) -> str:
    value = str(job_id)
    path = Path(value)
    if not value or value in {".", ".."} or path.is_absolute() or "/" in value or "\\" in value:
        raise ValueError(f"unsafe job id: {job_id!r}")
    return value


def job_dir(state_dir: Path, job_id: str) -> Path:
    return state_dir / "jobs" / validate_job_id(job_id)


def build_base_url(host: str, port: int) -> str:
    """Собирает базовый http-URL демона из host/port, оборачивая IPv6-адрес в скобки."""
    host_value = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{host_value}:{port}"


def daemon_file(state_dir: Path | None = None) -> Path:
    return (state_dir or default_state_dir()) / "daemon.json"


def daemon_stdout_log(state_dir: Path | None = None) -> Path:
    return (state_dir or default_state_dir()) / "daemon.stdout.log"


def daemon_stderr_log(state_dir: Path | None = None) -> Path:
    return (state_dir or default_state_dir()) / "daemon.stderr.log"


def ensure_state_dirs(state_dir: Path | None = None) -> Path:
    root = Path(state_dir or default_state_dir())
    root.mkdir(parents=True, exist_ok=True)
    jobs = root / "jobs"
    clients = root / "clients"
    jobs.mkdir(parents=True, exist_ok=True)
    clients.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        root.chmod(0o700)
        jobs.chmod(0o700)
        clients.chmod(0o700)
    return root
