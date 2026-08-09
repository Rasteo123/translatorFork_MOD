from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import time

from .jobs import utc_now
from .paths import clients_dir, ensure_state_dirs, validate_job_id

STALE_CLIENT_SECONDS = 6 * 60 * 60


def _pid_is_alive(pid) -> bool:
    try:
        pid_value = int(pid)
    except (TypeError, ValueError, OverflowError):
        return False
    if pid_value <= 0:
        return False
    try:
        os.kill(pid_value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _public_payload(payload: dict) -> dict:
    return {
        "id": str(payload.get("id") or ""),
        "transport": str(payload.get("transport") or "stdio"),
        "client_name": str(payload.get("client_name") or "MCP client"),
        "pid": payload.get("pid"),
        "last_seen_at": payload.get("last_seen_at"),
        "supports_sampling": bool(payload.get("supports_sampling")),
    }


def list_active_client_sessions(state_dir: Path, *, now: float | None = None) -> list[dict]:
    root = ensure_state_dirs(state_dir)
    client_root = clients_dir(root)
    current_time = time.time() if now is None else float(now)
    active: list[dict] = []

    for path in client_root.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue

        try:
            last_seen_epoch = float(payload.get("last_seen_epoch") or 0)
        except (TypeError, ValueError, OverflowError):
            last_seen_epoch = 0
        pid = payload.get("pid")
        is_recent = current_time - last_seen_epoch <= STALE_CLIENT_SECONDS
        is_active = _pid_is_alive(pid) if pid is not None else is_recent
        if is_active:
            active.append(_public_payload(payload))
            continue

        try:
            path.unlink()
        except OSError:
            pass

    return sorted(active, key=lambda item: (str(item.get("client_name") or ""), str(item.get("id") or "")))


class McpClientSession:
    def __init__(self, state_dir: Path, *, client_name: str = "MCP client", transport: str = "stdio"):
        self.state_dir = ensure_state_dirs(state_dir)
        self.client_name = client_name
        self.transport = transport
        self.id = validate_job_id(f"client_{os.getpid()}_{secrets.token_hex(4)}")
        self.path = clients_dir(self.state_dir) / f"{self.id}.json"
        self.connected_at = utc_now()
        self.client_capabilities: dict = {}
        self.supports_sampling = False
        self._closed = False
        self.touch("started")

    def set_client_identity(self, client_name) -> None:
        name = str(client_name or "").strip()
        if name:
            self.client_name = name[:120]

    def set_client_capabilities(self, capabilities: dict | None) -> None:
        self.client_capabilities = dict(capabilities or {})
        self.supports_sampling = isinstance(self.client_capabilities.get("sampling"), dict)
        self.touch("initialize")

    def touch(self, method: str | None = None) -> None:
        if self._closed:
            return
        payload = {
            "id": self.id,
            "transport": self.transport,
            "client_name": self.client_name,
            "pid": os.getpid(),
            "connected_at": self.connected_at,
            "last_seen_at": utc_now(),
            "last_seen_epoch": time.time(),
            "client_capabilities": self.client_capabilities,
            "supports_sampling": self.supports_sampling,
        }
        if method:
            payload["last_method"] = str(method)

        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if os.name != "nt":
            temp_path.chmod(0o600)
        temp_path.replace(self.path)

    def close(self) -> None:
        self._closed = True
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
