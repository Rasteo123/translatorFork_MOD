from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, Callable

from .client import ensure_daemon_process
from .commands import CommandBuildError, build_cli_command
from .jobs import redact_for_mcp

PROTOCOL_VERSION = "2025-06-18"

TOOL_NAMES = [
    "translator_status",
    "start_translation",
    "start_glossary_generation",
    "start_glossary_review_or_correction",
    "start_untranslated_fix",
    "start_consistency_check",
    "start_epub_build",
    "start_full_pipeline",
    "get_job_status",
    "list_jobs",
    "cancel_job",
    "install_mcp_client",
    "print_mcp_config",
]

PIPELINE_STEP_TO_TOOL = {
    "glossary": "start_glossary_generation",
    "translation": "start_translation",
    "untranslated_fix": "start_untranslated_fix",
    "consistency": "start_consistency_check",
    "epub_build": "start_epub_build",
}

DEFAULT_PIPELINE_STEPS = ["translation", "untranslated_fix", "consistency", "epub_build"]


def _schema(properties: dict[str, dict[str, Any]], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required or []),
        "additionalProperties": True,
    }


COMMON_START_PROPERTIES = {
    "epub": {"type": "string", "description": "Path to the source EPUB."},
    "project": {"type": "string", "description": "Path to the translator project directory."},
    "chapters": {"type": ["integer", "string"], "description": "Maximum number of chapters to process."},
    "chapter": {
        "type": ["array", "integer", "string"],
        "description": "Specific chapter number or numbers to process.",
    },
    "offset": {"type": ["integer", "string"], "description": "Chapter offset for batch processing."},
    "limit": {"type": ["integer", "string"], "description": "Chapter limit for batch processing."},
    "provider": {"type": "string", "description": "AI provider name."},
    "model": {"type": "string", "description": "Provider model name."},
    "api_key": {"type": ["array", "string"], "description": "Provider API key or keys."},
    "api_key_file": {"type": "string", "description": "Path to a file containing API keys."},
    "all_keys": {"type": "boolean", "description": "Use all configured API keys."},
    "workers": {"type": ["integer", "string"], "description": "Worker count."},
    "rpm": {"type": ["integer", "string"], "description": "Requests per minute limit."},
    "temperature": {"type": ["number", "string"], "description": "Generation temperature."},
    "mode": {"type": "string", "description": "Translation or processing mode."},
    "task_size": {"type": ["integer", "string"], "description": "Task size for generated batches."},
    "splits": {"type": ["integer", "string"], "description": "Split count for generated batches."},
    "force_accept": {"type": "boolean", "description": "Accept generated output without interactive review."},
    "json_epub": {"type": "boolean", "description": "Use JSON EPUB project data."},
    "prompt_file": {"type": "string", "description": "Path to a prompt file."},
    "glossary": {"type": "string", "description": "Path to glossary data."},
    "settings_json": {"type": "string", "description": "Path to settings JSON."},
    "settings_profile": {"type": "string", "description": "Named settings profile."},
    "settings_dir": {"type": "string", "description": "Settings directory."},
}


