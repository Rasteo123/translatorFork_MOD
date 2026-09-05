"""Регресс для mcp-bench-cli/bugs/1-mcp-sse-auth-bypass.

Дефект: GET /sse и POST /messages|/message в daemon.py::_dispatch
обрабатывались ДО проверки заголовка X-Translator-MCP-Token, поэтому любой
локальный процесс мог открыть SSE-сессию и выполнить полноценный JSON-RPC
(initialize/tools/list/tools/call) без знания секретного токена демона.

Тест поднимает настоящий McpDaemon на loopback с эфемерным портом (без
реальных subprocess: worker.run_job подменён на рекордер) и бьёт по нему
настоящими HTTP-запросами через urllib, как это делал бы неаутентифицированный
локальный клиент.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from queue import Empty, Queue

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.mcp import daemon as daemon_mod
from gemini_translator.mcp.jobs import load_job, mark_finished, save_job


def _http(method: str, url: str, body: dict | None = None, headers: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request_headers = dict(headers or {})
    if data is not None:
        request_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


class SseAuthBypassTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

        # Не запускаем настоящий subprocess: подменяем run_job рекордером,
        # чтобы даже успешный tools/call ничего не исполнял в системе.
        self._spawned: list[list[str]] = []
        self._real_run_job = daemon_mod.run_job

        def fake_run_job(state_dir, job_id):
            job = load_job(state_dir, job_id)
            self._spawned.append(list(job.argv))
            mark_finished(job, status="succeeded", exit_code=0)
            save_job(state_dir, job)
            return job

        daemon_mod.run_job = fake_run_job
        self.addCleanup(setattr, daemon_mod, "run_job", self._real_run_job)

        self.daemon = daemon_mod.McpDaemon(self._tmp.name, host="127.0.0.1", port=0, concurrency=1)
        self.daemon.start_in_thread()
        self.addCleanup(self.daemon.stop)
        self.base = self.daemon.base_url

    def _open_sse(self, url: str):
        """Открывает /sse и ждёт либо HTTP-ошибку, либо событие endpoint."""
        result: dict = {}

        def reader():
            req = urllib.request.Request(url, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    result["status"] = resp.status
                    event, buf = None, []
                    for raw in resp:
                        line = raw.decode("utf-8").rstrip("\n")
                        if line.startswith("event: "):
                            event = line[len("event: "):]
                        elif line.startswith("data: "):
                            buf.append(line[len("data: "):])
                        elif line == "":
                            if event == "endpoint":
                                result["endpoint"] = "\n".join(buf)
                                return
                            event, buf = [], []
            except urllib.error.HTTPError as exc:
                result["status"] = exc.code
                result["body"] = exc.read().decode("utf-8")

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        thread.join(timeout=5)
        return result

    def test_sse_requires_token_and_rejects_unauthenticated_connect(self):
        """RED до фикса: GET /sse без токена отдавал 200 и endpoint."""
        result = self._open_sse(f"{self.base}/sse")
        self.assertEqual(result.get("status"), 401, result)
        self.assertNotIn("endpoint", result)

    def test_messages_requires_token_even_with_valid_session_id(self):
        """RED до фикса: POST /messages без токена выполнял JSON-RPC."""
        # Получаем настоящий session_id аутентифицированным подключением,
        # но затем шлём /messages БЕЗ токена — раньше это тоже проходило
        # (проверка токена стояла после обеих SSE-веток).
        result = self._open_sse(f"{self.base}/sse?token={self.daemon.token}")
        endpoint = result.get("endpoint")
        self.assertTrue(endpoint, result)
        session_only_url = endpoint.split("&token=")[0]

        status, body = _http(
            "POST",
            session_only_url,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        self.assertEqual(status, 401, body)

    def test_authenticated_sse_flow_still_works_end_to_end(self):
        """Легитимный клиент, знающий токен, по-прежнему может работать по SSE."""
        events: Queue = Queue()
        endpoint_box: dict = {}

        def reader():
            req = urllib.request.Request(
                f"{self.base}/sse", headers={daemon_mod.TOKEN_HEADER: self.daemon.token}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                event, buf = None, []
                for raw in resp:
                    line = raw.decode("utf-8").rstrip("\n")
                    if line.startswith("event: "):
                        event = line[len("event: "):]
                    elif line.startswith("data: "):
                        buf.append(line[len("data: "):])
                    elif line == "":
                        if event == "endpoint":
                            endpoint_box["url"] = "\n".join(buf)
                        elif event == "message":
                            events.put(json.loads("\n".join(buf)))
                        event, buf = None, []

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()

        for _ in range(50):
            if "url" in endpoint_box:
                break
            time.sleep(0.05)
        endpoint = endpoint_box.get("url")
        self.assertTrue(endpoint)

        status, _ = _http(
            "POST", endpoint, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )
        self.assertEqual(status, 202)
        try:
            response = events.get(timeout=5)
        except Empty:
            self.fail("не получили ответ tools/list по SSE-каналу")
        tool_names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertIn("start_epub_build", tool_names)

        # Убеждаемся, что реального subprocess по-прежнему не было запущено
        # этим тестом (страховка от регресса в другую сторону).
        self.assertEqual(self._spawned, [])


if __name__ == "__main__":
    unittest.main()
