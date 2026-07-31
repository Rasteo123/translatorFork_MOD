import json
import os
from queue import Empty, Queue
import socket
import stat
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
import urllib.error
import urllib.request

import pytest

from gemini_translator.mcp.client import DaemonClient, DaemonClientError, load_client
from gemini_translator.mcp.client_sessions import McpClientSession, list_active_client_sessions
from gemini_translator.mcp.ai_bridge import create_gui_ai_task, load_gui_ai_task
from gemini_translator.mcp.daemon import McpDaemon, read_daemon_info
from gemini_translator.mcp.jobs import create_job, mark_finished, save_job
from gemini_translator.mcp.paths import DEFAULT_DAEMON_PORT, daemon_file

_SSE_SOCKET_TIMEOUT = 0.2


class _SocketLineReader:
    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._buffer = bytearray()

    def readline(self) -> bytes:
        while True:
            newline_index = self._buffer.find(b"\n")
            if newline_index >= 0:
                line = bytes(self._buffer[: newline_index + 1])
                del self._buffer[: newline_index + 1]
                return line

            chunk = self._sock.recv(4096)
            if not chunk:
                if self._buffer:
                    line = bytes(self._buffer)
                    self._buffer.clear()
                    return line
                return b""
            self._buffer.extend(chunk)

    def close(self) -> None:
        return None


def _request(method, url, token, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("X-Translator-MCP-Token", token)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _read_http_error_json(error):
    try:
        return json.loads(error.read().decode("utf-8"))
    finally:
        error.close()


class _RawSseResponse:
    def __init__(self, sock: socket.socket, reader):
        self._sock = sock
        self._reader = reader

    def readline(self):
        return self._reader.readline()

    def close(self):
        try:
            self._reader.close()
        finally:
            self._sock.close()


class _FakeSseSocket:
    def __init__(self, payload: bytes):
        self._payload = bytearray(payload)
        self.sent = b""
        self.closed = False
        self.timeout = None

    def sendall(self, payload: bytes):
        self.sent += payload

    def recv(self, _size: int):
        if not self._payload:
            return b""
        chunk = bytes(self._payload[:7])
        del self._payload[:7]
        return chunk

    def settimeout(self, timeout):
        self.timeout = timeout

    def close(self):
        self.closed = True


def _open_sse(url):
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    sock = socket.create_connection((host, port), timeout=5)
    try:
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Accept: text/event-stream\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        sock.sendall(request)
        reader = _SocketLineReader(sock)
        status_line = reader.readline().decode("iso-8859-1", errors="replace").strip()
        if " 200 " not in f" {status_line} ":
            raise AssertionError(f"SSE endpoint returned {status_line!r}")
        while True:
            header_line = reader.readline()
            if header_line in {b"\r\n", b"\n", b""}:
                break
        sock.settimeout(_SSE_SOCKET_TIMEOUT)
        return _RawSseResponse(sock, reader)
    except Exception:
        sock.close()
        raise


def test_open_sse_reads_lines_without_socket_makefile(monkeypatch):
    fake_socket = _FakeSseSocket(
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/event-stream\r\n"
        b"\r\n"
        b"event: endpoint\n"
        b"data: http://127.0.0.1:1234/messages?session_id=fake\n"
        b"\n"
    )
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: fake_socket)

    response = _open_sse("http://127.0.0.1:1234/sse")
    try:
        endpoint = _read_sse_event(response, "endpoint", timeout=1)
    finally:
        response.close()

    assert endpoint == "http://127.0.0.1:1234/messages?session_id=fake"
    assert fake_socket.closed is True
    assert b"GET /sse HTTP/1.1" in fake_socket.sent


def _read_sse_event(response, event_name, *, timeout=5):
    deadline = time.time() + timeout
    event = None
    data_lines = []
    while time.time() < deadline:
        try:
            raw_line = response.readline()
        except (OSError, TimeoutError, socket.timeout) as exc:
            if time.time() < deadline:
                continue
            raise AssertionError(f"SSE event {event_name!r} was not received") from exc
        if not raw_line:
            time.sleep(0.01)
            continue
        line = raw_line.decode("utf-8").rstrip("\n")
        if line.endswith("\r"):
            line = line[:-1]
        if not line:
            if event == event_name:
                return "\n".join(data_lines)
            event = None
            data_lines = []
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())
    raise AssertionError(f"SSE event {event_name!r} was not received")


