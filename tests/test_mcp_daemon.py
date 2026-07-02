import json
import os
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from gemini_translator.mcp.client import DaemonClient, DaemonClientError, load_client
from gemini_translator.mcp.daemon import McpDaemon, read_daemon_info
from gemini_translator.mcp.jobs import create_job, mark_finished, save_job
from gemini_translator.mcp.paths import daemon_file


def _request(method, url, token, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("X-Translator-MCP-Token", token)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def test_daemon_rejects_missing_token(tmp_path):
    daemon = McpDaemon(tmp_path)
    daemon.start_in_thread()
    try:
        request = urllib.request.Request(f"{daemon.base_url}/status", method="GET")
        try:
            urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
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
