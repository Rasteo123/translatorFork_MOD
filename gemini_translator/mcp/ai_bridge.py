from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import time
from typing import Any

from .paths import ensure_state_dirs, validate_job_id


TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}
GUI_AI_FAILURE_DETAIL_KEYS = {
    "retry_after_seconds",
    "reset_after_seconds",
    "reset_in_seconds",
    "delay_seconds",
    "retry_after",
    "reset_after",
    "window_seconds",
    "limit_window_seconds",
    "quota_window_seconds",
    "window_duration_seconds",
    "error_type",
    "code",
    "status",
    "reason",
    "type",
}


class GuiAiTaskTimeout(TimeoutError):
    pass


class GuiAiTaskCancelled(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_gui_ai_task_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return validate_job_id(f"gui_ai_{stamp}_{secrets.token_hex(4)}")


@dataclass
class GuiAiTask:
    id: str
    status: str
    created_at: str
    updated_at: str
    prompt: str
    system_instruction: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    result_text: str = ""
    error: str = ""
    error_payload: dict[str, Any] = field(default_factory=dict)
    claimed_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GuiAiTask":
        return cls(
            id=validate_job_id(str(payload["id"])),
            status=str(payload["status"]),
            created_at=str(payload["created_at"]),
            updated_at=str(payload.get("updated_at") or payload["created_at"]),
            prompt=str(payload.get("prompt") or ""),
            system_instruction=str(payload.get("system_instruction") or ""),
            metadata=dict(payload.get("metadata") or {}),
            result_text=str(payload.get("result_text") or ""),
            error=str(payload.get("error") or ""),
            error_payload=dict(payload.get("error_payload") or {}),
            claimed_by=str(payload.get("claimed_by") or ""),
        )


def gui_ai_tasks_dir(state_dir: Path) -> Path:
    root = ensure_state_dirs(Path(state_dir))
    directory = root / "gui_ai_tasks"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def gui_ai_task_path(state_dir: Path, task_id: str) -> Path:
    return gui_ai_tasks_dir(state_dir) / f"{validate_job_id(str(task_id))}.json"


def save_gui_ai_task(state_dir: Path, task: GuiAiTask) -> None:
    path = gui_ai_task_path(state_dir, task.id)
    payload = json.dumps(task.to_dict(), ensure_ascii=False, indent=2)
    temp_path = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temp_path.write_text(payload, encoding="utf-8")
        if temp_path.exists() and temp_path.stat().st_mode:
            try:
                temp_path.chmod(0o600)
            except OSError:
                pass
        temp_path.replace(path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def load_gui_ai_task(state_dir: Path, task_id: str) -> GuiAiTask:
    payload = json.loads(gui_ai_task_path(state_dir, task_id).read_text(encoding="utf-8"))
    return GuiAiTask.from_dict(payload)


def list_gui_ai_tasks(state_dir: Path, *, include_terminal: bool = True) -> list[GuiAiTask]:
    directory = gui_ai_tasks_dir(state_dir)
    tasks: list[GuiAiTask] = []
    for path in sorted(directory.glob("*.json")):
        try:
            task = GuiAiTask.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            continue
        if include_terminal or task.status not in TERMINAL_TASK_STATUSES:
            tasks.append(task)
    return sorted(tasks, key=lambda item: item.created_at)


def create_gui_ai_task(state_dir: Path, payload: dict[str, Any]) -> GuiAiTask:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    prompt = str(payload.get("prompt") or "")
    if not prompt.strip():
        raise ValueError("prompt is required")
    now = utc_now()
    task = GuiAiTask(
        id=new_gui_ai_task_id(),
        status="pending",
        created_at=now,
        updated_at=now,
        prompt=prompt,
        system_instruction=str(payload.get("system_instruction") or ""),
        metadata=dict(payload.get("metadata") or {}),
    )
    save_gui_ai_task(state_dir, task)
    return task


def claim_gui_ai_task(state_dir: Path, task_id: str, client_name: str) -> GuiAiTask:
    task = load_gui_ai_task(state_dir, task_id)
    if task.status in TERMINAL_TASK_STATUSES:
        raise ValueError(f"cannot claim {task.status} task")
    task.status = "claimed"
    task.claimed_by = str(client_name or "MCP client")[:120]
    task.updated_at = utc_now()
    save_gui_ai_task(state_dir, task)
    return task


def complete_gui_ai_task(state_dir: Path, task_id: str, text: str) -> GuiAiTask:
    task = load_gui_ai_task(state_dir, task_id)
    if task.status in TERMINAL_TASK_STATUSES:
        raise ValueError(f"cannot complete {task.status} task")
    result_text = str(text or "")
    if not result_text.strip():
        raise ValueError("result text is required")
    task.status = "completed"
    task.result_text = result_text
    task.error = ""
    task.updated_at = utc_now()
    save_gui_ai_task(state_dir, task)
    return task


def gui_ai_failure_details(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    details = {
        key: value
        for key, value in payload.items()
        if key in GUI_AI_FAILURE_DETAIL_KEYS and value is not None
    }
    data = payload.get("data")
    if isinstance(data, dict):
        details["data"] = data
    return details


def fail_gui_ai_task(
    state_dir: Path,
    task_id: str,
    error: str,
    error_payload: dict[str, Any] | None = None,
) -> GuiAiTask:
    task = load_gui_ai_task(state_dir, task_id)
    if task.status in TERMINAL_TASK_STATUSES:
        raise ValueError(f"cannot fail {task.status} task")
    task.status = "failed"
    task.error = str(error or "AI client failed")
    task.error_payload = gui_ai_failure_details(error_payload)
    task.updated_at = utc_now()
    save_gui_ai_task(state_dir, task)
    return task


def cancel_gui_ai_task(state_dir: Path, task_id: str, reason: str = "AI request cancelled") -> GuiAiTask:
    task = load_gui_ai_task(state_dir, task_id)
    if task.status in TERMINAL_TASK_STATUSES:
        return task
    task.status = "cancelled"
    task.error = str(reason or "AI request cancelled")
    task.updated_at = utc_now()
    save_gui_ai_task(state_dir, task)
    return task


def wait_for_gui_ai_task(
    state_dir: Path,
    task_id: str,
    *,
    timeout_sec: float,
    poll_interval: float = 0.1,
    cancel_event=None,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, float(timeout_sec))
    task_file = gui_ai_task_path(state_dir, task_id)
    last_mtime_ns = None
    interval = max(0.001, float(poll_interval))
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise GuiAiTaskCancelled(f"GUI AI task cancelled: {task_id}")
        # Задача может выполняться минуты: перечитываем и парсим JSON только
        # когда файл реально изменился, а не 10 раз в секунду.
        try:
            mtime_ns = task_file.stat().st_mtime_ns
        except OSError:
            mtime_ns = None
        if last_mtime_ns is None or mtime_ns is None or mtime_ns != last_mtime_ns:
            last_mtime_ns = mtime_ns
            task = load_gui_ai_task(state_dir, task_id)
            if task.status == "completed":
                return {"ok": True, "task_id": task.id, "text": task.result_text}
            if task.status in {"failed", "cancelled"}:
                payload = {
                    "ok": False,
                    "task_id": task.id,
                    "error": task.error or f"task {task.status}",
                }
                payload.update(task.error_payload)
                payload["error"] = task.error or str(payload.get("error") or f"task {task.status}")
                return payload
        if time.monotonic() >= deadline:
            raise GuiAiTaskTimeout(f"GUI AI task timed out: {task_id}")
        time.sleep(interval)
        # Плавный откат к 1 сек: быстрые задачи ловим быстро, долгие не поллим.
        interval = min(interval * 1.5, 1.0)


def task_public_payload(task: GuiAiTask, *, include_prompt: bool = False) -> dict[str, Any]:
    payload = {
        "id": task.id,
        "status": task.status,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "claimed_by": task.claimed_by,
        "metadata": task.metadata,
    }
    if include_prompt:
        payload["prompt"] = task.prompt
        payload["system_instruction"] = task.system_instruction
    return payload