def test_daemon_rejects_missing_token(tmp_path):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    try:
        request = urllib.request.Request(f"{daemon.base_url}/status", method="GET")
        try:
            urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            try:
                assert exc.code == 401
            finally:
                exc.close()
        else:
            raise AssertionError("request without token must fail")
    finally:
        daemon.stop()


def test_daemon_enqueue_and_status(tmp_path):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    try:
        payload = {
            "job_type": "fake",
            "argv": [sys.executable, "-c", "import json; print(json.dumps({'ok': True}))"],
            "project": "/project",
            "epub": "/book.epub",
            "metadata": {"tool": "fake"},
        }
        created = _request("POST", f"{daemon.base_url}/jobs", daemon.token, payload)
        assert created["job"]["status"] in {"queued", "running", "succeeded"}

        deadline = time.time() + 10
        status = {}
        while time.time() < deadline:
            status = _request("GET", f"{daemon.base_url}/jobs/{created['job']['id']}", daemon.token)
            if status["job"]["status"] == "succeeded":
                break
            time.sleep(0.1)

        assert status["job"]["status"] == "succeeded"
        assert status["job"]["project"] == "/project"
    finally:
        daemon.stop()


def test_daemon_info_is_written(tmp_path):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    try:
        info = read_daemon_info(tmp_path)
        assert info["pid"] > 0
        assert info["port"] == daemon.port
        assert info["token"] == daemon.token
    finally:
        daemon.stop()


def test_daemon_http_server_threads_do_not_block_pytest_shutdown(tmp_path):
    from gemini_translator.mcp import daemon as daemon_module

    server_class = getattr(daemon_module, "_DaemonHTTPServer", None)
    assert server_class is not None
    assert server_class.daemon_threads is True
    assert server_class.block_on_close is False

    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    try:
        assert isinstance(daemon._server, server_class)
    finally:
        daemon.stop()


def test_sse_loop_polls_queue_faster_than_keepalive():
    from gemini_translator.mcp import daemon as daemon_module

    assert daemon_module.SSE_QUEUE_POLL_INTERVAL_SECONDS < 1.0
    assert daemon_module.SSE_KEEPALIVE_INTERVAL_SECONDS > daemon_module.SSE_QUEUE_POLL_INTERVAL_SECONDS


def test_read_daemon_info_returns_empty_when_missing(tmp_path):
    assert read_daemon_info(tmp_path) == {}


def test_daemon_start_drains_existing_queued_job(tmp_path):
    job = create_job(
        tmp_path,
        "fake",
        [sys.executable, "-c", "import json; print(json.dumps({'ok': True}))"],
        project="/project",
        epub="/book.epub",
        metadata={"tool": "fake"},
    )
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    try:
        deadline = time.time() + 10
        status = {}
        while time.time() < deadline:
            status = _request("GET", f"{daemon.base_url}/jobs/{job.id}", daemon.token)
            if status["job"]["status"] == "succeeded":
                break
            time.sleep(0.1)

        assert status["job"]["status"] == "succeeded"
    finally:
        daemon.stop()


def test_status_does_not_expose_daemon_token(tmp_path):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    try:
        status_payload = _request("GET", f"{daemon.base_url}/status", daemon.token)

        assert "token" not in status_payload["daemon"]
        assert daemon.token not in json.dumps(status_payload)
    finally:
        daemon.stop()


def test_status_reports_active_mcp_client_sessions(tmp_path):
    clients_dir = tmp_path / "clients"
    clients_dir.mkdir(parents=True)
    (clients_dir / "gemini.json").write_text(
        json.dumps(
            {
                "id": "gemini",
                "transport": "stdio",
                "client_name": "Gemini",
                "pid": os.getpid(),
                "last_seen_epoch": time.time(),
            }
        ),
        encoding="utf-8",
    )

    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    try:
        status_payload = _request("GET", f"{daemon.base_url}/status", daemon.token)

        assert status_payload["mcp_clients"]["connected"] == 1
        assert status_payload["mcp_clients"]["items"][0]["client_name"] == "Gemini"
        assert status_payload["mcp_clients"]["items"][0]["transport"] == "stdio"
    finally:
        daemon.stop()


