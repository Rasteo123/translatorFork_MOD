import copy
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


STEP_STATUS_PENDING = "pending"
STEP_STATUS_RUNNING = "running"
STEP_STATUS_SUCCESS = "success"
STEP_STATUS_FAILED = "failed"
STEP_STATUS_CANCELLED = "cancelled"

PIPELINE_STATUS_IDLE = "idle"
PIPELINE_STATUS_RUNNING = "running"
PIPELINE_STATUS_SUCCESS = "success"
PIPELINE_STATUS_FAILED = "failed"
PIPELINE_STATUS_CANCELLED = "cancelled"

FINAL_STEP_STATUSES = {
    STEP_STATUS_SUCCESS,
    STEP_STATUS_FAILED,
    STEP_STATUS_CANCELLED,
}

FINAL_PIPELINE_STATUSES = {
    PIPELINE_STATUS_SUCCESS,
    PIPELINE_STATUS_FAILED,
    PIPELINE_STATUS_CANCELLED,
}

PIPELINE_TEMPLATE_VERSION = 1


MERGE_MODE_LABELS = {
    "supplement": "Дополнение",
    "update": "Обновление",
    "accumulate": "Накопление",
}

STEP_STATUS_LABELS = {
    STEP_STATUS_PENDING: "Ожидание",
    STEP_STATUS_RUNNING: "Выполняется",
    STEP_STATUS_SUCCESS: "Готово",
    STEP_STATUS_FAILED: "Ошибка",
    STEP_STATUS_CANCELLED: "Остановлено",
}


def build_default_step_name(settings: Optional[Dict[str, Any]] = None, index: Optional[int] = None) -> str:
    settings = settings or {}
    merge_mode = MERGE_MODE_LABELS.get(settings.get("merge_mode") or settings.get("glossary_merge_mode"), "Проход")

    try:
        temperature = float(settings.get("temperature", 1.0))
        temperature_part = f"T={temperature:.1f}"
    except (TypeError, ValueError):
        temperature_part = "T=?"

    prefix = f"Шаг {index}" if index is not None else "Шаг"
    return f"{prefix}: {merge_mode} · {temperature_part}"


def summarize_step_settings(settings: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    settings = settings or {}

    try:
        temperature_value = float(settings.get("temperature", 1.0))
        temperature = f"{temperature_value:.1f}"
    except (TypeError, ValueError):
        temperature = "?"

    merge_mode_key = settings.get("merge_mode") or settings.get("glossary_merge_mode") or "supplement"
    merge_mode = MERGE_MODE_LABELS.get(merge_mode_key, merge_mode_key or "supplement")
    execution_mode = "Последовательный" if settings.get("is_sequential") else "Параллельный"

    task_size = settings.get("task_size_limit")
    if task_size in (None, ""):
        task_size = settings.get("glossary_task_size_limit_override")
    task_size_text = str(task_size) if task_size not in (None, "") else "—"

    new_terms_limit = settings.get("new_terms_limit")
    new_terms_limit_text = str(new_terms_limit) if new_terms_limit not in (None, "") else "—"

    return {
        "temperature": temperature,
        "merge_mode": merge_mode,
        "execution_mode": execution_mode,
        "task_size": task_size_text,
        "new_terms_limit": new_terms_limit_text,
    }


def classify_shutdown_reason(reason: Optional[str]) -> str:
    normalized = (reason or "").strip()
    if normalized == "Сессия успешно завершена":
        return STEP_STATUS_SUCCESS

    lowered = normalized.lower()
    cancelled_markers = (
        "отмен",
        "останов",
        "прерван",
        "cancel",
        "manual stop",
        "manual_stop",
    )
    if any(marker in lowered for marker in cancelled_markers):
        return STEP_STATUS_CANCELLED

    return STEP_STATUS_FAILED


@dataclass
class GlossaryPipelineStep:
    name: str
    settings: Dict[str, Any]
    step_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = STEP_STATUS_PENDING
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    last_reason: Optional[str] = None
    log_lines: List[str] = field(default_factory=list)

    def clone(self) -> "GlossaryPipelineStep":
        return GlossaryPipelineStep.from_dict(self.to_dict(include_runtime=True))

    def reset_runtime(self) -> None:
        self.status = STEP_STATUS_PENDING
        self.started_at = None
        self.finished_at = None
        self.last_reason = None
        self.log_lines = []

    def append_log(self, message: str) -> None:
        if not isinstance(message, str):
            return
        cleaned = message.strip()
        if not cleaned:
            return
        self.log_lines.append(cleaned)

    def runtime_to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "last_reason": self.last_reason,
            "log_lines": list(self.log_lines),
        }

    def to_dict(self, include_runtime: bool = False) -> Dict[str, Any]:
        payload = {
            "id": self.step_id,
            "name": self.name,
            "settings": copy.deepcopy(self.settings),
        }
        if include_runtime:
            payload["runtime"] = self.runtime_to_dict()
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GlossaryPipelineStep":
        runtime = data.get("runtime") or {}
        step = cls(
            step_id=data.get("id") or uuid.uuid4().hex,
            name=(data.get("name") or "Шаг").strip() or "Шаг",
            settings=copy.deepcopy(data.get("settings") or {}),
        )
        step.status = runtime.get("status", STEP_STATUS_PENDING)
        step.started_at = runtime.get("started_at")
        step.finished_at = runtime.get("finished_at")
        step.last_reason = runtime.get("last_reason")
        step.log_lines = list(runtime.get("log_lines") or [])
        return step


