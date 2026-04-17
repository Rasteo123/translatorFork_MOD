from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


_WRITE_LOCK = threading.RLock()
_ANNOUNCED_SESSION_DIRS: set[str] = set()

_SENSITIVE_FIELD_NAMES = {
    "api_key",
    "apikey",
    "authorization",
    "proxy-authorization",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "client_secret",
    "password",
    "pass",
    "passwd",
    "cookie",
    "set-cookie",
    "x-api-key",
}

_SECRET_QUERY_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|key)=)([^&#\s]+)"
)
_BEARER_RE = re.compile(r"(?i)\b(Bearer\s+)([A-Za-z0-9._\-+/=]+)")
_GENERIC_SECRET_PAIR_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|x-api-key|token|secret|password|pass|passwd|cookie|set-cookie)\b([\"'\s:=]+)([^\"'\s,;]+)"
)
_PROXY_CREDENTIALS_RE = re.compile(r"(?i)(://)([^/\s:@]+):([^@\s/]+)@")
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\-\s()]{7,}\d)(?!\w)")
_JWT_RE = re.compile(r"(?<![A-Za-z0-9_-])(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9]{12,}\b")
_HF_KEY_RE = re.compile(r"\bhf_[A-Za-z0-9]{12,}\b")
_GOOGLE_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z\-_]{20,}\b")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _hash_fragment(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:12]


def safe_key_id(value: str | None) -> str | None:
    if not value:
        return None
    tail = value[-4:] if len(value) >= 4 else value
    return f"key-{_hash_fragment(value)}-...{tail}"


def _mask_secret_value(value: str) -> str:
    clean_value = str(value or "")
    tail = clean_value[-4:] if len(clean_value) >= 4 else clean_value
    return f"<redacted:{_hash_fragment(clean_value)}:...{tail}>"


def _sanitize_string(value: str) -> str:
    text = str(value)
    text = _PROXY_CREDENTIALS_RE.sub(r"\1<redacted>@", text)
    text = _SECRET_QUERY_RE.sub(lambda m: f"{m.group(1)}{_mask_secret_value(m.group(2))}", text)
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)}{_mask_secret_value(m.group(2))}", text)
    text = _GENERIC_SECRET_PAIR_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{_mask_secret_value(m.group(3))}", text
    )
    text = _JWT_RE.sub(lambda m: _mask_secret_value(m.group(1)), text)
    text = _OPENAI_KEY_RE.sub(lambda m: _mask_secret_value(m.group(0)), text)
    text = _HF_KEY_RE.sub(lambda m: _mask_secret_value(m.group(0)), text)
    text = _GOOGLE_KEY_RE.sub(lambda m: _mask_secret_value(m.group(0)), text)
    text = _EMAIL_RE.sub("<redacted-email>", text)
    text = _PHONE_RE.sub("<redacted-phone>", text)
    return text


def sanitize_for_logging(value: Any, field_name: str | None = None) -> Any:
    normalized_field = (field_name or "").strip().lower()

    if normalized_field in _SENSITIVE_FIELD_NAMES:
        if value is None:
            return None
        return _mask_secret_value(str(value))

    if isinstance(value, dict):
        return {
            str(key): sanitize_for_logging(item, field_name=str(key))
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_logging(item) for item in value]

    if isinstance(value, bytes):
        return _sanitize_string(value.decode("utf-8", errors="replace"))

    if isinstance(value, Path):
        return _sanitize_string(str(value))

    if isinstance(value, (str, os.PathLike)):
        return _sanitize_string(str(value))

    if value is None or isinstance(value, (bool, int, float)):
        return value

    return _sanitize_string(str(value))


def _slugify(value: str | None, fallback: str, max_length: int = 64) -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    text = text.replace("\\", "/")
    text = text.split("/")[-1]
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._-")
    if not text:
        text = fallback
    return text[:max_length]


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str))
            handle.write("\n")


def _write_json_if_missing(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        if path.exists():
            return
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)