def test_client_session_registry_ignores_recent_record_with_dead_pid(tmp_path):
    clients_dir = tmp_path / "clients"
    clients_dir.mkdir(parents=True)
    stale_path = clients_dir / "dead.json"
    stale_path.write_text(
        json.dumps(
            {
                "id": "dead",
                "transport": "stdio",
                "client_name": "Dead client",
                "pid": -1,
                "last_seen_epoch": time.time(),
            }
        ),
        encoding="utf-8",
    )

    assert list_active_client_sessions(tmp_path) == []
    assert not stale_path.exists()


def test_daemon_stop_signals_sse_sessions_before_server_shutdown(tmp_path):
    daemon = McpDaemon(tmp_path)
    session = McpClientSession(tmp_path, client_name="test", transport="sse")
    event_queue = Queue()
    daemon._sse_sessions[session.id] = (session, event_queue)

    class Server:
        def shutdown(self):
            try:
                payload = event_queue.get_nowait()
            except Empty as exc:
                raise AssertionError("SSE session was not signaled before server shutdown") from exc
            assert payload is None

        def server_close(self):
            pass

    daemon._server = Server()

    daemon.stop()


def test_daemon_sse_endpoint_registers_client_without_daemon_token(tmp_path):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    response = None
    try:
        response = _open_sse(f"{daemon.base_url}/sse")
        endpoint = _read_sse_event(response, "endpoint")

        status_payload = _request("GET", f"{daemon.base_url}/status", daemon.token)

        assert urlparse(endpoint).path == "/messages"
        assert status_payload["mcp_clients"]["connected"] == 1
        assert status_payload["mcp_clients"]["items"][0]["transport"] == "sse"
    finally:
        if response is not None:
            response.close()
        daemon.stop()


def test_daemon_stop_after_sse_client_close_returns_quickly(tmp_path):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    response = None
    try:
        response = _open_sse(f"{daemon.base_url}/sse")
        _read_sse_event(response, "endpoint")
        response.close()
        response = None

        started = time.monotonic()
        daemon.stop()

        assert time.monotonic() - started < 1.0
    finally:
        if response is not None:
            response.close()
        daemon.stop()


def test_daemon_sse_messages_round_trip_mcp_json_rpc(tmp_path):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    response = None
    try:
        response = _open_sse(f"{daemon.base_url}/sse")
        endpoint = _read_sse_event(response, "endpoint")
        request = urllib.request.Request(
            endpoint,
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).encode("utf-8"),
            method="POST",
        )
        request.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(request, timeout=5) as post_response:
            assert post_response.status in {200, 202}

        payload = json.loads(_read_sse_event(response, "message"))

        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 1
        assert payload["result"]["serverInfo"]["name"] == "translatorFork"
    finally:
        if response is not None:
            response.close()
        daemon.stop()


def test_daemon_ai_completion_uses_sse_sampling_client(tmp_path):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    response = None
    try:
        response = _open_sse(f"{daemon.base_url}/sse")
        endpoint = _read_sse_event(response, "endpoint")

        init_request = urllib.request.Request(
            endpoint,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "init-1",
                    "method": "initialize",
                    "params": {"capabilities": {"sampling": {}}},
                }
            ).encode("utf-8"),
            method="POST",
        )
        init_request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(init_request, timeout=5) as post_response:
            assert post_response.status in {200, 202}
        init_payload = json.loads(_read_sse_event(response, "message"))
        assert init_payload["id"] == "init-1"

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _request,
                "POST",
                f"{daemon.base_url}/ai/completions",
                daemon.token,
                {
                    "prompt": "Translate: hello",
                    "system_instruction": "Return only the translation.",
                    "timeout_sec": 5,
                },
            )

            sampling_request = json.loads(_read_sse_event(response, "message"))
            assert sampling_request["jsonrpc"] == "2.0"
            assert sampling_request["method"] == "sampling/createMessage"
            assert sampling_request["params"]["messages"][0]["content"]["text"] == "Translate: hello"
            assert sampling_request["params"]["systemPrompt"] == "Return only the translation."

            sampling_response = urllib.request.Request(
                endpoint,
                data=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": sampling_request["id"],
                        "result": {
                            "role": "assistant",
                            "content": {"type": "text", "text": "привет"},
                        },
                    }
                ).encode("utf-8"),
                method="POST",
            )
            sampling_response.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(sampling_response, timeout=5) as post_response:
                assert post_response.status in {200, 202}

            completion = future.result(timeout=5)

        assert completion == {
            "ok": True,
            "transport": "sampling",
            "text": "привет",
        }
    finally:
        if response is not None:
            response.close()
        daemon.stop()


