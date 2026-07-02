import sys
import types

from gemini_translator.mcp.server import McpStdioServer, TOOL_NAMES


class FakeClient:
    def __init__(self):
        self.enqueued = []

    def status(self):
        return {"ok": True, "daemon": {"pid": 1}, "queue": {}}

    def enqueue(self, payload):
        self.enqueued.append(payload)
        return {"ok": True, "job": {"id": "job_1", "status": "queued", "type": payload["job_type"]}}

    def get_job(self, job_id):
        return {"ok": True, "job": {"id": job_id, "status": "succeeded"}}

    def list_jobs(self):
        return {"ok": True, "jobs": []}

    def cancel_job(self, job_id):
        return {"ok": True, "job": {"id": job_id, "status": "cancelled"}}


class FailingStatusClient(FakeClient):
    def status(self):
        raise RuntimeError("boom")


def test_tools_list_contains_translation_tools():
    server = McpStdioServer(client_factory=lambda: FakeClient())
    result = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tool_names = [tool["name"] for tool in result["result"]["tools"]]

    assert "start_translation" in tool_names
    assert "get_job_status" in tool_names
    assert tool_names == TOOL_NAMES


def test_initialize_response_uses_mcp_protocol_version():
    server = McpStdioServer(client_factory=lambda: FakeClient())
    result = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

    assert result["result"]["protocolVersion"] == "2025-06-18"
    assert result["result"]["serverInfo"]["name"] == "translatorFork"


def test_start_translation_enqueues_daemon_job():
    fake = FakeClient()
    server = McpStdioServer(client_factory=lambda: fake)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "start_translation",
                "arguments": {"epub": "/book.epub", "project": "/project"},
            },
        }
    )

    assert response["result"]["isError"] is False
    assert fake.enqueued[0]["job_type"] == "translation"
    assert fake.enqueued[0]["project"] == "/project"


def test_glossary_correction_returns_structured_unsupported_response():
    server = McpStdioServer(client_factory=lambda: FakeClient())
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "start_glossary_review_or_correction",
                "arguments": {"epub": "/book.epub", "project": "/project"},
            },
        }
    )

    assert response["result"]["isError"] is True
    assert "unsupported_in_this_build" in response["result"]["content"][0]["text"]


def test_get_job_status_calls_client():
    server = McpStdioServer(client_factory=lambda: FakeClient())
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "get_job_status", "arguments": {"job_id": "job_1"}},
        }
    )

    assert response["result"]["isError"] is False
    assert "job_1" in response["result"]["content"][0]["text"]


def test_translator_status_client_failure_returns_tool_error():
    server = McpStdioServer(client_factory=lambda: FailingStatusClient())
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "translator_status", "arguments": {}},
        }
    )

    assert "error" not in response
    assert response["result"]["isError"] is True
    assert "boom" in response["result"]["content"][0]["text"]


def test_print_mcp_config_returns_config_snippet():
    server = McpStdioServer(client_factory=lambda: FakeClient())
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "print_mcp_config", "arguments": {"client": "codex"}},
        }
    )

    assert "error" not in response
    assert response["result"]["isError"] is False
    assert "content" in response["result"]
    assert "[mcp_servers.translatorFork]" in response["result"]["content"][0]["text"]
    assert "gemini_translator.mcp" in response["result"]["content"][0]["text"]


def test_print_mcp_config_wraps_available_client_install_payload(monkeypatch):
    module = types.SimpleNamespace(
        handle_install_tool=lambda name, arguments: {
            "ok": True,
            "tool": name,
            "arguments": arguments,
        }
    )
    monkeypatch.setitem(sys.modules, "gemini_translator.mcp.client_install", module)
    server = McpStdioServer(client_factory=lambda: FakeClient())
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "print_mcp_config", "arguments": {"client": "codex"}},
        }
    )

    assert "error" not in response
    assert response["result"]["isError"] is False
    assert "content" in response["result"]
    assert "print_mcp_config" in response["result"]["content"][0]["text"]
    assert "codex" in response["result"]["content"][0]["text"]


def test_installer_tool_schemas_expose_supported_arguments():
    server = McpStdioServer(client_factory=lambda: FakeClient())
    response = server.handle_request({"jsonrpc": "2.0", "id": 8, "method": "tools/list"})
    tools = {tool["name"]: tool for tool in response["result"]["tools"]}

    install_props = tools["install_mcp_client"]["inputSchema"]["properties"]
    assert {"client", "mode", "config_path", "server_name", "state_dir"} <= set(install_props)

    config_props = tools["print_mcp_config"]["inputSchema"]["properties"]
    assert {"client", "server_name", "state_dir"} <= set(config_props)


def test_notification_without_id_returns_none():
    server = McpStdioServer(client_factory=lambda: FakeClient())

    assert server.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_start_full_pipeline_enqueues_pipeline_job():
    fake = FakeClient()
    server = McpStdioServer(client_factory=lambda: fake)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "start_full_pipeline",
                "arguments": {
                    "epub": "/book.epub",
                    "project": "/project",
                    "steps": ["glossary", "translation", "untranslated_fix", "consistency", "epub_build"],
                },
            },
        }
    )

    assert response["result"]["isError"] is False
    assert fake.enqueued[0]["job_type"] == "pipeline"
    assert fake.enqueued[0]["metadata"]["steps"][0]["tool"] == "start_glossary_generation"
