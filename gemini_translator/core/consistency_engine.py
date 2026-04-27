# -*- coding: utf-8 -*-
"""
ConsistencyEngine v2 — Движок для проверки согласованности текста.
Управляет процессом анализа чанков текста с помощью ИИ, накапливает глоссарий сессии.
"""

import asyncio
import html
import inspect
import json
import logging
import re
import threading
from copy import deepcopy
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from PyQt6.QtCore import QObject, pyqtSignal

from ..api.errors import RateLimitExceededError, TemporaryRateLimitError
from ..api.factory import get_api_handler_class
from ..api import config as api_config
from ..utils.text import repair_json_string

logger = logging.getLogger(__name__)

CONSISTENCY_CONFIDENCE_LEVELS = ("high", "medium", "low")


def normalize_consistency_confidence(value: Any, default: str = "medium") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in CONSISTENCY_CONFIDENCE_LEVELS:
        return normalized
    return default


def normalize_consistency_confidences(
    values: Any,
    default: Any = None,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    source = values
    if source is None:
        source = default

    if source is None:
        source = CONSISTENCY_CONFIDENCE_LEVELS
    elif isinstance(source, str):
        source = [chunk.strip() for chunk in source.split(",")]

    normalized: list[str] = []
    for item in source or []:
        level = str(item or "").strip().lower()
        if level in CONSISTENCY_CONFIDENCE_LEVELS and level not in normalized:
            normalized.append(level)

    if not normalized and not allow_empty:
        fallback = default if default is not None else CONSISTENCY_CONFIDENCE_LEVELS
        if fallback is not source:
            return normalize_consistency_confidences(fallback, default=None, allow_empty=allow_empty)

    return tuple(normalized)


def filter_consistency_problems_by_confidence(
    problems: List[Dict[str, Any]] | None,
    allowed_confidences: Any = None,
) -> List[Dict[str, Any]]:
    if not problems:
        return []

    allowed = normalize_consistency_confidences(
        allowed_confidences,
        default=None,
        allow_empty=True,
    )
    if allowed_confidences is None:
        return list(problems)
    if not allowed:
        return []

    allowed_set = set(allowed)
    return [
        problem
        for problem in problems
        if normalize_consistency_confidence(problem.get("confidence")) in allowed_set
    ]


def _redact_api_key(api_key: Any) -> str:
    key = str(api_key or "")
    if not key:
        return "<empty>"
    return f"...{key[-4:]}" if len(key) > 4 else "..."


def _sanitize_api_keys(text: Any) -> str:
    sanitized = str(text or "")

    def _replace_api_key_match(match: re.Match) -> str:
        prefix = match.group(1) or ""
        return f"{prefix}{_redact_api_key(match.group(2))}"

    sanitized = re.sub(
        r"(api_key:)([A-Za-z0-9_\-]{8,})",
        _replace_api_key_match,
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"(\bkey=)([A-Za-z0-9_\-]{8,})",
        _replace_api_key_match,
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"\b(AIza[0-9A-Za-z_\-]{20,})\b",
        lambda match: _redact_api_key(match.group(1)),
        sanitized,
    )
    return sanitized


def _normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        normalized: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                normalized.append(text)
        return normalized
    text = str(value).strip()
    return [text] if text else []


def _normalize_character_entry(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, str):
        name = value.strip()
        if not name:
            return None
        return {"name": name, "aliases": []}

    if not isinstance(value, dict):
        return None

    name = str(value.get("name") or value.get("character") or "").strip()
    if not name:
        return None

    normalized = {"name": name, "aliases": _normalize_text_list(value.get("aliases"))}
    role = str(value.get("role") or "").strip()
    gender = str(value.get("gender") or "").strip()
    notes = str(value.get("notes") or value.get("note") or "").strip()
    if role:
        normalized["role"] = role
    if gender:
        normalized["gender"] = gender
    if notes:
        normalized["notes"] = notes
    return normalized


def _normalize_term_entry(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, str):
        term = value.strip()
        if not term:
            return None
        return {"term": term, "definition": ""}

    if not isinstance(value, dict):
        return None

    term = str(value.get("term") or value.get("name") or "").strip()
    if not term:
        return None

    normalized = {"term": term}
    definition = str(value.get("definition") or value.get("description") or "").strip()
    if definition:
        normalized["definition"] = definition
    return normalized


def _normalize_glossary_update(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {"characters": [], "terms": [], "plots": []}

    characters: list[Dict[str, Any]] = []
    raw_characters = value.get("characters", [])
    if isinstance(raw_characters, dict):
        raw_characters = [raw_characters]
    elif not isinstance(raw_characters, (list, tuple, set)):
        raw_characters = [raw_characters] if raw_characters else []
    for entry in raw_characters:
        normalized = _normalize_character_entry(entry)
        if normalized:
            characters.append(normalized)

    terms: list[Dict[str, Any]] = []
    raw_terms = value.get("terms", [])
    if isinstance(raw_terms, dict):
        raw_terms = [raw_terms]
    elif not isinstance(raw_terms, (list, tuple, set)):
        raw_terms = [raw_terms] if raw_terms else []
    for entry in raw_terms:
        normalized = _normalize_term_entry(entry)
        if normalized:
            terms.append(normalized)

    return {
        "characters": characters,
        "terms": terms,
        "plots": _normalize_text_list(value.get("plots")),
    }


def _normalize_context_summary(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "processed_chapters": [],
            "important_events": [],
            "next_chunk_focus": [],
        }

    return {
        "processed_chapters": _normalize_text_list(value.get("processed_chapters")),
        "important_events": _normalize_text_list(value.get("important_events")),
        "next_chunk_focus": _normalize_text_list(value.get("next_chunk_focus")),
    }


@dataclass
class GlossarySession:
    """
    Накопительный глоссарий сессии — хранит информацию о персонажах, терминах и сюжетных линиях,
    обнаруженных в процессе анализа чанков.
    """
    characters: List[Dict[str, Any]] = field(default_factory=list)
    terms: List[Dict[str, Any]] = field(default_factory=list)
    processed_chapters: List[str] = field(default_factory=list)
    important_events: List[str] = field(default_factory=list)
    next_chunk_focus: List[str] = field(default_factory=list)

    def update_from_response(self, glossary_update: Dict[str, Any], context_summary: Dict[str, Any]):
        """Обновляет глоссарий на основе ответа модели."""
        glossary_update = _normalize_glossary_update(glossary_update)
        context_summary = _normalize_context_summary(context_summary)

        if glossary_update:
            # Добавляем персонажей (с дедупликацией по имени)
            for char in glossary_update.get('characters', []):
                if char and not any(c.get('name') == char.get('name') for c in self.characters):
                    self.characters.append(char)
            
            # Добавляем термины (с дедупликацией)
            for term in glossary_update.get('terms', []):
                if term and not any(t.get('term') == term.get('term') for t in self.terms):
                    self.terms.append(term)
        
        if context_summary:
            # Обновляем обработанные главы
            for ch in context_summary.get('processed_chapters', []):
                if ch and ch not in self.processed_chapters:
                    self.processed_chapters.append(ch)
            
            # Обновляем важные события
            for event in context_summary.get('important_events', []):
                if event and event not in self.important_events:
                    self.important_events.append(event)
            
            # Заменяем фокус на следующий чанк (не накапливаем)
            self.next_chunk_focus = context_summary.get('next_chunk_focus', [])

    def to_dict(self) -> Dict[str, Any]:
        """Возвращает глоссарий как словарь для передачи в промт."""
        return {
            'characters': self.characters,
            'terms': self.terms,
            'processed_chapters': self.processed_chapters,
            'important_events': self.important_events,
            'next_chunk_focus': self.next_chunk_focus
        }

    def clear(self):
        """Очищает глоссарий для новой сессии."""
        self.characters.clear()
        self.terms.clear()
        self.processed_chapters.clear()
        self.important_events.clear()
        self.next_chunk_focus.clear()


class _ConsistencyPromptBuilder:
    def __init__(self):
        self.system_instruction = None


class _ConsistencyMockWorker:
    def __init__(self, parent_engine):
        self._parent_engine = parent_engine
        self.settings_manager = parent_engine.settings_manager if parent_engine else None
        self.session_id = "consistency_check"
        self.provider_config = {}
        self.model_config = {}
        self.temperature = 0.3
        self.temperature_override_enabled = True
        self.thinking_enabled = False
        self.thinking_budget = 0
        self.thinking_level = "minimal"
        self.api_key = ""
        self.worker_id = ""
        self.model_id = ""
        self.max_concurrent_requests = 1
        self.workascii_workspace_name = ""
        self.workascii_workspace_index = 1
        self.workascii_timeout_sec = 1800
        self.workascii_headless = False
        self.workascii_profile_template_dir = ""
        self.workascii_refresh_every_requests = 0
        self.debug_logging_enabled = False
        self.debug_operation_filters = ""
        self.debug_max_log_mb = 128
        self.prompt_builder = _ConsistencyPromptBuilder()

    def configure(
        self,
        provider_config: Dict[str, Any],
        model_config: Dict[str, Any],
        config: Dict[str, Any],
        api_key_value: str,
        default_model_name: str,
    ) -> None:
        self.provider_config = provider_config or {}
        self.model_config = model_config or {}
        self.temperature = config.get("temperature", 0.3)
        self.temperature_override_enabled = bool(config.get("temperature_override_enabled", True))
        self.thinking_enabled = config.get("thinking_enabled", False)
        self.thinking_budget = config.get("thinking_budget", 0)
        self.thinking_level = config.get("thinking_level", "minimal")
        self.api_key = api_key_value
        self.worker_id = api_key_value
        self.model_id = self.model_config.get("id", default_model_name)
        self.max_concurrent_requests = self._safe_int(
            config.get("max_concurrent_requests", self.model_config.get("max_concurrent_requests", 1)),
            default=1,
            minimum=1,
        )
        self.workascii_workspace_name = str(config.get("workascii_workspace_name", "") or "").strip()
        self.workascii_workspace_index = self._safe_int(
            config.get("workascii_workspace_index", 1),
            default=1,
            minimum=1,
        )
        self.workascii_timeout_sec = self._safe_int(
            config.get("workascii_timeout_sec", 1800),
            default=1800,
            minimum=60,
        )
        self.workascii_headless = bool(config.get("workascii_headless", False))
        self.workascii_profile_template_dir = str(config.get("workascii_profile_template_dir", "") or "").strip()
        self.workascii_refresh_every_requests = self._safe_int(
            config.get("workascii_refresh_every_requests", 0),
            default=0,
            minimum=0,
        )
        self.debug_logging_enabled = bool(config.get("debug_logging_enabled", False))
        self.debug_operation_filters = str(config.get("debug_operation_filters", "") or "").strip()
        self.debug_max_log_mb = self._safe_int(
            config.get("debug_max_log_mb", 128),
            default=128,
            minimum=1,
        )
        self.prompt_builder.system_instruction = config.get("system_prompt")

    @staticmethod
    def _safe_int(value: Any, default: int, minimum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, parsed)

    @property
    def is_cancelled(self):
        return self._parent_engine.is_cancelled if self._parent_engine else False

    @is_cancelled.setter
    def is_cancelled(self, value):
        pass

    def check_cancellation(self):
        if self.is_cancelled:
            from ..api.errors import OperationCancelledError
            raise OperationCancelledError("Cancelled by user")

    def _post_event(self, name: str, data: dict = None):
        if name != 'log_message':
            return
        message = data.get('message', '') if data else ''
        if message:
            if self._parent_engine and hasattr(self._parent_engine, "_emit_log_message"):
                self._parent_engine._emit_log_message(message)
            else:
                logger.info("[Consistency handler] %s", message)


class _ConsistencyKeyHolder:
    def __init__(self, key: str):
        self.api_key = key
        self.worker_id = key


class ConsistencyEngine(QObject):
    """
    Движок для проверки согласованности текста (Consistency Checker v2).
    Управляет процессом анализа чанков текста с помощью ИИ.
    """

    # Сигналы для UI
    progress_updated = pyqtSignal(int, int)       # current, total
    chunk_analyzed = pyqtSignal(dict)             # результат анализа чанка
    error_occurred = pyqtSignal(str)
    log_message = pyqtSignal(str)
    key_discarded = pyqtSignal(str, str)             # api_key, sanitized reason
    finished = pyqtSignal(list)                   # список всех найденных проблем
    fix_progress = pyqtSignal(int, int, str)      # current, total, chapter_name (для массового исправления)
    fix_completed = pyqtSignal(str, str)          # chapter_path, new_content

    _SCRIPT_STYLE_RE = re.compile(
        r"<(?:script|style)\b[^>]*>[\s\S]*?</(?:script|style)>",
        re.IGNORECASE,
    )
    _HTML_TAG_RE = re.compile(r"<[^>]+>")
    _LATIN_RESIDUE_RE = re.compile(r"[A-Za-z][A-Za-z'\-]{2,}")
    _CJK_RESIDUE_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+")
    _RATING_TOKEN_RE = re.compile(r"^[A-Sa-s][+-]?$")

    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.glossary_session = GlossarySession()
        self.all_problems = []
        self.is_cancelled = False
        
        # Кэш для связи проблем с главами
        self.chapter_problems_map: Dict[str, List[Dict[str, Any]]] = {}
        self.request_response_trace: List[Dict[str, Any]] = []
        self._thread_handler_cache: Dict[int, Dict[tuple, Dict[str, Any]]] = {}
        self._discarded_keys: set[str] = set()
        
        # Индекс текущего ключа для ротации
        self._current_key_index = 0

    def cancel(self):
        """Отменяет текущую операцию."""
        self.is_cancelled = True

    def reset_session(self):
        """Сбрасывает сессию для нового анализа."""
        self.close_session_resources()
        self.glossary_session.clear()
        self.all_problems.clear()
        self.chapter_problems_map.clear()
        self.request_response_trace.clear()
        self._discarded_keys.clear()
        self.is_cancelled = False
        self._current_key_index = 0

    def _emit_log_message(self, message: str) -> None:
        text = _sanitize_api_keys(message).strip()
        if not text:
            return
        logger.info("[Consistency] %s", text)
        self.log_message.emit(text)

    def import_shared_glossary_entries(self, glossary_entries: Any) -> None:
        raw_entries: list[Any] = []
        if isinstance(glossary_entries, dict):
            raw_entries = list(glossary_entries.values())
        elif isinstance(glossary_entries, (list, tuple, set)):
            raw_entries = list(glossary_entries)

        shared_terms: list[Dict[str, Any]] = []
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue

            original = str(
                entry.get("original") or entry.get("term") or entry.get("name") or ""
            ).strip()
            if not original:
                continue

            rus = str(
                entry.get("rus") or entry.get("translation") or entry.get("target") or ""
            ).strip()
            note = str(
                entry.get("note") or entry.get("notes") or entry.get("definition") or ""
            ).strip()

            term_payload: Dict[str, Any] = {"term": original}
            definition_parts = [part for part in (rus, note) if part]
            if definition_parts:
                term_payload["definition"] = " | ".join(definition_parts)
            shared_terms.append(term_payload)

        if shared_terms:
            self.glossary_session.update_from_response(
                {"characters": [], "terms": shared_terms, "plots": []},
                {},
            )

    @staticmethod
    def _format_chunk_label(chunk: List[Dict[str, Any]], limit: int = 3) -> str:
        chapter_names = [
            str(chapter.get('name') or "").strip()
            for chapter in (chunk or [])
            if isinstance(chapter, dict) and str(chapter.get('name') or "").strip()
        ]
        if not chapter_names:
            return "без названий"
        visible = chapter_names[:limit]
        suffix = ""
        if len(chapter_names) > limit:
            suffix = f" (+{len(chapter_names) - limit})"
        return ", ".join(visible) + suffix

    @staticmethod
    def _safe_trace_text(text: Any) -> str:
        normalized = str(text or "").strip()
        limit = 12000
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(0, limit - 16)].rstrip() + "\n...[truncated]..."

    def _record_request_response_trace(
        self,
        *,
        phase: str,
        prompt: str,
        response: str,
        chapter_names: List[str] | None = None,
        metadata: Dict[str, Any] | None = None,
    ):
        trace_entry = {
            'phase': phase,
            'prompt': self._safe_trace_text(prompt),
            'response': self._safe_trace_text(response),
            'chapter_names': [
                str(name).strip()
                for name in (chapter_names or [])
                if str(name).strip()
            ],
        }
        cleaned_metadata = {}
        for key, value in (metadata or {}).items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                cleaned_metadata[key] = value
            else:
                cleaned_metadata[key] = str(value)
        if cleaned_metadata:
            trace_entry['metadata'] = cleaned_metadata
        self.request_response_trace.append(trace_entry)

    def get_request_response_trace(self) -> List[Dict[str, Any]]:
        return [dict(entry) for entry in self.request_response_trace]

    def analyze_chapters(self, chapters: List[Dict[str, Any]], config: Dict[str, Any], 
                        active_keys: List[str], 
                        mode: str = 'standard'):
        """
        Основной метод анализа глав.
        
        Args:
            chapters: список словарей {'name': str, 'content': str, 'path': str}
            config: настройки (provider, model, chunk_size, temperature, etc.)
            active_keys: список активных API ключей для использования
            mode: 'standard' или 'glossary_first' (двухпроходный режим)
        """
        self.reset_session()

        if not active_keys:
            self.error_occurred.emit("Нет активных ключей для анализа")
            self.finished.emit([])
            return

        # 1. Разбиение на чанки
        chunks = self._split_into_chunks(chapters, config.get('chunk_size', 3))
        total_chunks = len(chunks)
        
        # Двухпроходный режим: сначала собираем глоссарий
        if mode == 'glossary_first':
            logger.info("Запуск двухпроходного режима: проход 1 - сбор глоссария")
            for i, chunk in enumerate(chunks):
                if self.is_cancelled:
                    break
                    
                self.progress_updated.emit(i + 1, total_chunks * 2)  # *2 для двух проходов
                
                try:
                    prompt = self._build_glossary_collection_prompt(chunk, config)
                    self._emit_log_message(
                        f"[Glossary] Чанк {i + 1}/{total_chunks}: {self._format_chunk_label(chunk)}"
                    )
                    response_text = self._call_api_with_key_retry(
                        prompt,
                        config,
                        active_keys,
                        retry_label=f"глоссария (чанк {i + 1}/{total_chunks})",
                    )
                    self._record_request_response_trace(
                        phase='glossary_collection',
                        prompt=prompt,
                        response=response_text,
                        chapter_names=[ch.get('name', '') for ch in chunk if isinstance(ch, dict)],
                        metadata={
                            'chunk_index': i + 1,
                            'total_chunks': total_chunks,
                            'mode': mode,
                        },
                    )
                    
                    result = self._parse_ai_response(response_text)
                    if result:
                        # Обновляем только глоссарий, игнорируя проблемы
                        self.glossary_session.update_from_response(
                            result.get('glossary_update', {}),
                            result.get('context_summary', {})
                        )
                        # Эмитим результат для отображения прогресса в UI
                        self.chunk_analyzed.emit({
                            'problems': [],
                            'glossary_update': result.get('glossary_update', {}),
                            'context_summary': result.get('context_summary', {}),
                            'phase': 'glossary_collection'
                        })
                        
                except Exception as e:
                    error_text = self._sanitize_exception_message(e)
                    logger.error(f"Error collecting glossary for chunk {i + 1}: {error_text}")
                    self.error_occurred.emit(f"Ошибка сбора глоссария (чанк {i + 1}): {error_text}")
                    if not active_keys:
                        self.is_cancelled = True
                        break
            
            logger.info("Двухпроходный режим: проход 2 - поиск проблем с глоссарием")

        # Основной проход: поиск проблем
        for i, chunk in enumerate(chunks):
            if self.is_cancelled:
                break

            if mode == 'glossary_first':
                self.progress_updated.emit(total_chunks + i + 1, total_chunks * 2)
            else:
                self.progress_updated.emit(i + 1, total_chunks)

            # 2. Формирование промпта
            prompt = self._build_analysis_prompt(chunk, config)

            # 3. Вызов API с ротацией ключей
            try:
                self._emit_log_message(
                    f"[Analysis] Чанк {i + 1}/{total_chunks}: {self._format_chunk_label(chunk)}"
                )
                response_text = self._call_api_with_key_retry(
                    prompt,
                    config,
                    active_keys,
                    retry_label=f"анализа (чанк {i + 1}/{total_chunks})",
                )
                self._record_request_response_trace(
                    phase='analysis',
                    prompt=prompt,
                    response=response_text,
                    chapter_names=[ch.get('name', '') for ch in chunk if isinstance(ch, dict)],
                    metadata={
                        'chunk_index': i + 1,
                        'total_chunks': total_chunks,
                        'mode': mode,
                    },
                )

                # 4. Валидация и парсинг JSON
                analysis_result = self._parse_ai_response(response_text)

                if analysis_result:
                    # Накапливаем проблемы
                    chunk_problems = analysis_result.get('problems', [])
                    for prob in chunk_problems:
                        prob['chunk_index'] = i
                        # Привязываем проблему к главе
                        chapter_name = prob.get('chapter', '')
                        if chapter_name not in self.chapter_problems_map:
                            self.chapter_problems_map[chapter_name] = []
                        self.chapter_problems_map[chapter_name].append(prob)
                    
                    self.all_problems.extend(chunk_problems)

                    # Обновляем глоссарий сессии (если не двухпроходный, или добавляем новое)
                    self.glossary_session.update_from_response(
                        analysis_result.get('glossary_update', {}),
                        analysis_result.get('context_summary', {})
                    )

                    self.chunk_analyzed.emit(analysis_result)

            except Exception as e:
                error_text = self._sanitize_exception_message(e)
                logger.error(f"Error analyzing chunk {i + 1}: {error_text}")
                self.error_occurred.emit(f"Ошибка анализа чанка {i + 1}: {error_text}")
                if not active_keys:
                    self.is_cancelled = True
                    break

        self.finished.emit(self.all_problems)

    def _get_next_key(self, active_keys: List[str]) -> str:
        """Получает следующий ключ с ротацией."""
        if not active_keys:
            raise ValueError("Нет доступных ключей")
        
        key = active_keys[self._current_key_index % len(active_keys)]
        self._current_key_index += 1
        return key

    def _sanitize_exception_message(self, exc: Exception | str) -> str:
        return _sanitize_api_keys(str(exc))

    def _is_key_retryable_error(self, exc: Exception) -> bool:
        text = str(exc or "")
        lowered = text.lower()

        if isinstance(exc, TemporaryRateLimitError):
            return True

        permanent_markers = (
            "suspended",
            "permission denied",
            "permission_denied",
            "api key not valid",
            "invalid api key",
            "invalid_api_key",
            "api_key_invalid",
            "api_key:",
            "api key",
            "невалид",
            "заблок",
        )
        has_auth_status = bool(re.search(r"\b(?:400|401|403)\b", lowered))
        if has_auth_status and any(marker in lowered for marker in permanent_markers):
            return True

        if any(marker in lowered for marker in ("suspended", "permission denied", "permission_denied")):
            return True

        quota_markers = (
            "quota",
            "квота",
            "исчерпал лимит",
            "исчерпан",
            "exhausted",
            "rate limit",
            "лимит запросов",
            "недостаточно средств",
        )
        if isinstance(exc, RateLimitExceededError) and any(marker in lowered for marker in quota_markers):
            return True

        return False

    def _mark_key_unavailable_for_current_model(self, api_key: str, config: Dict[str, Any]) -> None:
        if not api_key:
            return
        try:
            _, model_name, _, model_config = self._resolve_provider_and_model_config(config)
            model_id = model_config.get("id", model_name)
            marker = getattr(self.settings_manager, "mark_key_as_exhausted", None)
            if callable(marker):
                marker(api_key, model_id)
        except Exception as marker_error:
            logger.debug(
                "Failed to mark consistency key as unavailable: %s",
                self._sanitize_exception_message(marker_error),
            )

    def _should_persist_key_unavailable_status(self, exc: Exception) -> bool:
        return not isinstance(exc, TemporaryRateLimitError)

    def _discard_key_for_retry(
        self,
        active_keys: List[str],
        api_key: str,
        config: Dict[str, Any],
        exc: Exception,
    ) -> bool:
        before = len(active_keys)
        active_keys[:] = [key for key in active_keys if key != api_key]
        removed = len(active_keys) != before
        if not removed:
            return False

        self._discarded_keys.add(api_key)
        reason = self._sanitize_exception_message(exc)
        masked_key = _redact_api_key(api_key)
        if self._should_persist_key_unavailable_status(exc):
            self._mark_key_unavailable_for_current_model(api_key, config)
        self._emit_log_message(
            f"⚠️ Ключ {masked_key} отключён для текущей проверки: {reason}"
        )
        self.key_discarded.emit(api_key, reason)
        return True

    def _call_api_with_key_retry(
        self,
        prompt: str,
        config: Dict[str, Any],
        active_keys: List[str],
        *,
        retry_label: str,
    ) -> str:
        last_error: Exception | None = None

        while active_keys and not self.is_cancelled:
            api_key = self._get_next_key(active_keys)
            try:
                return self._call_api(prompt, config, api_key)
            except Exception as exc:
                if not self._is_key_retryable_error(exc):
                    raise

                last_error = exc
                if not self._discard_key_for_retry(active_keys, api_key, config, exc):
                    raise

                if active_keys:
                    self._emit_log_message(
                        f"↻ Повтор {retry_label} с другим ключом. Осталось ключей: {len(active_keys)}"
                    )

        if last_error is not None:
            raise RuntimeError(
                "Нет доступных ключей для повтора чанка после отключения неисправных ключей. "
                f"Последняя ошибка: {self._sanitize_exception_message(last_error)}"
            ) from last_error

        raise ValueError("Нет доступных ключей для анализа")

    def _split_into_chunks(self, chapters: List[Dict[str, Any]], chunk_size: int) -> List[List[Dict[str, Any]]]:
        """Разбивает список глав на группы (чанки)."""
        return [chapters[i:i + chunk_size] for i in range(0, len(chapters), chunk_size)]

    def _filter_glossary_for_text(self, text: str, extra_text: str = "") -> Dict[str, Any]:
        """
        Фильтрует глоссарий, оставляя только термины, встречающиеся в тексте.
        Использует нечеткий поиск (упрощенная лемматизация хвостов).
        """
        full_text = (text + " " + extra_text).lower()
        
        filtered_chars = []
        filtered_terms = []
        
        # 1. Фильтруем персонажей
        for char in self.glossary_session.characters:
            name = char.get('name', '').strip()
            aliases = char.get('aliases', [])
            
            # Проверяем имя и алиасы
            found = False
            to_check = [name] + aliases
            
            for word in to_check:
                if not word: continue
                word_lower = word.lower()
                
                # Эвристика: если слово длинное (>4), ищем основу без окончания
                if len(word_lower) > 4:
                    root = word_lower[:-1]
                else:
                    root = word_lower
                    
                if root in full_text:
                    found = True
                    break
            
            if found:
                filtered_chars.append(char)
                
        # 2. Фильтруем термины
        for term_obj in self.glossary_session.terms:
            term = term_obj.get('term', '').strip()
            if not term: continue
            
            term_lower = term.lower()
            if len(term_lower) > 4:
                root = term_lower[:-1]
            else:
                root = term_lower
                
            if root in full_text:
                filtered_terms.append(term_obj)
                
        return {
            'characters': filtered_chars,
            'terms': filtered_terms,
            'processed_chapters': self.glossary_session.processed_chapters,
            'important_events': self.glossary_session.important_events,
            'next_chunk_focus': self.glossary_session.next_chunk_focus
        }

    def _build_analysis_prompt(self, chunk: List[Dict[str, Any]], config: Dict[str, Any]) -> str:
        """Формирует промпт для анализа."""
        chapters_text = ""
        for ch in chunk:
            chapters_text += f"\n--- CHAPTER: {ch['name']} ---\n{ch['content']}\n"

        # Умная фильтрация глоссария
        filtered_glossary = self._filter_glossary_for_text(chapters_text)
        
        context_json = json.dumps(
            filtered_glossary, ensure_ascii=False, indent=2)

        # Загружаем промпт из файла
        from ..api.config import get_resource_path
        prompts_file = get_resource_path("config/consistency_prompts.json")
        system_prompt = ""
        
        if prompts_file.exists():
            try:
                with open(prompts_file, 'r', encoding='utf-8') as f:
                    prompts_data = json.load(f)
                    system_prompt = "\n".join(
                        prompts_data.get("consistency_analysis", []))
            except Exception as e:
                logger.error(f"Failed to load consistency prompts: {e}")

        if not system_prompt:
            system_prompt = config.get(
                'system_prompt', "You are a professional literary editor.")

        # Подставляем переменные в промпт
        prompt = system_prompt.replace('{context_json}', context_json)
        prompt = prompt.replace('{chapters_text}', chapters_text)

        return prompt

    def _build_glossary_collection_prompt(self, chunk: List[Dict[str, Any]], config: Dict[str, Any]) -> str:
        """Формирует промпт для сбора глоссария (первый проход двухпроходного режима)."""
        chapters_text = ""
        for ch in chunk:
            chapters_text += f"\n--- CHAPTER: {ch['name']} ---\n{ch['content']}\n"

        # Умная фильтрация глоссария
        filtered_glossary = self._filter_glossary_for_text(chapters_text)
        
        context_json = json.dumps(
            filtered_glossary, ensure_ascii=False, indent=2)

        # Загружаем промпт для сбора глоссария
        from ..api.config import get_resource_path
        prompts_file = get_resource_path("config/consistency_prompts.json")
        system_prompt = ""
        
        if prompts_file.exists():
            try:
                with open(prompts_file, 'r', encoding='utf-8') as f:
                    prompts_data = json.load(f)
                    system_prompt = "\n".join(
                        prompts_data.get("glossary_collection", []))
            except Exception as e:
                logger.error(f"Failed to load glossary collection prompts: {e}")

        if not system_prompt:
            # Fallback: используем обычный промпт, но попросим не искать проблемы
            system_prompt = (
                "Analyze the following text and extract:\n"
                "1. Characters: name, gender, role, aliases\n"
                "2. Terms: unique terms, skills, items, locations\n"
                "3. Plot points: active storylines\n"
                "Do NOT look for problems in this pass.\n\n"
                "CURRENT CONTEXT:\n{context_json}\n\n"
                "TEXT TO ANALYZE:\n{chapters_text}\n\n"
                "Return JSON with glossary_update and context_summary only."
            )

        prompt = system_prompt.replace('{context_json}', context_json)
        prompt = prompt.replace('{chapters_text}', chapters_text)

        return prompt

    def _unwrap_code_fence(self, text: str) -> str:
        """Извлекает содержимое markdown-блока, если ответ завернут в ```...```."""
        if not text:
            return text

        matches = re.findall(r"```(?:[\w#+.-]+)?\s*([\s\S]*?)\s*```", text.strip())
        if not matches:
            return text

        return max(matches, key=len).strip()

    def _looks_like_html_content(self, text: str) -> bool:
        stripped = (text or "").lstrip()
        return stripped.startswith("<") or bool(re.search(r"</?[a-zA-Z][\w:-]*\b", stripped[:500]))

    def _extract_html_start_anchors(self, original_content: str) -> List[str]:
        anchors: List[str] = []
        stripped = (original_content or "").lstrip()

        for pattern in (
            r"<\?xml[^>]*\?>",
            r"<!DOCTYPE[^>]*>",
            r"<html\b",
            r"<body\b",
        ):
            match = re.search(pattern, stripped, re.IGNORECASE)
            if match:
                anchors.append(match.group(0))

        first_tag = re.search(r"<([a-zA-Z][\w:-]*)\b", stripped)
        if first_tag:
            anchors.append(f"<{first_tag.group(1)}")

        return list(dict.fromkeys(anchors))

    def _extract_html_end_anchors(self, original_content: str) -> List[str]:
        stripped = (original_content or "").rstrip()
        closing_tags = re.findall(r"</[a-zA-Z][\w:-]*\s*>", stripped, re.IGNORECASE)
        if not closing_tags:
            return []

        return list(dict.fromkeys(reversed(closing_tags[-3:])))

    def _trim_html_wrappers(self, text: str, original_content: str) -> str:
        cleaned = text.strip()

        for anchor in self._extract_html_start_anchors(original_content):
            pos = cleaned.find(anchor)
            if pos > 0:
                prefix = cleaned[:pos]
                if prefix.strip() and "<" not in prefix and ">" not in prefix:
                    cleaned = cleaned[pos:].lstrip()
                    break

        for anchor in self._extract_html_end_anchors(original_content):
            pos = cleaned.rfind(anchor)
            if pos != -1:
                end_pos = pos + len(anchor)
                suffix = cleaned[end_pos:]
                if suffix.strip() and "<" not in suffix and ">" not in suffix:
                    cleaned = cleaned[:end_pos].rstrip()
                    break

        return cleaned

    def _is_meta_line(self, line: str, original_content: str, *, trailing: bool = False) -> bool:
        candidate = (line or "").strip()
        if not candidate:
            return False

        normalized = re.sub(r"\s+", " ", candidate).strip(":- ").lower()
        if not normalized:
            return False

        original_ref = (original_content or "").strip()
        if original_ref:
            original_normalized = re.sub(r"\s+", " ", original_ref).lower()
            if (not trailing and original_normalized.startswith(normalized)) or (
                trailing and original_normalized.endswith(normalized)
            ):
                return False

        meta_patterns = (
            r"^исправлен(?:ия|ный|ная|ные)?(?:\s+были\s+сделаны)?$",
            r"^вот\s+исправлен(?:ный|ная|ные)?(?:\s+текст(?:\s+главы)?)?$",
            r"^ниже\s+исправлен(?:ный|ная|ные)?(?:\s+текст(?:\s+главы)?)?$",
            r"^правки\s+внесены$",
            r"^changes?\s+have\s+been\s+made$",
            r"^here\s+is\s+the\s+corrected(?:\s+chapter|\s+text)?$",
            r"^corrected(?:\s+chapter|\s+text)?$",
            r"^(?:#+\s*)?(?:output|ответ|вывод)$",
        )
        return any(re.match(pattern, normalized, re.IGNORECASE) for pattern in meta_patterns)

    def _strip_meta_prefix(self, text: str, original_content: str) -> str:
        cleaned = text.strip()
        if not cleaned:
            return cleaned

        lowered_cleaned = cleaned.lstrip().lower()
        lowered_original = (original_content or "").lstrip().lower()
        for phrase in (
            "исправления были сделаны",
            "исправленный текст",
            "вот исправленный текст",
            "ниже исправленный текст",
            "правки внесены",
            "changes have been made",
            "here is the corrected chapter",
            "here is the corrected text",
            "corrected chapter",
            "corrected text",
        ):
            if lowered_cleaned.startswith(phrase) and not lowered_original.startswith(phrase):
                cleaned = cleaned.lstrip()[len(phrase):].lstrip(" \t:-–—\r\n")
                lowered_cleaned = cleaned.lstrip().lower()
                break

        inline_prefix = re.compile(
            r"^\s*(?:"
            r"исправлен(?:ия|ный|ная|ные)?(?:\s+были\s+сделаны)?"
            r"|вот\s+исправлен(?:ный|ная|ные)?(?:\s+текст(?:\s+главы)?)?"
            r"|ниже\s+исправлен(?:ный|ная|ные)?(?:\s+текст(?:\s+главы)?)?"
            r"|правки\s+внесены"
            r"|changes?\s+have\s+been\s+made"
            r"|here\s+is\s+the\s+corrected(?:\s+chapter|\s+text)?"
            r"|corrected(?:\s+chapter|\s+text)?"
            r")(?:\s*[:\\-–—]\s*|\s+)",
            re.IGNORECASE,
        )

        if not inline_prefix.match((original_content or "").lstrip()):
            cleaned = inline_prefix.sub("", cleaned, count=1).strip()

        lines = cleaned.splitlines()
        while lines and self._is_meta_line(lines[0], original_content, trailing=False):
            lines.pop(0)
        while lines and self._is_meta_line(lines[-1], original_content, trailing=True):
            lines.pop()

        return "\n".join(lines).strip()

    def _plain_text_for_residue_scan(self, content: str) -> str:
        stripped = self._SCRIPT_STYLE_RE.sub(" ", content or "")
        stripped = self._HTML_TAG_RE.sub(" ", stripped)
        stripped = html.unescape(stripped)
        return re.sub(r"\s+", " ", stripped)

    def _collect_residue_tokens(self, content: str) -> Dict[str, str]:
        text = self._plain_text_for_residue_scan(content)
        tokens: Dict[str, str] = {}

        for match in self._CJK_RESIDUE_RE.finditer(text):
            token = match.group().strip()
            if token:
                tokens.setdefault(token, token)

        for match in self._LATIN_RESIDUE_RE.finditer(text):
            token = match.group().strip("'-")
            if len(token) < 3:
                continue
            if self._RATING_TOKEN_RE.fullmatch(token):
                continue
            tokens.setdefault(token.casefold(), token)

        return tokens

    def _new_residue_tokens_after_fix(self, original_content: str, fixed_content: str) -> List[str]:
        original_tokens = set(self._collect_residue_tokens(original_content).keys())
        fixed_tokens = self._collect_residue_tokens(fixed_content)
        return [
            token
            for key, token in fixed_tokens.items()
            if key not in original_tokens
        ]

    def _validate_fixed_chapter_residue(self, original_content: str, fixed_content: str) -> None:
        new_tokens = self._new_residue_tokens_after_fix(original_content, fixed_content)
        if not new_tokens:
            return

        preview = ", ".join(new_tokens[:12])
        if len(new_tokens) > 12:
            preview += f", +{len(new_tokens) - 12}"
        raise ValueError(
            "Consistency fix introduced new untranslated Latin/CJK residue: "
            f"{preview}"
        )

    def _sanitize_fixed_chapter_response(self, response_text: str, original_content: str) -> str:
        """
        Убирает служебные обертки модели и возвращает только исправленный текст главы.
        Это защищает от ответов вида "Исправления были сделаны" + содержимое главы.
        """
        cleaned = self._unwrap_code_fence((response_text or "").strip()).lstrip("\ufeff").strip()
        if not cleaned:
            return cleaned

        if self._looks_like_html_content(original_content):
            cleaned = self._trim_html_wrappers(cleaned, original_content)

        cleaned = self._strip_meta_prefix(cleaned, original_content)
        self._validate_fixed_chapter_residue(original_content, cleaned)
        return cleaned.strip()

    def fix_chapter(self, chapter_content: str, problems: List[Dict[str, Any]], 
                    config: Dict[str, Any], active_keys: List[str],
                    batch_mode: bool = False,
                    chapter_name: str | None = None) -> str:
        """
        Исправляет конкретную главу на основе списка проблем.
        
        Args:
            chapter_content: текст главы для исправления
            problems: список проблем для исправления
            config: настройки API
            active_keys: список активных API ключей
            batch_mode: использовать batch-промпт для нескольких ошибок
            
        Returns:
            Исправленный текст главы
        """
        from ..api.config import get_resource_path
        prompts_file = get_resource_path("config/consistency_prompts.json")
        
        if batch_mode and len(problems) > 1:
            # Batch-режим для нескольких ошибок
            prompt_template = ""
            if prompts_file.exists():
                try:
                    with open(prompts_file, 'r', encoding='utf-8') as f:
                        prompts_data = json.load(f)
                        prompt_template = "\n".join(
                            prompts_data.get("batch_chapter_fix", []))
                except Exception as e:
                    logger.error(f"Failed to load batch fix prompt: {e}")
            
            if not prompt_template:
                prompt_template = "Fix the following errors in the chapter:\n{errors_list}\n\nChapter:\n{chapter_content}"
            
            # Формируем список ошибок
            errors_list = []
            for i, prob in enumerate(problems, 1):
                errors_list.append(
                    f"{i}. [{prob.get('type', 'error')}] {prob.get('description', '')}\n"
                    f"   Цитата: \"{prob.get('quote', '')}\"\n"
                    f"   Исправить: {prob.get('suggestion', '')}"
                )
            
            prompt = prompt_template.replace('{errors_list}', "\n".join(errors_list))
            
            # Для множественных ошибок берем полный текст описаний
            extra_context = "\n".join([p.get('description', '') + " " + p.get('quote', '') for p in problems])
            filtered_glossary = self._filter_glossary_for_text(chapter_content, extra_context)
            
            prompt = prompt.replace('{glossary_json}', json.dumps(filtered_glossary, ensure_ascii=False, indent=2))
            prompt = prompt.replace('{chapter_content}', chapter_content)
            
        else:
            # Одиночное исправление
            prompt_template = ""
            if prompts_file.exists():
                try:
                    with open(prompts_file, 'r', encoding='utf-8') as f:
                        prompts_data = json.load(f)
                        prompt_template = "\n".join(
                            prompts_data.get("consistency_correction", []))
                except Exception as e:
                    logger.error(f"Failed to load correction prompt: {e}")
            
            if not prompt_template:
                prompt_template = "Fix this error: {error_description}\n\nChapter:\n{chapter_content}"
            
            prob = problems[0] if problems else {}
            prompt = prompt_template.replace('{error_type}', prob.get('type', 'error'))
            prompt = prompt.replace('{error_description}', prob.get('description', ''))
            prompt = prompt.replace('{quote}', prob.get('quote', ''))
            prompt = prompt.replace('{suggestion}', prob.get('suggestion', ''))
            
            # Фильтрация для одиночной ошибки
            extra_context = prob.get('description', '') + " " + prob.get('quote', '')
            filtered_glossary = self._filter_glossary_for_text(chapter_content, extra_context)
            
            prompt = prompt.replace('{glossary_json}', json.dumps(filtered_glossary, ensure_ascii=False, indent=2))
            prompt = prompt.replace('{chapter_content}', chapter_content)

        api_key = self._get_next_key(active_keys)
        self._emit_log_message(
            f"[Fix] {chapter_name or 'без названия'}: {len(problems or [])} проблем"
        )
        response_text = self._call_api(prompt, config, api_key)
        self._record_request_response_trace(
            phase='fix',
            prompt=prompt,
            response=response_text,
            chapter_names=[chapter_name] if chapter_name else [],
            metadata={
                'problem_count': len(problems or []),
                'batch_mode': bool(batch_mode),
            },
        )
        return self._sanitize_fixed_chapter_response(response_text, chapter_content)

    def fix_all_chapters(self, chapters: List[Dict[str, Any]], config: Dict[str, Any],
                         active_keys: List[str]) -> Dict[str, str]:
        """
        Массово исправляет все главы с найденными проблемами.
        
        Args:
            chapters: список глав {'name': str, 'content': str, 'path': str}
            config: настройки API
            active_keys: список активных API ключей
            
        Returns:
            Словарь {path: new_content} с исправленными главами
        """
        results = {}
        allowed_confidences = normalize_consistency_confidences(
            config.get('consistency_fix_confidences'),
            default=None,
            allow_empty=True,
        )
        use_confidence_filter = 'consistency_fix_confidences' in config
        chapters_with_problems = []
        for chapter in chapters:
            chapter_name = chapter['name']
            problems = self.chapter_problems_map.get(chapter_name, [])
            if use_confidence_filter:
                problems = filter_consistency_problems_by_confidence(
                    problems,
                    allowed_confidences,
                )
            if problems:
                chapters_with_problems.append((chapter, problems))

        total = len(chapters_with_problems)

        for i, (chapter, problems) in enumerate(chapters_with_problems):
            if self.is_cancelled:
                break
                
            chapter_name = chapter['name']
            
            self.fix_progress.emit(i + 1, total, chapter_name)
            
            try:
                fixed_content = self.fix_chapter(
                    chapter['content'], 
                    problems, 
                    config,
                    active_keys,
                    batch_mode=len(problems) > 1,
                    chapter_name=chapter_name,
                )
                results[chapter['path']] = fixed_content
                self.fix_completed.emit(chapter['path'], fixed_content)
                
            except Exception as e:
                logger.error(f"Error fixing chapter {chapter_name}: {e}")
                self.error_occurred.emit(f"Ошибка при исправлении {chapter_name}: {e}")
        
        return results

    def _run_handler_awaitable(self, awaitable):
        from ..api.base import get_worker_loop

        loop = get_worker_loop()
        if loop.is_running():
            temp_loop = asyncio.new_event_loop()
            try:
                return temp_loop.run_until_complete(awaitable)
            finally:
                temp_loop.close()
        return loop.run_until_complete(awaitable)

    def _cleanup_handler(self, handler) -> None:
        cleanup = getattr(handler, "_close_thread_session_internal", None)
        if not callable(cleanup):
            return

        try:
            result = cleanup()
        except Exception as e:
            logger.warning("Failed to start handler cleanup: %s", e)
            return

        if inspect.isawaitable(result):
            try:
                self._run_handler_awaitable(result)
            except Exception as e:
                logger.warning("Failed to cleanup handler resources: %s", e)

    def _get_current_thread_handler_cache(self) -> Dict[tuple, Dict[str, Any]]:
        thread_id = threading.get_ident()
        cache = self._thread_handler_cache.get(thread_id)
        if cache is None:
            cache = {}
            self._thread_handler_cache[thread_id] = cache
        return cache

    def close_session_resources(self) -> None:
        thread_id = threading.get_ident()
        cache = self._thread_handler_cache.pop(thread_id, {})
        for entry in cache.values():
            handler = entry.get("handler")
            if handler is not None:
                self._cleanup_handler(handler)

    @staticmethod
    def _build_handler_setup_signature(config: Dict[str, Any]) -> str:
        setup_keys = (
            "max_concurrent_requests",
            "workascii_workspace_name",
            "workascii_workspace_index",
            "workascii_timeout_sec",
            "workascii_headless",
            "workascii_profile_template_dir",
            "workascii_refresh_every_requests",
        )
        setup_config = {key: config.get(key) for key in setup_keys if key in config}
        return json.dumps(setup_config, ensure_ascii=False, sort_keys=True, default=str)

    def _build_handler_cache_key(
        self,
        handler_class_name: str,
        model_id: str,
        api_key: str,
        config: Dict[str, Any],
        proxy_settings: Dict[str, Any] | None,
    ) -> tuple:
        proxy_signature = json.dumps(proxy_settings or {}, ensure_ascii=False, sort_keys=True, default=str)
        setup_signature = self._build_handler_setup_signature(config)
        return (handler_class_name, str(model_id), str(api_key), proxy_signature, setup_signature)

    def _get_or_create_cached_handler(
        self,
        *,
        cache_key: tuple,
        handler_class,
        provider_info: Dict[str, Any],
        model_config: Dict[str, Any],
        config: Dict[str, Any],
        api_key: str,
        model_name: str,
        proxy_settings: Dict[str, Any] | None,
    ):
        cache = self._get_current_thread_handler_cache()
        entry = cache.get(cache_key)
        if entry:
            worker = entry["worker"]
            worker.configure(provider_info, model_config, config, api_key, model_name)
            return entry["handler"]

        worker = _ConsistencyMockWorker(self)
        worker.configure(provider_info, model_config, config, api_key, model_name)
        handler = handler_class(worker)
        key_holder = _ConsistencyKeyHolder(api_key)
        if not handler.setup_client(key_holder, proxy_settings=proxy_settings):
            self._cleanup_handler(handler)
            raise ValueError(f"Failed to initialize API handler {handler_class.__name__}")
        cache[cache_key] = {
            "handler": handler,
            "worker": worker,
        }
        return handler

    def _invalidate_cached_handler(self, cache_key: tuple, handler) -> None:
        cache = self._get_current_thread_handler_cache()
        cached = cache.get(cache_key)
        if cached and cached.get("handler") is handler:
            cache.pop(cache_key, None)
        self._cleanup_handler(handler)

    def _resolve_provider_and_model_config(
        self,
        config: Dict[str, Any],
    ) -> tuple[str, str, Dict[str, Any], Dict[str, Any]]:
        provider_name = str(config.get("provider") or "google").strip()
        model_name = str(config.get("model") or "gemini-2.0-flash-exp").strip()

        if provider_name == "local":
            provider_info = api_config.ensure_dynamic_provider_models(provider_name)
        else:
            provider_info = api_config.api_providers().get(provider_name, {})

        if not isinstance(provider_info, dict) or not provider_info:
            raise ValueError(f"Provider {provider_name} not found in config")

        explicit_model_config = config.get("model_config")
        explicit_provider = ""
        if isinstance(explicit_model_config, dict):
            explicit_provider = str(explicit_model_config.get("provider") or "").strip()

        if isinstance(explicit_model_config, dict) and (not explicit_provider or explicit_provider == provider_name):
            model_config = deepcopy(explicit_model_config)
        else:
            model_config = deepcopy(provider_info.get("models", {}).get(model_name, {}))

        if not model_config:
            runtime_model_config = api_config.all_models().get(model_name)
            runtime_provider = str((runtime_model_config or {}).get("provider") or "").strip()
            if isinstance(runtime_model_config, dict) and (not runtime_provider or runtime_provider == provider_name):
                model_config = deepcopy(runtime_model_config)

        if not model_config:
            for provider_model_name, provider_model_config in provider_info.get("models", {}).items():
                if str(provider_model_config.get("id") or "").strip() == model_name:
                    model_name = str(provider_model_name or model_name)
                    model_config = deepcopy(provider_model_config)
                    break

        if not str(model_config.get("id") or "").strip():
            model_config["id"] = model_name

        return provider_name, model_name, deepcopy(provider_info), model_config

    def _call_api_with_cached_handler(self, prompt: str, config: Dict[str, Any], api_key: str) -> str:
        provider_name = config.get('provider', 'google')
        if provider_name == 'dry_run':
            raise ValueError("Consistency check requires a real LLM provider. Dry Run mode is not supported.")

        provider_name, model_name, provider_info, model_config = self._resolve_provider_and_model_config(config)

        handler_class_name = provider_info.get('handler_class')
        handler_class = get_api_handler_class(handler_class_name)

        model_id = model_config.get('id', model_name)
        key_info = {'key': api_key, 'provider': provider_name}
        if self.settings_manager.is_key_limit_active(key_info, model_id):
            raise ValueError(f"РљР»СЋС‡ {api_key[:8]}... РёСЃС‡РµСЂРїР°Р» Р»РёРјРёС‚ РґР»СЏ РјРѕРґРµР»Рё {model_id}")

        proxy_settings = config.get('proxy_settings')
        if proxy_settings is None:
            try:
                proxy_settings = self.settings_manager.load_proxy_settings()
            except Exception as e:
                logger.warning(
                    "Failed to load proxy settings for consistency engine: %s",
                    e
                )
                proxy_settings = None

        cache_key = self._build_handler_cache_key(
            handler_class_name=handler_class_name,
            model_id=model_id,
            api_key=api_key,
            config=config,
            proxy_settings=proxy_settings,
        )
        handler = self._get_or_create_cached_handler(
            cache_key=cache_key,
            handler_class=handler_class,
            provider_info=provider_info,
            model_config=model_config,
            config=config,
            api_key=api_key,
            model_name=model_name,
            proxy_settings=proxy_settings,
        )

        try:
            response = handler.execute_api_call(prompt, "[Consistency]", use_stream=False)
            if inspect.isawaitable(response):
                response = self._run_handler_awaitable(response)
            return response
        except Exception:
            self._invalidate_cached_handler(cache_key, handler)
            raise

    def _call_api(self, prompt: str, config: Dict[str, Any], api_key: str) -> str:
        return self._call_api_with_cached_handler(prompt, config, api_key)
        """
        Вызывает API через существующую инфраструктуру.
        
        Args:
            prompt: текст промпта
            config: настройки (provider, model, temperature, etc.)
            api_key: API ключ для использования
        """
        provider_name = config.get('provider', 'google')
        if provider_name == 'dry_run':
            raise ValueError("Consistency check requires a real LLM provider. Dry Run mode is not supported.")

        model_name = config.get('model', 'gemini-2.0-flash-exp')

        providers_config = _load_providers_config()
        provider_info = providers_config.get(provider_name)

        if not provider_info:
            raise ValueError(f"Provider {provider_name} not found in config")

        handler_class_name = provider_info.get('handler_class')
        handler_class = get_api_handler_class(handler_class_name)

        model_config = provider_info.get('models', {}).get(model_name, {})
        
        # Добавляем id модели в model_config если его нет
        if 'id' not in model_config:
            model_config['id'] = model_name
        
        model_id = model_config.get('id', model_name)
        
        # RPD tracking: проверяем, не исчерпан ли лимит ключа для этой модели
        key_info = {'key': api_key, 'provider': provider_name}
        if self.settings_manager.is_key_limit_active(key_info, model_id):
            raise ValueError(f"Ключ {api_key[:8]}... исчерпал лимит для модели {model_id}")

        # Создаём mock-worker для совместимости с handler API
        class MockWorker:
            def __init__(self, provider_config, model_config, config, api_key_value, parent_engine):
                self.provider_config = provider_config
                self.model_config = model_config
                self.session_id = "consistency_check"
                self.temperature = config.get('temperature', 0.3)
                self.temperature_override_enabled = bool(config.get('temperature_override_enabled', True))
                self.thinking_enabled = config.get('thinking_enabled', False)
                self.thinking_budget = config.get('thinking_budget', 0)
                self.thinking_level = config.get('thinking_level', 'minimal')
                self.api_key = api_key_value
                self.worker_id = api_key_value
                self.model_id = model_config.get('id', model_name)
                self._parent_engine = parent_engine
                
                # Mock PromptBuilder
                class MockPromptBuilder:
                    def __init__(self):
                        self.system_instruction = config.get('system_prompt')
                        
                self.prompt_builder = MockPromptBuilder()

            @property
            def is_cancelled(self):
                return self._parent_engine.is_cancelled if self._parent_engine else False

            @is_cancelled.setter
            def is_cancelled(self, value):
                pass

            def check_cancellation(self):
                if self.is_cancelled:
                    from ..api.errors import OperationCancelledError
                    raise OperationCancelledError("Cancelled by user")

            def _post_event(self, name: str, data: dict = None):
                if name != 'log_message':
                    return
                message = data.get('message', '') if data else ''
                if message:
                    logger.info("[Consistency handler] %s", message)

        mock_worker = MockWorker(provider_info, model_config, config, api_key, self)
        handler = handler_class(mock_worker)

        # Создаём объект с api_key для setup_client
        class KeyHolder:
            def __init__(self, key):
                self.api_key = key
                self.worker_id = key
        
        proxy_settings = config.get('proxy_settings')
        if proxy_settings is None:
            try:
                proxy_settings = self.settings_manager.load_proxy_settings()
            except Exception as e:
                logger.warning(
                    "Failed to load proxy settings for consistency engine: %s",
                    e
                )
                proxy_settings = None

        key_holder = KeyHolder(api_key)
        handler.setup_client(
            key_holder,
            proxy_settings=proxy_settings
        )
        try:
            response = handler.call_api(prompt, "[Consistency]", use_stream=False)
            if inspect.isawaitable(response):
                response = self._run_handler_awaitable(response)

            # RPD tracking: инкрементируем счётчик запросов после успешного вызова
            self.settings_manager.increment_request_count(api_key, model_id)

            return response
        finally:
            self._cleanup_handler(handler)

    def _parse_ai_response(self, text: str) -> Optional[Dict[str, Any]]:
        """
        Парсит и валидирует JSON от ИИ.
        Обрабатывает различные форматы ответа (чистый JSON, markdown-блоки).
        """
        if not text:
            raise ValueError("AI returned an empty response instead of JSON.")
            
        # Убираем markdown-блоки если есть
        text = text.strip()
        if re.match(
            r"^(?:chatgpt\s+(?:said|сказал)\s*:?\s*)?"
            r"(?:thinking|думаю|думaю|analyzing|анализирую|reasoning|рассуждаю|thought for|reasoned for)\b",
            text,
            re.IGNORECASE,
        ):
            raise ValueError("AI returned a thinking/status prelude instead of JSON.")
        
        # Паттерн для извлечения JSON из markdown блока
        json_block_pattern = r'```(?:json)?\s*([\s\S]*?)\s*```'
        match = re.search(json_block_pattern, text)
        if match:
            text = match.group(1).strip()
        
        # Ищем первую { и последнюю }
        start_idx = text.find('{')
        end_idx = text.rfind('}')
        
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            text = text[start_idx:end_idx + 1]
        
        try:
            # Пробуем распарсить напрямую
            data = json.loads(text)
            validated = self._validate_response(data)
            if validated is None:
                raise ValueError("AI returned JSON in an unexpected schema.")
            return validated
        except (json.JSONDecodeError, ValueError):
            # Пробуем восстановить битый JSON
            try:
                repaired_json = repair_json_string(text)
                if not repaired_json:
                    raise ValueError("JSON repair returned no result.")
                data = json.loads(repaired_json)
                validated = self._validate_response(data)
                if validated is None:
                    raise ValueError("AI returned JSON in an unexpected schema after repair.")
                return validated
            except Exception as e:
                logger.error(f"Failed to parse AI response: {e}\nOriginal text: {text[:500]}...")
                raise ValueError(f"Не удалось разобрать JSON-ответ AI-consistency: {e}") from e

    def _validate_response(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Валидирует структуру ответа."""
        if not isinstance(data, dict):
            return None

        problems_payload = data.get('problems', [])
        if isinstance(problems_payload, dict):
            problems_payload = [problems_payload]
        elif not isinstance(problems_payload, list):
            problems_payload = []

        # Валидируем каждую проблему
        valid_problems = []
        for prob in problems_payload:
            if isinstance(prob, dict) and prob.get('type'):
                normalized_prob = dict(prob)
                normalized_prob['id'] = normalized_prob.get('id', len(valid_problems) + 1)
                normalized_prob['confidence'] = normalize_consistency_confidence(
                    normalized_prob.get('confidence')
                )
                normalized_prob['chapter'] = str(
                    normalized_prob.get('chapter') or 'Unknown'
                ).strip() or 'Unknown'
                valid_problems.append(normalized_prob)

        data['problems'] = valid_problems
        data['glossary_update'] = _normalize_glossary_update(data.get('glossary_update'))
        data['context_summary'] = _normalize_context_summary(data.get('context_summary'))

        return data

    def get_problems_for_chapter(self, chapter_name: str) -> List[Dict[str, Any]]:
        """Возвращает список проблем для конкретной главы."""
        return self.chapter_problems_map.get(chapter_name, [])

    def get_glossary_summary(self) -> Dict[str, Any]:
        """Возвращает текущее состояние глоссария сессии."""
        return self.glossary_session.to_dict()

    def get_glossary_token_count(self) -> int:
        """Возвращает приблизительное количество токенов в глоссарии сессии."""
        glossary_json = json.dumps(self.glossary_session.to_dict(), ensure_ascii=False)
        return len(glossary_json) // 4  # ~4 символа на токен