def test_daemon_ai_completion_falls_back_to_gui_ai_task_inbox(tmp_path):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    response = None
    try:
        response = _open_sse(f"{daemon.base_url}/sse")
        endpoint = _read_sse_event(response, "endpoint")

        init_request = urllib.request.Request(
            endpoint,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "init-no-sampling",
                    "method": "initialize",
                    "params": {"capabilities": {}},
                }
            ).encode("utf-8"),
            method="POST",
        )
        init_request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(init_request, timeout=5) as post_response:
            assert post_response.status in {200, 202}
        init_payload = json.loads(_read_sse_event(response, "message"))
        assert init_payload["id"] == "init-no-sampling"

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _request,
                "POST",
                f"{daemon.base_url}/ai/completions",
                daemon.token,
                {
                    "prompt": "Translate: bridge",
                    "system_instruction": "Return only the translation.",
                    "timeout_sec": 5,
                },
            )

            tasks_payload = {}
            deadline = time.time() + 5
            while time.time() < deadline:
                tasks_payload = _request("GET", f"{daemon.base_url}/gui-ai-tasks", daemon.token)
                if tasks_payload["tasks"]:
                    break
                time.sleep(0.05)

            task = tasks_payload["tasks"][0]
            claim = _request(
                "POST",
                f"{daemon.base_url}/gui-ai-tasks/{task['id']}/claim",
                daemon.token,
                {"client_name": "Gemini"},
            )
            assert claim["task"]["prompt"] == "Translate: bridge"
            assert claim["task"]["system_instruction"] == "Return only the translation."

            _request(
                "POST",
                f"{daemon.base_url}/gui-ai-tasks/{task['id']}/complete",
                daemon.token,
                {"text": "мост"},
            )
            completion = future.result(timeout=5)

        assert completion == {
            "ok": True,
            "transport": "inbox",
            "task_id": task["id"],
            "text": "мост",
        }
    finally:
        if response is not None:
            response.close()
        daemon.stop()


def test_daemon_ai_completion_inbox_failure_returns_http_error(tmp_path):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    response = None
    try:
        response = _open_sse(f"{daemon.base_url}/sse")
        endpoint = _read_sse_event(response, "endpoint")

        init_request = urllib.request.Request(
            endpoint,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "init-no-sampling-fail",
                    "method": "initialize",
                    "params": {"capabilities": {}},
                }
            ).encode("utf-8"),
            method="POST",
        )
        init_request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(init_request, timeout=5) as post_response:
            assert post_response.status in {200, 202}
        _read_sse_event(response, "message")

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _request,
                "POST",
                f"{daemon.base_url}/ai/completions",
                daemon.token,
                {"prompt": "Translate: fail", "timeout_sec": 5},
            )

            task_id = None
            deadline = time.time() + 5
            while time.time() < deadline:
                tasks_payload = _request("GET", f"{daemon.base_url}/gui-ai-tasks", daemon.token)
                if tasks_payload["tasks"]:
                    task_id = tasks_payload["tasks"][0]["id"]
                    break
                time.sleep(0.05)
            assert task_id

            _request(
                "POST",
                f"{daemon.base_url}/gui-ai-tasks/{task_id}/fail",
                daemon.token,
                {"error": "AI client refused"},
            )

            with pytest.raises(urllib.error.HTTPError) as error_info:
                future.result(timeout=5)

        assert error_info.value.code == 502
        payload = _read_http_error_json(error_info.value)
        assert payload["ok"] is False
        assert "AI client refused" in payload["error"]
    finally:
        if response is not None:
            response.close()
        daemon.stop()


