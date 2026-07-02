from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .daemon import TOKEN_HEADER, read_daemon_info
from .paths import daemon_stderr_log, daemon_stdout_log, default_state_dir, ensure_state_dirs

ALLOWED_DAEMON_HOSTS = {"127.0.0.1", "localhost"}


class DaemonClientError(RuntimeError):
    pass


class DaemonClient:
    def __init__(self, host, port, token):
        host_value = str(host)
        if host_value not in ALLOWED_DAEMON_HOSTS:
            raise DaemonClientError("daemon info has invalid host")
        self.host = host_value
        try:
            port_number = int(port)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DaemonClientError("daemon info has invalid port") from exc
        if not 1 <= port_number <= 65535:
            raise DaemonClientError("daemon info has invalid port")
        self.port = port_number
        self.token = str(token)

    @classmethod
    def from_info(cls, info):
        if not info:
            raise DaemonClientError("daemon is not running")
        if not isinstance(info, dict):
            raise DaemonClientError("daemon info is invalid")
        try:
            return cls(info.get("host", "127.0.0.1"), info["port"], info["token"])
        except KeyError as exc:
            raise DaemonClientError(f"daemon info is missing {exc.args[0]}") from exc

    @property
    def base_url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        return f"http://{host}:{self.port}"

    def request(self, method, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(f"{self.base_url}{path}", data=data, method=str(method).upper())
        request.add_header(TOKEN_HEADER, self.token)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise DaemonClientError(self._http_error_message(exc)) from exc
        except URLError as exc:
            raise DaemonClientError(str(exc)) from exc
        except json.JSONDecodeError as exc:
            raise DaemonClientError(f"invalid daemon response: {exc}") from exc

    def status(self):
        return self.request("GET", "/status")

    def enqueue(self, payload):
        return self.request("POST", "/jobs", payload)

    def get_job(self, job_id):
        return self.request("GET", f"/jobs/{quote(str(job_id), safe='')}")

    def list_jobs(self):
        return self.request("GET", "/jobs")

    def cancel_job(self, job_id):
        return self.request("POST", f"/jobs/{quote(str(job_id), safe='')}/cancel")

    def shutdown(self):
        return self.request("POST", "/shutdown")

    @staticmethod
    def _http_error_message(exc: HTTPError) -> str:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            return str(exc)
        return str(payload.get("error") or exc)


def load_client(state_dir: Path | None = None):
    root = Path(state_dir).expanduser().resolve() if state_dir is not None else default_state_dir()
    try:
        info = read_daemon_info(root)
    except (json.JSONDecodeError, OSError) as exc:
        raise DaemonClientError(f"daemon info is invalid: {exc}") from exc
    return DaemonClient.from_info(info)


def ensure_daemon_process(state_dir: Path | None = None):
    root = ensure_state_dirs(Path(state_dir).expanduser().resolve() if state_dir is not None else default_state_dir())
    try:
        client = load_client(root)
        client.status()
        return client
    except DaemonClientError:
        pass

    stdout_path = daemon_stdout_log(root)
    stderr_path = daemon_stderr_log(root)
    command = [
        sys.executable,
        "-m",
        "gemini_translator.mcp",
        "--state-dir",
        str(root),
        "daemon",
        "serve",
    ]
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            client = load_client(root)
            client.status()
            return client
        except DaemonClientError:
            if process.poll() is not None:
                break
            time.sleep(0.1)

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    raise DaemonClientError(f"daemon did not start; see {stderr_path}")
