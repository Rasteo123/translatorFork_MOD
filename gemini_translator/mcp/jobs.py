from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets

from .paths import ensure_state_dirs, job_dir

TEXT_FIELD_HINTS = ("text", "prompt", "chapter", "response", "content")
SECRET_FIELD_HINTS = ("api_key", "api-key", "token", "secret", "password")
SECRET_ARG_OPTIONS = {"--api-key", "--token", "--password"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_job_id(prefix: str = "job") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}_{stamp}_{secrets.token_hex(4)}"


@dataclass
class JobRecord:
    id: str
    type: str
    status: str
    created_at: str
    argv: list[str]
    project: str | None = None
    epub: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    pid: int | None = None
    exit_code: int | None = None
    result_path: str = ""
    stdout_path: str = ""
    stderr_path: str = ""
    command_path: str = ""
    error: str | None = None
    children: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "JobRecord":
        return cls(
            id=str(payload["id"]),
            type=str(payload["type"]),
            status=str(payload["status"]),
            created_at=str(payload["created_at"]),
            argv=[str(item) for item in payload.get("argv", [])],
            project=payload.get("project"),
            epub=payload.get("epub"),
            started_at=payload.get("started_at"),
            finished_at=payload.get("finished_at"),
            pid=payload.get("pid"),
            exit_code=payload.get("exit_code"),
            result_path=str(payload.get("result_path", "")),
            stdout_path=str(payload.get("stdout_path", "")),
            stderr_path=str(payload.get("stderr_path", "")),
            command_path=str(payload.get("command_path", "")),
            error=payload.get("error"),
            children=[str(item) for item in payload.get("children", [])],
            metadata=dict(payload.get("metadata") or {}),
        )


def create_job(
    state_dir: Path,
    job_type: str,
    argv: list[str],
    *,
    project: str | None,
    epub: str | None,
    metadata: dict | None = None,
    children: list[str] | None = None,
) -> JobRecord:
    ensure_state_dirs(state_dir)
    job_id = new_job_id(job_type)
    directory = job_dir(state_dir, job_id)
    directory.mkdir(parents=True, exist_ok=False)
    job = JobRecord(
        id=job_id,
        type=job_type,
        status="queued",
        created_at=utc_now(),
        argv=list(argv),
        project=project,
        epub=epub,
        result_path=str(directory / "result.json"),
        stdout_path=str(directory / "stdout.log"),
        stderr_path=str(directory / "stderr.log"),
        command_path=str(directory / "command.json"),
        metadata=dict(metadata or {}),
        children=list(children or []),
    )
    save_job(state_dir, job)
    Path(job.command_path).write_text(json.dumps({"argv": job.argv}, ensure_ascii=False, indent=2), encoding="utf-8")
    return job


def job_path(state_dir: Path, job_id: str) -> Path:
    return job_dir(state_dir, job_id) / "job.json"


def save_job(state_dir: Path, job: JobRecord) -> None:
    directory = job_dir(state_dir, job.id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "job.json"
    payload = json.dumps(job.to_dict(), ensure_ascii=False, indent=2)
    temp_path = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temp_path.write_text(payload, encoding="utf-8")
        temp_path.replace(path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def load_job(state_dir: Path, job_id: str) -> JobRecord:
    payload = json.loads(job_path(state_dir, job_id).read_text(encoding="utf-8"))
    return JobRecord.from_dict(payload)


def list_jobs(state_dir: Path) -> list[JobRecord]:
    root = state_dir / "jobs"
    if not root.exists():
        return []
    jobs = []
    for path in sorted(root.glob("*/job.json")):
        jobs.append(JobRecord.from_dict(json.loads(path.read_text(encoding="utf-8"))))
    return sorted(jobs, key=lambda item: item.created_at, reverse=True)


def mark_running(job: JobRecord, *, pid: int) -> None:
    job.status = "running"
    job.pid = pid
    job.started_at = utc_now()
    job.error = None


def mark_finished(job: JobRecord, *, status: str, exit_code: int | None, error: str | None = None) -> None:
    job.status = status
    job.exit_code = exit_code
    job.finished_at = utc_now()
    job.error = error
    job.pid = None


def _is_secret_key(key: str) -> bool:
    lowered = key.replace("_", "-").lower()
    return any(hint in lowered for hint in SECRET_FIELD_HINTS)


def _is_large_text_key(key: str) -> bool:
    lowered = key.replace("_", "-").lower()
    return any(hint in lowered for hint in TEXT_FIELD_HINTS)


def _redact_argv(argv: list[str]) -> list[str]:
    redacted = []
    skip_next = False
    for item in argv:
        if skip_next:
            redacted.append("<redacted>")
            skip_next = False
            continue
        option, separator, _value = item.partition("=")
        if separator and option in SECRET_ARG_OPTIONS:
            redacted.append(f"{option}=<redacted>")
            continue
        redacted.append(item)
        if item in SECRET_ARG_OPTIONS:
            skip_next = True
    return redacted


def redact_for_mcp(payload):
    if isinstance(payload, JobRecord):
        payload = payload.to_dict()
    if isinstance(payload, dict):
        result = {}
        for key, value in payload.items():
            if _is_secret_key(str(key)):
                result[key] = "<redacted>"
            elif _is_large_text_key(str(key)):
                result[key] = "<omitted>"
            elif key == "argv" and isinstance(value, list):
                result[key] = _redact_argv([str(item) for item in value])
            else:
                result[key] = redact_for_mcp(value)
        return result
    if isinstance(payload, list):
        return [redact_for_mcp(item) for item in payload]
    return payload


def tail_log(path: Path | str, *, limit: int = 20) -> list[str]:
    log_path = Path(path)
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-max(0, int(limit)) :]