def test_daemon_ai_completion_inbox_failure_preserves_limit_payload(tmp_path):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    response = None
    try:
        response = _open_sse(f"{daemon.base_url}/sse")
        endpoint = _read_sse_event(response, "endpoint")

        init_request = urllib.request.Request(
            endpoint,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "init-no-sampling-limit",
                    "method": "initialize",
                    "params": {"capabilities": {}},
                }
            ).encode("utf-8"),
            method="POST",
        )
        init_request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(init_request, timeout=5) as post_response:
            assert post_response.status in {200, 202}
        _read_sse_event(response, "message")

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _request,
                "POST",
                f"{daemon.base_url}/ai/completions",
                daemon.token,
                {"prompt": "Translate: limit", "timeout_sec": 5},
            )

            task_id = None
            deadline = time.time() + 5
            while time.time() < deadline:
                tasks_payload = _request("GET", f"{daemon.base_url}/gui-ai-tasks", daemon.token)
                if tasks_payload["tasks"]:
                    task_id = tasks_payload["tasks"][0]["id"]
                    break
                time.sleep(0.05)
            assert task_id

            _request(
                "POST",
                f"{daemon.base_url}/gui-ai-tasks/{task_id}/fail",
                daemon.token,
                {
                    "error": "usage limit reached",
                    "reset_after_seconds": 120,
                    "limit_window_seconds": 5 * 60 * 60,
                },
            )

            with pytest.raises(urllib.error.HTTPError) as error_info:
                future.result(timeout=5)

        assert error_info.value.code == 502
        payload = _read_http_error_json(error_info.value)
        assert payload["ok"] is False
        assert payload["error"] == "usage limit reached"
        assert payload["reset_after_seconds"] == 120
        assert payload["limit_window_seconds"] == 5 * 60 * 60
    finally:
        if response is not None:
            response.close()
        daemon.stop()


def test_daemon_ai_completion_cancel_stops_inbox_wait_and_hides_task(tmp_path):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    response = None
    request_id = "cancel_inbox_request"
    try:
        response = _open_sse(f"{daemon.base_url}/sse")
        endpoint = _read_sse_event(response, "endpoint")

        init_request = urllib.request.Request(
            endpoint,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "init-no-sampling-cancel",
                    "method": "initialize",
                    "params": {"capabilities": {}},
                }
            ).encode("utf-8"),
            method="POST",
        )
        init_request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(init_request, timeout=5) as post_response:
            assert post_response.status in {200, 202}
        _read_sse_event(response, "message")

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _request,
                "POST",
                f"{daemon.base_url}/ai/completions",
                daemon.token,
                {"request_id": request_id, "prompt": "Translate: cancel", "timeout_sec": 5},
            )

            task_id = None
            deadline = time.time() + 5
            while time.time() < deadline:
                tasks_payload = _request("GET", f"{daemon.base_url}/gui-ai-tasks", daemon.token)
                if tasks_payload["tasks"]:
                    task_id = tasks_payload["tasks"][0]["id"]
                    break
                time.sleep(0.05)
            assert task_id

            cancel_payload = _request(
                "POST",
                f"{daemon.base_url}/ai/completions/{request_id}/cancel",
                daemon.token,
            )

            with pytest.raises(urllib.error.HTTPError) as error_info:
                future.result(timeout=5)

        assert cancel_payload == {"ok": True, "cancelled": True, "request_id": request_id}
        assert error_info.value.code == 499
        payload = _read_http_error_json(error_info.value)
        assert payload["ok"] is False
        assert "cancelled" in payload["error"]
        assert _request("GET", f"{daemon.base_url}/gui-ai-tasks", daemon.token)["tasks"] == []
    finally:
        if response is not None:
            response.close()
        daemon.stop()


def test_daemon_hides_orphan_gui_ai_tasks_from_client(tmp_path):
    task = create_gui_ai_task(tmp_path, {"prompt": "stale prompt"})
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    try:
        payload = _request("GET", f"{daemon.base_url}/gui-ai-tasks", daemon.token)

        assert payload["tasks"] == []
        cancelled = load_gui_ai_task(tmp_path, task.id)
        assert cancelled.status == "cancelled"
        assert "no active application request" in cancelled.error
    finally:
        daemon.stop()


