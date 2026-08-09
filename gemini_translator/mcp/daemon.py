from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from queue import Empty, Queue
import secrets
import threading
import time
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .client_sessions import McpClientSession, list_active_client_sessions
from .ai_bridge import (
    GuiAiTaskTimeout,
    GuiAiTaskCancelled,
    cancel_gui_ai_task,
    claim_gui_ai_task,
    complete_gui_ai_task,
    create_gui_ai_task,
    fail_gui_ai_task,
    gui_ai_failure_details,
    list_gui_ai_tasks,
    task_public_payload,
    wait_for_gui_ai_task,
)
from .jobs import (
    create_job,
    list_jobs,
    load_job,
    mark_finished,
    redact_for_mcp,
    save_job,
    tail_log,
    utc_now,
)
from .paths import daemon_file, ensure_state_dirs, validate_job_id
from .worker import cancel_process, run_job

TOKEN_HEADER = "X-Translator-MCP-Token"
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
PIPELINE_METADATA_KEYS = {"pipeline_parent", "pipeline_step", "pipeline_index", "pipeline_total"}
SSE_KEEPALIVE_INTERVAL_SECONDS = 15.0
SSE_QUEUE_POLL_INTERVAL_SECONDS = 0.1


class _DaemonHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


class _HttpError(Exception):
    def __init__(self, status: int, message: str, payload: dict | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.payload = payload if isinstance(payload, dict) else {}


def read_daemon_info(state_dir: Path) -> dict:
    path = daemon_file(Path(state_dir))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


class _DaemonMcpClient:
    def __init__(self, daemon: "McpDaemon"):
        self.daemon = daemon

    def status(self):
        return self.daemon.status_payload()

    def enqueue(self, payload):
        return self.daemon.enqueue(payload)

    def get_job(self, job_id):
        return self.daemon.get_job_payload(job_id)

    def list_jobs(self):
        return self.daemon.list_jobs_payload()

    def cancel_job(self, job_id):
        return self.daemon.cancel(job_id)

    def list_gui_ai_tasks(self):
        return self.daemon.list_gui_ai_tasks_payload()

    def claim_gui_ai_task(self, task_id, client_name=""):
        return self.daemon.claim_gui_ai_task_payload(task_id, client_name)

    def submit_gui_ai_task_result(self, task_id, text):
        return self.daemon.submit_gui_ai_task_result_payload(task_id, text)

    def fail_gui_ai_task(self, task_id, error, error_payload=None):
        return self.daemon.fail_gui_ai_task_payload(task_id, error, error_payload)


class McpDaemon:
    def __init__(self, state_dir, *, host="127.0.0.1", port=0, concurrency=1):
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("MCP daemon must bind to a loopback host")

        self.state_dir = Path(state_dir)
        self.host = str(host)
        self.port = int(port)
        self.concurrency = max(1, int(concurrency))
        self.token = secrets.token_urlsafe(32)
        self.started_at = utc_now()
        self.active_threads: dict[str, threading.Thread] = {}

        self._lock = threading.RLock()
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._cancel_requested: set[str] = set()
        self._sse_sessions: dict[str, tuple[McpClientSession, Queue]] = {}
        self._pending_sse_requests: dict[str, Queue] = {}
        self._active_ai_request_cancellations: dict[str, threading.Event] = {}
        self._active_ai_request_tasks: dict[str, str] = {}
        # Отмена может прийти РАНЬШЕ самого запроса (гонка на стороне клиента:
        # CancelledError в воркере до того, как executor-поток отправил
        # /ai/completions). Запоминаем такие request_id и убиваем запоздавший
        # запрос сразу, иначе он создаст задачу-сироту и повиснет до таймаута.
        self._ai_cancel_tombstones: dict[str, float] = {}

    @property
    def base_url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        return f"http://{host}:{self.port}"

    def start_in_thread(self) -> None:
        self._ensure_server()
        if self._server_thread and self._server_thread.is_alive():
            return
        self._server_thread = threading.Thread(
            target=self.serve_forever,
            name="TranslatorMcpDaemon",
            daemon=True,
        )
        self._server_thread.start()

    def serve_forever(self) -> None:
        self._ensure_server()
        assert self._server is not None
        self._server.serve_forever(poll_interval=0.1)

    def stop(self) -> None:
        server = self._server
        thread = self._server_thread

        with self._lock:
            sse_sessions = list(self._sse_sessions.values())
            self._sse_sessions.clear()
            pending_queues = list(self._pending_sse_requests.values())
            self._pending_sse_requests.clear()
            active_cancellations = list(self._active_ai_request_cancellations.values())
            self._active_ai_request_cancellations.clear()
            active_task_ids = list(self._active_ai_request_tasks.values())
            self._active_ai_request_tasks.clear()

        for session, event_queue in sse_sessions:
            session.close()
            event_queue.put(None)
        for pending_queue in pending_queues:
            pending_queue.put({"error": {"message": "daemon stopped"}})
        for cancel_event in active_cancellations:
            cancel_event.set()
        for task_id in active_task_ids:
            try:
                cancel_gui_ai_task(self.state_dir, task_id, "daemon stopped")
            except (FileNotFoundError, ValueError, OSError):
                pass

        if server is not None:
            server.shutdown()
            server.server_close()

        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)

        with self._lock:
            self._server = None
            self._server_thread = None
            self._remove_daemon_info()

    def status_payload(self) -> dict:
        jobs = list_jobs(self.state_dir)
        counts: dict[str, int] = {}
        for job in jobs:
            counts[job.status] = counts.get(job.status, 0) + 1

        with self._lock:
            self._drop_finished_threads()
            active_jobs = sorted(self.active_threads)

        clients = list_active_client_sessions(self.state_dir)

        return {
            "ok": True,
            "daemon": self._daemon_info(include_token=False),
            "mcp_clients": {
                "connected": len(clients),
                "items": clients,
            },
            "queue": {
                "total": len(jobs),
                "counts": counts,
                "active": len(active_jobs),
                "queued": counts.get("queued", 0),
            },
            "active_jobs": active_jobs,
        }

    def enqueue(self, payload) -> dict:
        if not isinstance(payload, dict):
            raise _HttpError(400, "JSON object body is required")

        job_type = str(payload.get("job_type") or payload.get("type") or "")
        argv = payload.get("argv")
        self._validate_job_type(job_type)
        self._validate_argv(argv)

        if job_type == "pipeline":
            return self._enqueue_pipeline(payload)

        self._validate_ordinary_metadata(payload.get("metadata"))
        job = create_job(
            self.state_dir,
            job_type,
            argv,
            project=payload.get("project"),
            epub=payload.get("epub"),
            metadata=payload.get("metadata"),
            children=payload.get("children"),
        )
        self._start_available_jobs()
        return {"ok": True, "job": redact_for_mcp(job)}

    def _enqueue_pipeline(self, payload: dict) -> dict:
        metadata = self._validate_metadata_object(payload.get("metadata"))
        steps = metadata.get("steps", [])
        if not isinstance(steps, list):
            raise _HttpError(400, "metadata.steps must be a list")

        validated_steps = []
        for step in steps:
            if not isinstance(step, dict):
                raise _HttpError(400, "pipeline steps must be objects")
            step_job_type = str(step.get("job_type") or "")
            step_argv = step.get("argv")
            self._validate_job_type(step_job_type)
            self._validate_argv(step_argv)
            validated_steps.append((step, step_job_type, step_argv))

        with self._lock:
            parent = create_job(
                self.state_dir,
                "pipeline",
                [],
                project=payload.get("project"),
                epub=payload.get("epub"),
                metadata=metadata,
            )

            child_ids = []
            total = len(validated_steps)
            for index, (step, step_job_type, step_argv) in enumerate(validated_steps):
                child_metadata = {
                    "tool": step.get("tool"),
                    "pipeline_parent": parent.id,
                    "pipeline_step": step.get("name"),
                    "pipeline_index": index,
                    "pipeline_total": total,
                }
                child = create_job(
                    self.state_dir,
                    step_job_type,
                    step_argv,
                    project=payload.get("project"),
                    epub=payload.get("epub"),
                    metadata=child_metadata,
                )
                child_ids.append(child.id)

            parent.children = child_ids
            parent.status = "running"
            parent.started_at = parent.created_at
            save_job(self.state_dir, parent)
            self._refresh_pipeline_parent(parent.id)
            self._start_available_jobs()
            return {"ok": True, "job": redact_for_mcp(parent)}

    def get_job_payload(self, job_id) -> dict:
        job = self._load_existing_job(job_id)
        payload = {
            "ok": True,
            "job": redact_for_mcp(job),
            "stdout_tail": tail_log(job.stdout_path),
            "stderr_tail": tail_log(job.stderr_path),
        }

        result_path = Path(job.result_path)
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                payload["result"] = redact_for_mcp(result)
            except (json.JSONDecodeError, OSError) as exc:
                payload["result_error"] = str(exc)

        return payload

    def list_jobs_payload(self) -> dict:
        return {"ok": True, "jobs": [redact_for_mcp(job) for job in list_jobs(self.state_dir)]}

    def cancel(self, job_id) -> dict:
        job = self._load_existing_job(job_id)
        validate_job_id(job.id)

        with self._lock:
            if job.type == "pipeline":
                self._cancel_pipeline_children(job)
            self._cancel_job(job)
            parent_id = job.metadata.get("pipeline_parent")
            if parent_id:
                self._refresh_pipeline_parent(str(parent_id))
        return {"ok": True, "job": redact_for_mcp(job)}

    def list_gui_ai_tasks_payload(self) -> dict:
        tasks = self._active_gui_ai_tasks()
        return {"ok": True, "tasks": [task_public_payload(task) for task in tasks]}

    def claim_gui_ai_task_payload(self, task_id, client_name="") -> dict:
        if not self._is_active_gui_ai_task_id(str(task_id)):
            try:
                cancel_gui_ai_task(
                    self.state_dir,
                    str(task_id),
                    "GUI AI task has no active application request",
                )
            except (FileNotFoundError, ValueError, OSError):
                pass
            raise _HttpError(409, "GUI AI task is no longer active")
        task = claim_gui_ai_task(self.state_dir, str(task_id), str(client_name or "MCP client"))
        return {"ok": True, "task": task_public_payload(task, include_prompt=True)}

    def submit_gui_ai_task_result_payload(self, task_id, text) -> dict:
        task = complete_gui_ai_task(self.state_dir, str(task_id), str(text or ""))
        return {"ok": True, "task": task_public_payload(task)}

    def fail_gui_ai_task_payload(self, task_id, error, error_payload=None) -> dict:
        task = fail_gui_ai_task(
            self.state_dir,
            str(task_id),
            str(error or "AI client failed"),
            gui_ai_failure_details(error_payload),
        )
        return {"ok": True, "task": task_public_payload(task)}

    def _load_existing_job(self, job_id):
        try:
            return load_job(self.state_dir, validate_job_id(str(job_id)))
        except ValueError as exc:
            raise _HttpError(400, str(exc)) from exc
        except FileNotFoundError as exc:
            raise _HttpError(404, "job not found") from exc

    def _start_available_jobs(self) -> None:
        with self._lock:
            self._drop_finished_threads()
            slots = self.concurrency - len(self.active_threads)
            if slots <= 0:
                return

            queued_jobs = [
                job
                for job in sorted(list_jobs(self.state_dir), key=lambda item: item.created_at)
                if job.status == "queued" and job.id not in self.active_threads
            ]

            for job in queued_jobs:
                if slots <= 0:
                    return
                if not self._pipeline_allows_start(job):
                    continue
                thread = threading.Thread(
                    target=self._run_and_continue,
                    args=(job.id,),
                    name=f"TranslatorMcpJob-{job.id}",
                    daemon=True,
                )
                self.active_threads[job.id] = thread
                thread.start()
                slots -= 1

    def _pipeline_allows_start(self, job) -> bool:
        parent_id = job.metadata.get("pipeline_parent")
        if not parent_id:
            return True

        try:
            parent = load_job(self.state_dir, str(parent_id))
        except (FileNotFoundError, ValueError):
            mark_finished(job, status="failed", exit_code=None, error="Pipeline parent job not found")
            save_job(self.state_dir, job)
            return False
        if parent.status in TERMINAL_STATUSES:
            mark_finished(
                job,
                status="cancelled",
                exit_code=None,
                error=f"Skipped because pipeline parent is {parent.status}",
            )
            save_job(self.state_dir, job)
            return False

        try:
            current_index = int(job.metadata.get("pipeline_index") or 0)
        except (TypeError, ValueError):
            mark_finished(job, status="failed", exit_code=None, error="Invalid pipeline_index metadata")
            save_job(self.state_dir, job)
            self._refresh_pipeline_parent(parent.id)
            return False
        continue_on_error = bool(parent.metadata.get("continue_on_error"))
        siblings = []
        for child_id in parent.children:
            try:
                sibling = load_job(self.state_dir, child_id)
            except FileNotFoundError:
                continue
            siblings.append(sibling)

        prior_siblings = []
        for sibling in siblings:
            try:
                sibling_index = int(sibling.metadata.get("pipeline_index") or 0)
            except (TypeError, ValueError):
                continue
            if sibling_index < current_index:
                prior_siblings.append(sibling)
        for sibling in prior_siblings:
            if sibling.status not in TERMINAL_STATUSES:
                return False
            if sibling.status != "succeeded" and not continue_on_error:
                mark_finished(
                    job,
                    status="cancelled",
                    exit_code=None,
                    error=f"Skipped because pipeline step {sibling.id} did not succeed",
                )
                save_job(self.state_dir, job)
                self._refresh_pipeline_parent(parent.id)
                return False

        return True

    def _refresh_pipeline_parent(self, parent_id: str) -> None:
        try:
            parent = load_job(self.state_dir, str(parent_id))
        except FileNotFoundError:
            return
        if parent.type != "pipeline" or parent.status in TERMINAL_STATUSES:
            return

        children = []
        for child_id in parent.children:
            try:
                children.append(load_job(self.state_dir, child_id))
            except (FileNotFoundError, ValueError):
                return

        if not children and parent.children:
            return
        if all(child.status in TERMINAL_STATUSES for child in children):
            succeeded = all(child.status == "succeeded" for child in children)
            if succeeded:
                mark_finished(parent, status="succeeded", exit_code=0)
            else:
                mark_finished(
                    parent,
                    status="failed",
                    exit_code=1,
                    error="One or more pipeline steps did not succeed",
                )
            save_job(self.state_dir, parent)

    def _refresh_pipeline_parents(self) -> None:
        for job in list_jobs(self.state_dir):
            if job.type == "pipeline" and job.status not in TERMINAL_STATUSES:
                self._refresh_pipeline_parent(job.id)

    def _cancel_pipeline_children(self, parent) -> None:
        for child_id in parent.children:
            try:
                child = load_job(self.state_dir, child_id)
            except (FileNotFoundError, ValueError):
                continue
            if child.status not in TERMINAL_STATUSES:
                self._cancel_job(child)

    def _cancel_job(self, job) -> None:
        if job.status == "running" or job.id in self.active_threads:
            self._cancel_requested.add(job.id)
        if job.pid:
            cancel_process(int(job.pid))
        mark_finished(job, status="cancelled", exit_code=job.exit_code, error="Cancelled by MCP daemon")
        save_job(self.state_dir, job)

    def _drop_finished_threads(self) -> None:
        for job_id, thread in list(self.active_threads.items()):
            if not thread.is_alive():
                self.active_threads.pop(job_id, None)

    def _run_and_continue(self, job_id) -> None:
        try:
            run_job(self.state_dir, str(job_id))
        except Exception as exc:
            try:
                job = self._load_existing_job(job_id)
                mark_finished(job, status="failed", exit_code=None, error=str(exc))
                save_job(self.state_dir, job)
            except Exception:
                pass
        finally:
            with self._lock:
                self.active_threads.pop(str(job_id), None)
                if str(job_id) in self._cancel_requested:
                    self._cancel_requested.remove(str(job_id))
                    try:
                        job = self._load_existing_job(job_id)
                        if job.status != "cancelled":
                            mark_finished(
                                job,
                                status="cancelled",
                                exit_code=job.exit_code,
                                error="Cancelled by MCP daemon",
                            )
                            save_job(self.state_dir, job)
                    except Exception:
                        pass
                try:
                    job = self._load_existing_job(job_id)
                    parent_id = job.metadata.get("pipeline_parent")
                    if parent_id:
                        self._refresh_pipeline_parent(str(parent_id))
                except Exception:
                    pass
                self._start_available_jobs()

    def _validate_job_type(self, job_type: str) -> None:
        if not job_type:
            raise _HttpError(400, "job_type is required")
        if "/" in job_type or "\\" in job_type or job_type in {".", ".."}:
            raise _HttpError(400, "job_type is unsafe")

    def _validate_argv(self, argv) -> None:
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise _HttpError(400, "argv must be a list of strings")

    def _validate_ordinary_metadata(self, metadata) -> None:
        self._validate_metadata_object(metadata)

    def _validate_metadata_object(self, metadata) -> dict:
        if metadata is None:
            return {}
        if not isinstance(metadata, dict):
            raise _HttpError(400, "metadata must be an object")
        reserved = sorted(PIPELINE_METADATA_KEYS.intersection(metadata))
        if reserved:
            raise _HttpError(400, f"metadata.{reserved[0]} is reserved for pipeline jobs")
        return dict(metadata)

    def _register_sse_session(self, user_agent: str | None) -> tuple[McpClientSession, Queue]:
        client_name = (user_agent or "").strip()[:80] or "MCP SSE client"
        session = McpClientSession(self.state_dir, client_name=client_name, transport="sse")
        event_queue: Queue = Queue()
        with self._lock:
            self._sse_sessions[session.id] = (session, event_queue)
        return session, event_queue

    def _unregister_sse_session(self, session_id: str) -> None:
        with self._lock:
            entry = self._sse_sessions.pop(session_id, None)
        if entry is not None:
            session, _event_queue = entry
            session.close()

    def _handle_sse_json_rpc(self, session_id: str, request: dict) -> dict:
        with self._lock:
            entry = self._sse_sessions.get(session_id)
        if entry is None:
            raise _HttpError(404, "MCP SSE session not found")

        session, event_queue = entry
        if "method" not in request and "id" in request:
            request_id = str(request.get("id"))
            with self._lock:
                pending_queue = self._pending_sse_requests.get(request_id)
            if pending_queue is not None:
                pending_queue.put(request)
                session.touch("sampling/response")
                return {"ok": True}
            return {"ok": True, "ignored": True}

        from .server import McpStdioServer

        server = McpStdioServer(
            client_factory=lambda: _DaemonMcpClient(self),
            client_session=session,
        )
        response = server.handle_request(request)
        if response is not None:
            event_queue.put(response)
        return {"ok": True}

    def request_ai_completion(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise _HttpError(400, "JSON object body is required")
        prompt = str(payload.get("prompt") or "")
        if not prompt.strip():
            raise _HttpError(400, "prompt is required")
        try:
            timeout_sec = float(payload.get("timeout_sec") or 1800)
        except (TypeError, ValueError, OverflowError):
            timeout_sec = 1800
        timeout_sec = max(1.0, min(timeout_sec, 7200.0))
        request_id = self._completion_request_id(payload)
        cancel_event = threading.Event()

        with self._lock:
            self._prune_ai_cancel_tombstones()
            if self._ai_cancel_tombstones.pop(request_id, None) is not None:
                raise _HttpError(499, f"AI request cancelled before start: {request_id}")
            if request_id in self._active_ai_request_cancellations:
                raise _HttpError(409, f"AI completion request already active: {request_id}")
            self._active_ai_request_cancellations[request_id] = cancel_event
        try:
            sampling_session = self._select_sampling_sse_session()
            if sampling_session is None:
                clients = list_active_client_sessions(self.state_dir)
                if not clients:
                    raise _HttpError(409, "no MCP client is connected")
                return self._request_inbox_completion(
                    request_id,
                    payload,
                    timeout_sec=timeout_sec,
                    cancel_event=cancel_event,
                )
            return self._request_sse_sampling(sampling_session, payload, timeout_sec=timeout_sec, cancel_event=cancel_event)
        finally:
            with self._lock:
                self._active_ai_request_cancellations.pop(request_id, None)

    _AI_CANCEL_TOMBSTONE_TTL_SEC = 600.0

    def _prune_ai_cancel_tombstones(self, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        expired = [key for key, expiry in self._ai_cancel_tombstones.items() if expiry <= current]
        for key in expired:
            self._ai_cancel_tombstones.pop(key, None)

    def cancel_ai_completion_payload(self, request_id) -> dict:
        request_id = str(request_id or "")
        with self._lock:
            cancel_event = self._active_ai_request_cancellations.get(request_id)
            if cancel_event is None and request_id:
                # Запрос ещё не дошёл — оставляем tombstone, чтобы убить его
                # на входе, когда (если) он придёт.
                self._prune_ai_cancel_tombstones()
                self._ai_cancel_tombstones[request_id] = (
                    time.monotonic() + self._AI_CANCEL_TOMBSTONE_TTL_SEC
                )
        if cancel_event is None:
            return {"ok": True, "cancelled": False, "tombstoned": bool(request_id), "request_id": request_id}
        cancel_event.set()
        return {"ok": True, "cancelled": True, "request_id": request_id}

    def _completion_request_id(self, payload: dict) -> str:
        request_id = str(payload.get("request_id") or "").strip()
        return request_id or secrets.token_hex(16)

    def _request_inbox_completion(
        self,
        request_id: str,
        payload: dict,
        *,
        timeout_sec: float,
        cancel_event: threading.Event,
    ) -> dict:
        metadata = dict(payload.get("metadata") or {})
        metadata["request_id"] = str(request_id)
        with self._lock:
            task = create_gui_ai_task(
                self.state_dir,
                {
                    "prompt": str(payload.get("prompt") or ""),
                    "system_instruction": str(payload.get("system_instruction") or ""),
                    "metadata": metadata,
                },
            )
            self._active_ai_request_tasks[str(request_id)] = task.id
        try:
            result = wait_for_gui_ai_task(
                self.state_dir,
                task.id,
                timeout_sec=timeout_sec,
                cancel_event=cancel_event,
            )
        except GuiAiTaskCancelled as exc:
            cancel_gui_ai_task(self.state_dir, task.id, str(exc))
            raise _HttpError(499, "AI request cancelled") from exc
        except GuiAiTaskTimeout as exc:
            cancel_gui_ai_task(self.state_dir, task.id, str(exc))
            raise _HttpError(504, str(exc)) from exc
        finally:
            with self._lock:
                if self._active_ai_request_tasks.get(str(request_id)) == task.id:
                    self._active_ai_request_tasks.pop(str(request_id), None)
        if not result.get("ok"):
            details = {
                key: value
                for key, value in result.items()
                if key not in {"ok", "task_id", "error"} and value is not None
            }
            raise _HttpError(502, str(result.get("error") or "GUI AI task failed"), details)
        return {"ok": True, "transport": "inbox", "task_id": task.id, "text": str(result.get("text") or "")}

    def _active_gui_ai_task_ids(self) -> set[str]:
        with self._lock:
            return {str(task_id) for task_id in self._active_ai_request_tasks.values()}

    def _is_active_gui_ai_task_id(self, task_id: str) -> bool:
        return str(task_id) in self._active_gui_ai_task_ids()

    def _active_gui_ai_tasks(self):
        with self._lock:
            active_task_ids = {str(task_id) for task_id in self._active_ai_request_tasks.values()}
            tasks = list_gui_ai_tasks(self.state_dir, include_terminal=False)
            for task in tasks:
                if task.id in active_task_ids:
                    continue
                try:
                    cancel_gui_ai_task(
                        self.state_dir,
                        task.id,
                        "GUI AI task has no active application request",
                    )
                except (FileNotFoundError, ValueError, OSError):
                    pass
            return [task for task in tasks if task.id in active_task_ids]

    def _select_sampling_sse_session(self) -> tuple[str, McpClientSession, Queue] | None:
        with self._lock:
            for session_id, (session, event_queue) in self._sse_sessions.items():
                if getattr(session, "supports_sampling", False):
                    return session_id, session, event_queue
        return None

    def _request_sse_sampling(
        self,
        session_entry: tuple[str, McpClientSession, Queue],
        payload: dict,
        *,
        timeout_sec: float,
        cancel_event: threading.Event,
    ) -> dict:
        _session_id, session, event_queue = session_entry
        request_id = f"sampling_{secrets.token_hex(8)}"
        pending_queue: Queue = Queue(maxsize=1)
        params = self._sampling_params(payload)
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "sampling/createMessage",
            "params": params,
        }
        with self._lock:
            self._pending_sse_requests[request_id] = pending_queue
        try:
            session.touch("sampling/createMessage")
            event_queue.put(message)
            deadline = time.monotonic() + timeout_sec
            while True:
                if cancel_event.is_set():
                    raise _HttpError(499, "AI request cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _HttpError(504, "sampling request timed out")
                try:
                    response = pending_queue.get(timeout=min(0.1, remaining))
                    break
                except Empty:
                    continue
        finally:
            with self._lock:
                self._pending_sse_requests.pop(request_id, None)

        if not isinstance(response, dict):
            raise _HttpError(502, "invalid sampling response")
        if response.get("error"):
            error = response.get("error")
            details = {}
            if isinstance(error, dict):
                message_text = error.get("message") or error.get("code") or "sampling error"
                details = gui_ai_failure_details(error)
                data = error.get("data")
                if isinstance(data, dict):
                    details.update(gui_ai_failure_details(data))
            else:
                message_text = str(error)
            raise _HttpError(502, str(message_text), details)
        result = response.get("result")
        text = self._extract_sampling_text(result)
        if not text.strip():
            raise _HttpError(502, "sampling response did not contain text")
        return {"ok": True, "transport": "sampling", "text": text}

    def _sampling_params(self, payload: dict) -> dict:
        prompt = str(payload.get("prompt") or "")
        params = {
            "messages": [
                {
                    "role": "user",
                    "content": {"type": "text", "text": prompt},
                }
            ],
        }
        system_instruction = str(payload.get("system_instruction") or "").strip()
        if system_instruction:
            params["systemPrompt"] = system_instruction
        max_tokens = payload.get("max_output_tokens")
        try:
            max_tokens_value = int(max_tokens)
        except (TypeError, ValueError, OverflowError):
            max_tokens_value = 0
        if max_tokens_value > 0:
            params["maxTokens"] = max_tokens_value
        if payload.get("temperature") is not None:
            try:
                params["temperature"] = float(payload.get("temperature"))
            except (TypeError, ValueError, OverflowError):
                pass
        return params

    def _extract_sampling_text(self, result) -> str:
        if isinstance(result, str):
            return result
        if not isinstance(result, dict):
            return ""
        content = result.get("content")
        if isinstance(content, dict):
            if content.get("type") == "text" or "text" in content:
                return str(content.get("text") or "")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and (item.get("type") == "text" or "text" in item):
                    parts.append(str(item.get("text") or ""))
            return "\n".join(part for part in parts if part)
        if result.get("text") is not None:
            return str(result.get("text") or "")
        return ""

    def _make_handler(self):
        daemon = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "TranslatorMCP/0.1"

            def do_GET(self) -> None:
                self._dispatch("GET")

            def do_POST(self) -> None:
                self._dispatch("POST")

            def log_message(self, format, *args) -> None:
                return

            def _dispatch(self, method: str) -> None:
                try:
                    parsed = urlsplit(self.path)
                    path = parsed.path
                    if method == "GET" and path == "/sse":
                        self._serve_sse()
                        return
                    if method == "POST" and path in {"/messages", "/messages/", "/message"}:
                        self._handle_sse_message(parsed)
                        return

                    if self.headers.get(TOKEN_HEADER) != daemon.token:
                        self._send_json(401, {"ok": False, "error": "unauthorized"})
                        return

                    if method == "GET" and path == "/status":
                        self._send_json(200, daemon.status_payload())
                    elif method == "POST" and path == "/ai/completions":
                        self._send_json(200, daemon.request_ai_completion(self._read_json_body()))
                    elif method == "POST" and path.startswith("/ai/completions/") and path.endswith("/cancel"):
                        self._send_json(
                            200,
                            daemon.cancel_ai_completion_payload(
                                self._ai_completion_request_id_from_path(path, suffix="/cancel"),
                            ),
                        )
                    elif method == "GET" and path == "/gui-ai-tasks":
                        self._send_json(200, daemon.list_gui_ai_tasks_payload())
                    elif method == "POST" and path.startswith("/gui-ai-tasks/") and path.endswith("/claim"):
                        body = self._read_json_body()
                        self._send_json(
                            200,
                            daemon.claim_gui_ai_task_payload(
                                self._gui_ai_task_id_from_path(path, suffix="/claim"),
                                body.get("client_name", ""),
                            ),
                        )
                    elif method == "POST" and path.startswith("/gui-ai-tasks/") and path.endswith("/complete"):
                        body = self._read_json_body()
                        self._send_json(
                            200,
                            daemon.submit_gui_ai_task_result_payload(
                                self._gui_ai_task_id_from_path(path, suffix="/complete"),
                                body.get("text", ""),
                            ),
                        )
                    elif method == "POST" and path.startswith("/gui-ai-tasks/") and path.endswith("/fail"):
                        body = self._read_json_body()
                        self._send_json(
                            200,
                            daemon.fail_gui_ai_task_payload(
                                self._gui_ai_task_id_from_path(path, suffix="/fail"),
                                body.get("error", ""),
                                body,
                            ),
                        )
                    elif method == "POST" and path == "/jobs":
                        self._send_json(201, daemon.enqueue(self._read_json_body()))
                    elif method == "GET" and path == "/jobs":
                        self._send_json(200, daemon.list_jobs_payload())
                    elif method == "GET" and path.startswith("/jobs/"):
                        self._send_json(200, daemon.get_job_payload(self._job_id_from_path(path)))
                    elif method == "POST" and path.startswith("/jobs/") and path.endswith("/cancel"):
                        self._send_json(200, daemon.cancel(self._job_id_from_path(path, suffix="/cancel")))
                    elif method == "POST" and path == "/shutdown":
                        self._send_json(200, {"ok": True})
                        threading.Thread(target=daemon.stop, name="TranslatorMcpShutdown", daemon=True).start()
                    else:
                        self._send_json(404, {"ok": False, "error": "not found"})
                except _HttpError as exc:
                    payload = {"ok": False, "error": exc.message}
                    payload.update(exc.payload)
                    payload["error"] = exc.message
                    self._send_json(exc.status, payload)
                except Exception as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})

            def _serve_sse(self) -> None:
                session, event_queue = daemon._register_sse_session(self.headers.get("User-Agent"))
                endpoint = f"{daemon.base_url}/messages?session_id={quote(session.id, safe='')}"
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    self._write_sse_event("endpoint", endpoint)

                    next_keepalive_at = time.monotonic() + SSE_KEEPALIVE_INTERVAL_SECONDS
                    while True:
                        try:
                            payload = event_queue.get(timeout=SSE_QUEUE_POLL_INTERVAL_SECONDS)
                        except Empty:
                            now = time.monotonic()
                            if now < next_keepalive_at:
                                continue
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                            next_keepalive_at = now + SSE_KEEPALIVE_INTERVAL_SECONDS
                            continue
                        if payload is None:
                            break
                        self._write_sse_event(
                            "message",
                            json.dumps(payload, ensure_ascii=False),
                        )
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    daemon._unregister_sse_session(session.id)

            def _handle_sse_message(self, parsed) -> None:
                params = parse_qs(parsed.query)
                session_id = (params.get("session_id") or params.get("sessionId") or [""])[0]
                if not session_id:
                    raise _HttpError(400, "session_id is required")
                payload = self._read_json_body()
                if not isinstance(payload, dict):
                    raise _HttpError(400, "JSON object body is required")
                self._send_json(202, daemon._handle_sse_json_rpc(session_id, payload))

            def _write_sse_event(self, event: str, data: str) -> None:
                self.wfile.write(f"event: {event}\n".encode("utf-8"))
                lines = str(data).splitlines() or [""]
                for line in lines:
                    self.wfile.write(f"data: {line}\n".encode("utf-8"))
                self.wfile.write(b"\n")
                self.wfile.flush()

            def _read_json_body(self):
                length = int(self.headers.get("Content-Length") or "0")
                if length <= 0:
                    return {}
                body = self.rfile.read(length).decode("utf-8")
                try:
                    return json.loads(body)
                except json.JSONDecodeError as exc:
                    raise _HttpError(400, f"invalid JSON: {exc}") from exc

            def _job_id_from_path(self, path: str, *, suffix: str = "") -> str:
                if suffix:
                    path = path[: -len(suffix)]
                prefix = "/jobs/"
                if not path.startswith(prefix):
                    raise _HttpError(404, "not found")
                job_id = unquote(path[len(prefix) :])
                try:
                    return validate_job_id(job_id)
                except ValueError as exc:
                    raise _HttpError(400, str(exc)) from exc

            def _gui_ai_task_id_from_path(self, path: str, *, suffix: str = "") -> str:
                if suffix:
                    path = path[: -len(suffix)]
                prefix = "/gui-ai-tasks/"
                if not path.startswith(prefix):
                    raise _HttpError(404, "not found")
                task_id = unquote(path[len(prefix) :])
                try:
                    return validate_job_id(task_id)
                except ValueError as exc:
                    raise _HttpError(400, str(exc)) from exc

            def _ai_completion_request_id_from_path(self, path: str, *, suffix: str = "") -> str:
                if suffix:
                    path = path[: -len(suffix)]
                prefix = "/ai/completions/"
                if not path.startswith(prefix):
                    raise _HttpError(404, "not found")
                request_id = unquote(path[len(prefix) :]).strip()
                if not request_id:
                    raise _HttpError(400, "request_id is required")
                return request_id

            def _send_json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler

    def _ensure_server(self) -> None:
        with self._lock:
            if self._server is not None:
                return

            ensure_state_dirs(self.state_dir)
            self._server = _DaemonHTTPServer((self.host, self.port), self._make_handler())
            self.host = str(self._server.server_address[0])
            self.port = int(self._server.server_address[1])
            self.started_at = utc_now()
            self._write_daemon_info()
            self._refresh_pipeline_parents()
            self._start_available_jobs()

    def _daemon_info(self, *, include_token: bool = True) -> dict:
        info = {
            "pid": os.getpid(),
            "host": self.host,
            "port": self.port,
            "started_at": self.started_at,
        }
        if include_token:
            info["token"] = self.token
        return info

    def _write_daemon_info(self) -> None:
        path = daemon_file(self.state_dir)
        payload = json.dumps(self._daemon_info(), ensure_ascii=False, indent=2)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        if os.name != "nt":
            path.chmod(0o600)

    def _remove_daemon_info(self) -> None:
        try:
            daemon_file(self.state_dir).unlink()
        except FileNotFoundError:
            pass
