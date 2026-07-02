from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
from urllib.parse import unquote, urlsplit

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


class _HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def read_daemon_info(state_dir: Path) -> dict:
    path = daemon_file(Path(state_dir))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


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

        return {
            "ok": True,
            "daemon": self._daemon_info(include_token=False),
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
                if self.headers.get(TOKEN_HEADER) != daemon.token:
                    self._send_json(401, {"ok": False, "error": "unauthorized"})
                    return

                try:
                    path = urlsplit(self.path).path
                    if method == "GET" and path == "/status":
                        self._send_json(200, daemon.status_payload())
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
                    self._send_json(exc.status, {"ok": False, "error": exc.message})
                except Exception as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})

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
            self._server = ThreadingHTTPServer((self.host, self.port), self._make_handler())
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