def create_step_from_settings(
    settings: Optional[Dict[str, Any]] = None,
    *,
    name: Optional[str] = None,
    index: Optional[int] = None,
) -> GlossaryPipelineStep:
    settings_snapshot = copy.deepcopy(settings or {})
    step_name = (name or build_default_step_name(settings_snapshot, index=index)).strip()
    return GlossaryPipelineStep(name=step_name or "Шаг", settings=settings_snapshot)


def steps_to_template_payload(steps: Iterable[GlossaryPipelineStep]) -> Dict[str, Any]:
    return {
        "version": PIPELINE_TEMPLATE_VERSION,
        "steps": [step.to_dict(include_runtime=False) for step in steps],
    }


def steps_from_template_payload(payload: Optional[Dict[str, Any]]) -> List[GlossaryPipelineStep]:
    payload = payload or {}
    raw_steps = payload.get("steps") or []
    return [GlossaryPipelineStep.from_dict(step_data) for step_data in raw_steps if isinstance(step_data, dict)]


class GlossaryPipelineRun:
    def __init__(self, steps: Optional[Iterable[GlossaryPipelineStep]] = None):
        self.steps = [step.clone() for step in (steps or [])]
        self.status = PIPELINE_STATUS_IDLE
        self.current_step_id: Optional[str] = None
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.last_reason: Optional[str] = None

    def has_steps(self) -> bool:
        return bool(self.steps)

    def reset(self) -> None:
        self.status = PIPELINE_STATUS_IDLE
        self.current_step_id = None
        self.started_at = None
        self.finished_at = None
        self.last_reason = None
        for step in self.steps:
            step.reset_runtime()

    def start(self) -> None:
        self.reset()
        self.status = PIPELINE_STATUS_RUNNING
        self.started_at = time.time()

    def get_step(self, step_id: Optional[str]) -> Optional[GlossaryPipelineStep]:
        if not step_id:
            return None
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def get_current_step(self) -> Optional[GlossaryPipelineStep]:
        return self.get_step(self.current_step_id)

    def get_step_index(self, step_id: Optional[str]) -> int:
        if not step_id:
            return -1
        for index, step in enumerate(self.steps):
            if step.step_id == step_id:
                return index
        return -1

    def has_pending_steps(self) -> bool:
        return any(step.status == STEP_STATUS_PENDING for step in self.steps)

    def start_next_step(self) -> Optional[GlossaryPipelineStep]:
        if self.status not in {PIPELINE_STATUS_IDLE, PIPELINE_STATUS_RUNNING}:
            return None

        if self.status == PIPELINE_STATUS_IDLE:
            self.start()

        for step in self.steps:
            if step.status == STEP_STATUS_PENDING:
                step.status = STEP_STATUS_RUNNING
                step.started_at = time.time()
                step.finished_at = None
                step.last_reason = None
                self.current_step_id = step.step_id
                self.status = PIPELINE_STATUS_RUNNING
                return step
        return None

    def append_log(self, message: str, step_id: Optional[str] = None) -> None:
        target_step_id = step_id or self.current_step_id
        step = self.get_step(target_step_id)
        if step:
            step.append_log(message)

    def finish_current_step(self, status: str, reason: Optional[str] = None) -> Optional[GlossaryPipelineStep]:
        step = self.get_current_step()
        if not step:
            return None

        step.status = status
        step.finished_at = time.time()
        step.last_reason = reason
        self.current_step_id = None

        if status == STEP_STATUS_SUCCESS:
            if self.has_pending_steps():
                self.status = PIPELINE_STATUS_RUNNING
            else:
                self.status = PIPELINE_STATUS_SUCCESS
                self.finished_at = step.finished_at
        elif status == STEP_STATUS_CANCELLED:
            self.status = PIPELINE_STATUS_CANCELLED
            self.finished_at = step.finished_at
        else:
            self.status = PIPELINE_STATUS_FAILED
            self.finished_at = step.finished_at

        self.last_reason = reason
        return step

    def mark_current_step_success(self, reason: Optional[str] = None) -> Optional[GlossaryPipelineStep]:
        return self.finish_current_step(STEP_STATUS_SUCCESS, reason)

    def mark_current_step_failed(self, reason: Optional[str] = None) -> Optional[GlossaryPipelineStep]:
        return self.finish_current_step(STEP_STATUS_FAILED, reason)

    def mark_current_step_cancelled(self, reason: Optional[str] = None) -> Optional[GlossaryPipelineStep]:
        return self.finish_current_step(STEP_STATUS_CANCELLED, reason)

    def is_finished(self) -> bool:
        return self.status in FINAL_PIPELINE_STATUSES