def test_daemon_ai_completion_timeout_cancels_inbox_task(tmp_path):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    response = None
    try:
        response = _open_sse(f"{daemon.base_url}/sse")
        endpoint = _read_sse_event(response, "endpoint")

        init_request = urllib.request.Request(
            endpoint,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "init-no-sampling-timeout",
                    "method": "initialize",
                    "params": {"capabilities": {}},
                }
            ).encode("utf-8"),
            method="POST",
        )
        init_request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(init_request, timeout=5) as post_response:
            assert post_response.status in {200, 202}
        _read_sse_event(response, "message")

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _request,
                "POST",
                f"{daemon.base_url}/ai/completions",
                daemon.token,
                {"request_id": "timeout_inbox_request", "prompt": "Translate: timeout", "timeout_sec": 1},
            )

            task_id = None
            deadline = time.time() + 5
            while time.time() < deadline:
                tasks_payload = _request("GET", f"{daemon.base_url}/gui-ai-tasks", daemon.token)
                if tasks_payload["tasks"]:
                    task_id = tasks_payload["tasks"][0]["id"]
                    break
                time.sleep(0.05)
            assert task_id

            with pytest.raises(urllib.error.HTTPError) as error_info:
                future.result(timeout=5)

        assert error_info.value.code == 504
        error_info.value.close()
        cancelled = load_gui_ai_task(tmp_path, task_id)
        assert cancelled.status == "cancelled"
        assert "timed out" in cancelled.error
        assert _request("GET", f"{daemon.base_url}/gui-ai-tasks", daemon.token)["tasks"] == []
    finally:
        if response is not None:
            response.close()
        daemon.stop()