def _resolve_project_root(worker: Any, operation_context: dict[str, Any]) -> Path:
    project_path = operation_context.get("project_path")
    if project_path:
        return Path(project_path).resolve()

    project_manager = getattr(worker, "project_manager", None)
    if project_manager and getattr(project_manager, "project_folder", None):
        return Path(project_manager.project_folder).resolve()

    output_folder = getattr(worker, "output_folder", None)
    if output_folder:
        return Path(output_folder).resolve()

    file_path = getattr(worker, "file_path", None)
    if file_path:
        return Path(file_path).resolve().parent

    settings_manager = getattr(worker, "settings_manager", None)
    if settings_manager and getattr(settings_manager, "config_dir", None):
        return Path(settings_manager.config_dir).resolve()

    return Path.cwd().resolve()


def _normalize_filters(raw_filters: Any) -> set[str]:
    if not raw_filters:
        return set()

    if isinstance(raw_filters, str):
        parts = re.split(r"[,;\n]+", raw_filters)
    elif isinstance(raw_filters, (list, tuple, set)):
        parts = [str(item) for item in raw_filters]
    else:
        parts = [str(raw_filters)]

    return {part.strip().lower() for part in parts if str(part).strip()}


def _matches_filters(task_type: str, filters: set[str]) -> bool:
    if not filters:
        return True
    return task_type.strip().lower() in filters


def _build_chapter_summary(operation_context: dict[str, Any]) -> tuple[str | None, list[str]]:
    chapters = operation_context.get("chapters") or []
    if chapters and not isinstance(chapters, (list, tuple, set)):
        chapters = [chapters]

    sanitized_chapters = [str(chapter) for chapter in chapters if chapter]
    chapter = operation_context.get("chapter")
    if chapter:
        chapter = str(chapter)
    elif sanitized_chapters:
        chapter = sanitized_chapters[0]
    else:
        chapter = None

    return chapter, sanitized_chapters


def _chapter_label(chapter: str | None, chapters: list[str]) -> str:
    if chapter:
        return _slugify(chapter, "chapter")
    if chapters:
        first_chapter = _slugify(chapters[0], "chapter")
        if len(chapters) == 1:
            return first_chapter
        return f"{first_chapter}_plus_{len(chapters) - 1}"
    return "no_chapter"


def _build_error_payload(error: BaseException) -> dict[str, Any]:
    return {
        "type": type(error).__name__,
        "message": sanitize_for_logging(str(error)),
        "traceback": sanitize_for_logging("".join(traceback.format_exception(type(error), error, error.__traceback__))),
    }