def _start_properties(*extra: tuple[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    properties = dict(COMMON_START_PROPERTIES)
    properties.update(dict(extra))
    return properties


TOOL_DEFINITIONS = [
    {
        "name": "translator_status",
        "description": "Return daemon status and queue counts for translatorFork.",
        "inputSchema": _schema({}),
    },
    {
        "name": "start_translation",
        "description": "Start a headless EPUB translation job through the local daemon.",
        "inputSchema": _schema(
            _start_properties(
                ("timeout", {"type": ["number", "string"], "description": "Request timeout."}),
                ("verbose", {"type": "boolean", "description": "Enable verbose CLI logging."}),
            ),
            required=["epub", "project"],
        ),
    },
    {
        "name": "start_glossary_generation",
        "description": "Generate glossary terms for a translator project.",
        "inputSchema": _schema(
            _start_properties(
                ("batch_size", {"type": ["integer", "string"], "description": "Glossary batch size."}),
                ("merge_mode", {"type": "string", "description": "Glossary merge mode."}),
                ("new_terms_limit", {"type": ["integer", "string"], "description": "Limit for new terms."}),
                ("glossary_prompt_file", {"type": "string", "description": "Glossary prompt file."}),
                ("timeout", {"type": ["number", "string"], "description": "Request timeout."}),
                ("verbose", {"type": "boolean", "description": "Enable verbose CLI logging."}),
            ),
            required=["epub", "project"],
        ),
    },
    {
        "name": "start_glossary_review_or_correction",
        "description": "Request glossary review or correction; currently reports unsupported headless status.",
        "inputSchema": _schema(COMMON_START_PROPERTIES, required=["epub", "project"]),
    },
    {
        "name": "start_untranslated_fix",
        "description": "Find and fix untranslated fragments in a translator project.",
        "inputSchema": _schema(
            _start_properties(
                ("suffix", {"type": "string", "description": "Output suffix."}),
                ("exceptions", {"type": "string", "description": "Path to exception rules."}),
                ("fix_prompt_file", {"type": "string", "description": "Fix prompt file."}),
                ("batch_size", {"type": ["integer", "string"], "description": "Fix batch size."}),
                ("max_context_chars", {"type": ["integer", "string"], "description": "Maximum context length."}),
                ("dry_run", {"type": "boolean", "description": "Prepare without writing changes."}),
                ("timeout", {"type": ["number", "string"], "description": "Request timeout."}),
                ("verbose", {"type": "boolean", "description": "Enable verbose CLI logging."}),
            ),
            required=["epub", "project"],
        ),
    },
    {
        "name": "start_consistency_check",
        "description": "Run consistency checking and optional fix/write actions for a project.",
        "inputSchema": _schema(
            _start_properties(
                ("suffix", {"type": "string", "description": "Output suffix."}),
                ("consistency_mode", {"type": "string", "description": "Consistency mode."}),
                ("glossary_first", {"type": "boolean", "description": "Run glossary checks first."}),
                ("chunk_size", {"type": ["integer", "string"], "description": "Consistency chunk size."}),
                ("no_source", {"type": "boolean", "description": "Skip source text in prompts."}),
                ("fix", {"type": "boolean", "description": "Generate fixes."}),
                ("write", {"type": "boolean", "description": "Write fixes to disk."}),
                ("confidences", {"type": ["array", "string"], "description": "Confidence filters."}),
            ),
            required=["epub", "project"],
        ),
    },
    {
        "name": "start_epub_build",
        "description": "Build an EPUB from an existing translator project.",
        "inputSchema": _schema(
            _start_properties(
                ("output", {"type": "string", "description": "Output EPUB path."}),
                ("suffix", {"type": "string", "description": "Output suffix."}),
                ("strict", {"type": "boolean", "description": "Enable strict build validation."}),
            ),
            required=["epub", "project"],
        ),
    },
    {
        "name": "start_full_pipeline",
        "description": "Start the future glossary, translation, checks, and EPUB build pipeline.",
        "inputSchema": _schema(
            _start_properties(
                ("steps", {"type": "array", "description": "Pipeline steps to run in order."}),
                ("continue_on_error", {"type": "boolean", "description": "Run later pipeline steps after failures."}),
            ),
            required=["epub", "project"],
        ),
    },
    {
        "name": "get_job_status",
        "description": "Return daemon job status, result metadata, and recent logs.",
        "inputSchema": _schema(
            {"job_id": {"type": "string", "description": "Daemon job id."}},
            required=["job_id"],
        ),
    },
    {
        "name": "list_jobs",
        "description": "List daemon jobs known to the current MCP state directory.",
        "inputSchema": _schema({}),
    },
    {
        "name": "cancel_job",
        "description": "Request cancellation for a daemon job.",
        "inputSchema": _schema(
            {"job_id": {"type": "string", "description": "Daemon job id."}},
            required=["job_id"],
        ),
    },
    {
        "name": "install_mcp_client",
        "description": "Install or update a desktop AI client MCP configuration.",
        "inputSchema": _schema(
            {
                "client": {"type": "string", "description": "Target MCP client name."},
                "mode": {"type": "string", "description": "Install mode: auto, print, or write."},
                "config_path": {"type": "string", "description": "Client configuration file path for safe writes."},
                "server_name": {"type": "string", "description": "MCP server name to write in client config."},
                "state_dir": {"type": "string", "description": "Optional MCP state directory."},
            }
        ),
    },
    {
        "name": "print_mcp_config",
        "description": "Print MCP client configuration JSON for manual installation.",
        "inputSchema": _schema(
            {
                "client": {"type": "string", "description": "Target MCP client name."},
                "server_name": {"type": "string", "description": "MCP server name to include in client config."},
                "state_dir": {"type": "string", "description": "Optional MCP state directory."},
            }
        ),
    },
]


class McpStdioServer:
    def __init__(self, client_factory: Callable[[], Any]):
        self._client_factory = client_factory

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(request, dict):
            return self._error(None, -32600, "Invalid Request")
        if "id" not in request:
            return None

        request_id = request.get("id")
        method = request.get("method")

        try:
            if method == "initialize":
                return self._response(request_id, self._initialize())
            if method == "ping":
                return self._response(request_id, {})
            if method == "tools/list":
                return self._response(request_id, {"tools": TOOL_DEFINITIONS})
            if method == "tools/call":
                params = request.get("params") or {}
                if not isinstance(params, dict):
                    return self._error(request_id, -32602, "Invalid params")
                try:
                    result = self._call_tool(params.get("name"), params.get("arguments") or {})
                except Exception as exc:
                    result = self._tool_exception_result(exc)
                return self._response(request_id, result)
            return self._error(request_id, -32601, f"Method not found: {method}")
        except Exception as exc:
            return self._error(request_id, -32603, str(exc))

    def _initialize(self) -> dict[str, Any]:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "translatorFork", "version": "0.1.0"},
        }

    def _call_tool(self, name: str | None, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            return self._tool_result({"ok": False, "error": "arguments must be an object"}, is_error=True)
        if name not in TOOL_NAMES:
            return self._tool_result({"ok": False, "error": f"Unknown tool: {name}"}, is_error=True)

        if name == "translator_status":
            return self._tool_result(self._client().status())
        if name == "get_job_status":
            job_id = arguments.get("job_id")
            if not job_id:
                return self._tool_result({"ok": False, "error": "job_id is required"}, is_error=True)
            return self._tool_result(self._client().get_job(job_id))
        if name == "list_jobs":
            return self._tool_result(self._client().list_jobs())
        if name == "cancel_job":
            job_id = arguments.get("job_id")
            if not job_id:
                return self._tool_result({"ok": False, "error": "job_id is required"}, is_error=True)
            return self._tool_result(self._client().cancel_job(job_id))
        if name in {"install_mcp_client", "print_mcp_config"}:
            return self._call_client_install_tool(name, arguments)
        if name == "start_full_pipeline":
            try:
                payload = build_pipeline_payload(arguments)
            except CommandBuildError as exc:
                return self._tool_result({"ok": False, "error": str(exc)}, is_error=True)
            return self._tool_result(self._client().enqueue(payload))

        try:
            command = build_cli_command(name, arguments)
        except CommandBuildError as exc:
            return self._tool_result({"ok": False, "error": str(exc)}, is_error=True)

        payload = {
            "job_type": command.job_type,
            "argv": command.argv,
            "project": command.project,
            "epub": command.epub,
            "metadata": command.metadata,
        }
        if command.metadata.get("unsupported_in_this_build"):
            payload["ok"] = False
            return self._tool_result(payload, is_error=True)
        return self._tool_result(self._client().enqueue(payload))

    def _client(self):
        return self._client_factory()

    def _call_client_install_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            from .client_install import handle_install_tool
        except ModuleNotFoundError as exc:
            if exc.name == "gemini_translator.mcp.client_install":
                return self._tool_result(
                    {
                        "ok": False,
                        "error": "client_install_unavailable",
                        "reason": "MCP client installation tools are implemented by Task 8.",
                    },
                    is_error=True,
                )
            raise

        payload = handle_install_tool(name, arguments)
        return self._tool_result(
            payload,
            is_error=isinstance(payload, dict) and payload.get("ok") is False,
        )

    def _tool_exception_result(self, exc: Exception) -> dict[str, Any]:
        return self._tool_result(
            {
                "ok": False,
                "error": str(exc) or type(exc).__name__,
                "error_type": type(exc).__name__,
            },
            is_error=True,
        )

    def _tool_result(self, payload, is_error: bool = False) -> dict[str, Any]:
        text = json.dumps(redact_for_mcp(payload), ensure_ascii=False, indent=2, sort_keys=True)
        return {
            "content": [{"type": "text", "text": text}],
            "isError": bool(is_error),
        }

    def _response(self, request_id, result) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _error(self, request_id, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": int(code), "message": str(message)},
        }


def build_pipeline_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    requested_steps = arguments.get("steps", DEFAULT_PIPELINE_STEPS)
    if not isinstance(requested_steps, list):
        raise CommandBuildError("steps must be a list of pipeline step names")

    step_args = {key: value for key, value in arguments.items() if key != "steps"}
    steps = []
    for step in requested_steps:
        if not isinstance(step, str) or not step:
            raise CommandBuildError("pipeline step names must be non-empty strings")
        tool_name = PIPELINE_STEP_TO_TOOL.get(step)
        if tool_name is None:
            raise CommandBuildError(f"Unsupported pipeline step: {step}")
        command = build_cli_command(tool_name, step_args)
        steps.append(
            {
                "name": step,
                "tool": tool_name,
                "job_type": command.job_type,
                "argv": command.argv,
            }
        )

    return {
        "job_type": "pipeline",
        "argv": [],
        "project": arguments.get("project"),
        "epub": arguments.get("epub"),
        "metadata": {
            "tool": "start_full_pipeline",
            "steps": steps,
            "continue_on_error": bool(arguments.get("continue_on_error")),
        },
    }


def run_stdio_server(state_dir: Path) -> None:
    server = McpStdioServer(client_factory=lambda: ensure_daemon_process(state_dir))
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = server.handle_request(request)
        except json.JSONDecodeError as exc:
            response = server._error(None, -32700, f"Parse error: {exc}")

        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


__all__ = [
    "McpStdioServer",
    "PROTOCOL_VERSION",
    "TOOL_DEFINITIONS",
    "TOOL_NAMES",
    "run_stdio_server",
]