def test_daemon_state_files_are_private(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX mode assertions do not apply on Windows")

    state_dir = tmp_path / "state"
    daemon = McpDaemon(state_dir)
    daemon.start_in_thread()
    try:
        assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(daemon_file(state_dir).stat().st_mode) == 0o600
    finally:
        daemon.stop()


def test_daemon_rejects_ipv6_loopback_until_supported(tmp_path):
    with pytest.raises(ValueError):
        McpDaemon(tmp_path, host="::1")


def test_daemon_client_status(tmp_path):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    try:
        client = DaemonClient.from_info(read_daemon_info(tmp_path))
        payload = client.status()
        assert payload["ok"] is True
        assert payload["daemon"]["port"] == daemon.port
    finally:
        daemon.stop()


def test_module_cli_status_without_daemon(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gemini_translator.mcp",
            "--state-dir",
            str(tmp_path),
            "daemon",
            "status",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 1
    assert "daemon is not running" in result.stdout


def test_module_cli_serve_uses_stable_default_port(monkeypatch, tmp_path):
    from gemini_translator.mcp import __main__ as mcp_main

    calls = []

    class FakeDaemon:
        def __init__(self, state_dir, *, port=0):
            calls.append({"state_dir": state_dir, "port": port})

        def serve_forever(self):
            return None

    monkeypatch.setattr(mcp_main, "McpDaemon", FakeDaemon)

    result = mcp_main.main(["--state-dir", str(tmp_path), "daemon", "serve"])

    assert result == 0
    assert calls == [{"state_dir": tmp_path.resolve(), "port": DEFAULT_DAEMON_PORT}]


def test_module_cli_status_with_corrupt_daemon_info_reports_json_error(tmp_path):
    daemon_file(tmp_path).write_text("{not json", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gemini_translator.mcp",
            "--state-dir",
            str(tmp_path),
            "daemon",
            "status",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 1
    assert '"ok": false' in result.stdout
    assert '"error"' in result.stdout
    assert "Traceback" not in result.stderr


def test_daemon_client_rejects_invalid_port_info():
    with pytest.raises(DaemonClientError):
        DaemonClient.from_info({"host": "127.0.0.1", "port": "bad", "token": "x"})


@pytest.mark.parametrize("host", ["example.com", "192.168.1.2", "::1"])
def test_daemon_client_rejects_non_loopback_host_info(host):
    with pytest.raises(DaemonClientError, match="host"):
        DaemonClient.from_info({"host": host, "port": 1, "token": "x"})


@pytest.mark.parametrize("port", [float("inf"), 999999999999999999999999999, 0, -1])
def test_daemon_client_rejects_invalid_numeric_port_info(port):
    with pytest.raises(DaemonClientError):
        DaemonClient.from_info({"host": "127.0.0.1", "port": port, "token": "x"})


@pytest.mark.parametrize("port_json", ["1e999", "999999999999999999999999999", "0", "-1"])
def test_load_client_rejects_invalid_numeric_port_info(tmp_path, port_json):
    daemon_file(tmp_path).write_text(
        f'{{"host": "127.0.0.1", "port": {port_json}, "token": "x"}}',
        encoding="utf-8",
    )

    with pytest.raises(DaemonClientError):
        load_client(tmp_path)


def test_load_client_with_corrupt_daemon_info_raises_client_error(tmp_path):
    daemon_file(tmp_path).write_text("{not json", encoding="utf-8")

    with pytest.raises(DaemonClientError):
        load_client(tmp_path)


def test_pipeline_skips_later_steps_after_failure(tmp_path):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    try:
        payload = {
            "job_type": "pipeline",
            "argv": [],
            "project": "/project",
            "epub": "/book.epub",
            "metadata": {
                "tool": "start_full_pipeline",
                "continue_on_error": False,
                "steps": [
                    {
                        "name": "first",
                        "tool": "start_translation",
                        "job_type": "fake",
                        "argv": [sys.executable, "-c", "import sys; sys.exit(2)"],
                    },
                    {
                        "name": "second",
                        "tool": "start_epub_build",
                        "job_type": "fake",
                        "argv": [sys.executable, "-c", "import json; print(json.dumps({'ok': True}))"],
                    },
                ],
            },
        }
        created = _request("POST", f"{daemon.base_url}/jobs", daemon.token, payload)
        parent_id = created["job"]["id"]

        deadline = time.time() + 10
        parent = {}
        while time.time() < deadline:
            parent = _request("GET", f"{daemon.base_url}/jobs/{parent_id}", daemon.token)
            if parent["job"]["status"] == "failed":
                break
            time.sleep(0.1)

        children = parent["job"]["children"]
        first = _request("GET", f"{daemon.base_url}/jobs/{children[0]}", daemon.token)
        second = _request("GET", f"{daemon.base_url}/jobs/{children[1]}", daemon.token)

        assert parent["job"]["status"] == "failed"
        assert first["job"]["status"] == "failed"
        assert second["job"]["status"] == "cancelled"
    finally:
        daemon.stop()


def test_cancel_pipeline_parent_cancels_running_and_queued_children(tmp_path):
    daemon = McpDaemon(tmp_path, concurrency=1)
    daemon.start_in_thread()
    try:
        payload = {
            "job_type": "pipeline",
            "argv": [],
            "project": "/project",
            "epub": "/book.epub",
            "metadata": {
                "tool": "start_full_pipeline",
                "continue_on_error": False,
                "steps": [
                    {
                        "name": "first",
                        "tool": "start_translation",
                        "job_type": "fake",
                        "argv": [
                            sys.executable,
                            "-c",
                            "import json, time; time.sleep(0.7); print(json.dumps({'ok': True}))",
                        ],
                    },
                    {
                        "name": "second",
                        "tool": "start_epub_build",
                        "job_type": "fake",
                        "argv": [sys.executable, "-c", "import json; print(json.dumps({'ok': True}))"],
                    },
                ],
            },
        }
        created = _request("POST", f"{daemon.base_url}/jobs", daemon.token, payload)
        parent_id = created["job"]["id"]
        first_id, second_id = created["job"]["children"]

        deadline = time.time() + 10
        while time.time() < deadline:
            first = _request("GET", f"{daemon.base_url}/jobs/{first_id}", daemon.token)
            if first["job"]["status"] == "running":
                break
            time.sleep(0.05)

        cancelled = _request("POST", f"{daemon.base_url}/jobs/{parent_id}/cancel", daemon.token)
        assert cancelled["job"]["status"] == "cancelled"

        while time.time() < deadline:
            first = _request("GET", f"{daemon.base_url}/jobs/{first_id}", daemon.token)
            second = _request("GET", f"{daemon.base_url}/jobs/{second_id}", daemon.token)
            if first["job"]["status"] in {"cancelled", "succeeded"} and second["job"]["status"] in {
                "cancelled",
                "succeeded",
            }:
                break
            time.sleep(0.05)

        assert first["job"]["status"] == "cancelled"
        assert second["job"]["status"] == "cancelled"
    finally:
        daemon.stop()


def test_daemon_start_refreshes_terminal_pipeline_parent(tmp_path):
    parent = create_job(
        tmp_path,
        "pipeline",
        [],
        project="/project",
        epub="/book.epub",
        metadata={"tool": "start_full_pipeline", "continue_on_error": False, "steps": []},
    )
    first = create_job(
        tmp_path,
        "fake",
        [sys.executable, "-c", "import json; print(json.dumps({'ok': True}))"],
        project="/project",
        epub="/book.epub",
        metadata={"tool": "start_translation", "pipeline_parent": parent.id, "pipeline_index": 0},
    )
    second = create_job(
        tmp_path,
        "fake",
        [sys.executable, "-c", "import json; print(json.dumps({'ok': True}))"],
        project="/project",
        epub="/book.epub",
        metadata={"tool": "start_epub_build", "pipeline_parent": parent.id, "pipeline_index": 1},
    )
    mark_finished(first, status="succeeded", exit_code=0)
    mark_finished(second, status="succeeded", exit_code=0)
    save_job(tmp_path, first)
    save_job(tmp_path, second)
    parent.children = [first.id, second.id]
    parent.status = "running"
    parent.started_at = parent.created_at
    save_job(tmp_path, parent)

    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    try:
        parent_status = _request("GET", f"{daemon.base_url}/jobs/{parent.id}", daemon.token)

        assert parent_status["job"]["status"] == "succeeded"
    finally:
        daemon.stop()


def test_daemon_rejects_reserved_pipeline_metadata_on_ordinary_jobs(tmp_path):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    try:
        payload = {
            "job_type": "fake",
            "argv": [sys.executable, "-c", "import json; print(json.dumps({'ok': True}))"],
            "project": "/project",
            "epub": "/book.epub",
            "metadata": {"tool": "fake", "pipeline_parent": "../evil"},
        }

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _request("POST", f"{daemon.base_url}/jobs", daemon.token, payload)

        assert exc_info.value.code == 400
        exc_info.value.close()
    finally:
        daemon.stop()


def test_daemon_rejects_falsey_non_list_pipeline_steps(tmp_path):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    try:
        payload = {
            "job_type": "pipeline",
            "argv": [],
            "project": "/project",
            "epub": "/book.epub",
            "metadata": {"tool": "start_full_pipeline", "steps": ""},
        }

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _request("POST", f"{daemon.base_url}/jobs", daemon.token, payload)

        assert exc_info.value.code == 400
        exc_info.value.close()
    finally:
        daemon.stop()


@pytest.mark.parametrize("metadata", ["abc", 1, [["steps", []]]])
def test_daemon_rejects_non_object_pipeline_metadata(tmp_path, metadata):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    try:
        payload = {
            "job_type": "pipeline",
            "argv": [],
            "project": "/project",
            "epub": "/book.epub",
            "metadata": metadata,
        }

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _request("POST", f"{daemon.base_url}/jobs", daemon.token, payload)

        assert exc_info.value.code == 400
        exc_info.value.close()
    finally:
        daemon.stop()


def test_daemon_start_tolerates_poisoned_pipeline_child_id(tmp_path):
    parent = create_job(
        tmp_path,
        "pipeline",
        [],
        project="/project",
        epub="/book.epub",
        metadata={"tool": "start_full_pipeline", "continue_on_error": False, "steps": []},
    )
    parent.children = ["../evil"]
    parent.status = "running"
    parent.started_at = parent.created_at
    save_job(tmp_path, parent)

    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    try:
        status = _request("GET", f"{daemon.base_url}/status", daemon.token)
        parent_status = _request("GET", f"{daemon.base_url}/jobs/{parent.id}", daemon.token)

        assert status["ok"] is True
        assert parent_status["job"]["status"] in {"running", "failed", "cancelled"}
    finally:
        daemon.stop()