@dataclass
class DebugOperationTrace:
    log_root: Path
    session_dir: Path
    operation_path: Path
    index_path: Path
    max_total_bytes: int
    max_file_count: int
    base_record: dict[str, Any]
    session_announcement_needed: bool = False

    def write_event(
        self,
        event_type: str,
        *,
        attempt: int = 1,
        raw_request: Any = None,
        raw_response: Any = None,
        status: str | None = None,
        error: BaseException | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        record = dict(self.base_record)
        record.update(
            {
                "event": event_type,
                "timestamp": _now_iso(),
                "attempt": attempt,
            }
        )
        if status:
            record["status"] = status
        if raw_request is not None:
            record["raw_request"] = sanitize_for_logging(raw_request)
        if raw_response is not None:
            record["raw_response"] = sanitize_for_logging(raw_response)
        if error is not None:
            record["error"] = _build_error_payload(error)
        if extra:
            for key, value in extra.items():
                record[key] = sanitize_for_logging(value, field_name=key)
        _append_jsonl(self.operation_path, record)

    def finalize(
        self,
        *,
        status: str,
        duration_ms: int,
        error: BaseException | None = None,
    ) -> dict[str, Any]:
        summary_record = dict(self.base_record)
        summary_record.update(
            {
                "event": "summary",
                "timestamp": _now_iso(),
                "status": status,
                "duration_ms": duration_ms,
                "operation_log_path": str(self.operation_path),
            }
        )
        if error is not None:
            summary_record["error"] = _build_error_payload(error)

        _append_jsonl(self.operation_path, summary_record)
        _append_jsonl(self.index_path, summary_record)
        self._rotate_logs(protected_paths={self.operation_path, self.index_path})
        return summary_record

    def _rotate_logs(self, protected_paths: set[Path]) -> None:
        with _WRITE_LOCK:
            try:
                all_log_files = [path for path in self.log_root.rglob("*.jsonl") if path.is_file()]
            except FileNotFoundError:
                return

            total_bytes = sum(path.stat().st_size for path in all_log_files if path.exists())
            file_count = len(all_log_files)

            if total_bytes <= self.max_total_bytes and file_count <= self.max_file_count:
                return

            candidates = sorted(
                (path for path in all_log_files if path not in protected_paths),
                key=lambda item: item.stat().st_mtime if item.exists() else 0,
            )

            for candidate in candidates:
                if total_bytes <= self.max_total_bytes and file_count <= self.max_file_count:
                    break
                if not candidate.exists():
                    continue
                try:
                    size = candidate.stat().st_size
                    candidate.unlink()
                    total_bytes -= size
                    file_count -= 1
                except OSError:
                    continue


def create_operation_trace(
    *,
    worker: Any,
    log_prefix: str,
    operation_context: dict[str, Any] | None,
    raw_filters: Any,
    max_total_mb: Any,
    max_file_count: int = 1000,
) -> DebugOperationTrace | None:
    operation_context = dict(operation_context or {})
    task_type = str(
        operation_context.get("task_type")
        or operation_context.get("operation_type")
        or "api_call"
    )
    filters = _normalize_filters(raw_filters)
    if not _matches_filters(task_type, filters):
        return None

    try:
        max_total_bytes = max(8, int(max_total_mb or 256)) * 1024 * 1024
    except (TypeError, ValueError):
        max_total_bytes = 256 * 1024 * 1024

    project_root = _resolve_project_root(worker, operation_context)
    debug_root = project_root / ".debug_logs"
    session_id = str(getattr(worker, "session_id", None) or "adhoc")
    session_dir = debug_root / "sessions" / _slugify(session_id, "adhoc", max_length=80)
    operation_dir = session_dir / "operations" / _slugify(task_type, "api_call", max_length=40)

    chapter, chapters = _build_chapter_summary(operation_context)
    chapter_label = _chapter_label(chapter, chapters)
    operation_id = uuid.uuid4().hex
    operation_filename = (
        f"{int(time.time() * 1000)}_{chapter_label}_{operation_id[:8]}.jsonl"
    )
    operation_path = operation_dir / operation_filename
    index_path = session_dir / "index.jsonl"

    provider_name = getattr(worker, "api_provider_name", None)
    model_id = getattr(worker, "model_id", None)
    project_name = project_root.name or "project"

    session_meta = {
        "session_id": session_id,
        "started_at": _now_iso(),
        "project_name": project_name,
        "project_path": str(project_root),
        "provider": provider_name,
        "model": model_id,
        "debug_operation_filters": sorted(filters),
        "debug_max_total_mb": max_total_bytes // (1024 * 1024),
    }
    _write_json_if_missing(session_dir / "session.json", session_meta)

    session_dir_key = str(session_dir)
    with _WRITE_LOCK:
        session_announcement_needed = session_dir_key not in _ANNOUNCED_SESSION_DIRS
        _ANNOUNCED_SESSION_DIRS.add(session_dir_key)

    key_value = getattr(worker, "api_key", None)
    chapter_count = len(chapters)
    base_record = {
        "operation_id": operation_id,
        "session_id": session_id,
        "worker_id": str(getattr(worker, "worker_id", None) or ""),
        "log_prefix": sanitize_for_logging(log_prefix),
        "project": project_name,
        "project_path": str(project_root),
        "provider": provider_name,
        "model": model_id,
        "key_id": safe_key_id(key_value),
        "task_id": str(operation_context.get("task_id") or ""),
        "task_type": task_type,
        "action": operation_context.get("action"),
        "chapter": chapter,
        "chapters": chapters,
        "chapter_count": chapter_count,
        "chunk_index": operation_context.get("chunk_index"),
        "chunk_total": operation_context.get("chunk_total"),
        "is_retry": bool(operation_context.get("is_retry", False)),
    }

    return DebugOperationTrace(
        log_root=debug_root,
        session_dir=session_dir,
        operation_path=operation_path,
        index_path=index_path,
        max_total_bytes=max_total_bytes,
        max_file_count=max(50, int(max_file_count)),
        base_record=base_record,
        session_announcement_needed=session_announcement_needed,
    )
