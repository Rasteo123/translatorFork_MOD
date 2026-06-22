import asyncio
import inspect
import json
import re
import time
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gemini_translator.api import config as api_config
from gemini_translator.api.errors import (
    ContentFilterError,
    LocationBlockedError,
    ModelNotFoundError,
    NetworkError,
    OperationCancelledError,
    PartialGenerationError,
    RateLimitExceededError,
    TemporaryRateLimitError,
    ValidationFailedError,
)
from gemini_translator.api.factory import get_api_handler_class


TRANSLATION_TASK_TYPES = {"epub", "epub_chunk", "raw_text_translation"}
TRANSLATION_ACTION_PREFIXES = ("translate",)


@dataclass
class ProviderAttempt:
    provider_id: str
    model_name: str
    model_config: dict
    api_key: str
    label: str
    temperature: float | None = None
    temperature_override_enabled: bool | None = None
    prompt_prefix: str = ""
    prompt_suffix: str = ""
    pass_index: int = 1


@dataclass
class ProviderAttemptResult:
    attempt: ProviderAttempt
    text: str = ""
    elapsed_ms: int = 0
    error: str = ""
    exception: Exception | None = None

    @property
    def ok(self) -> bool:
        return bool(self.text and not self.error)


class _ApiKeyHolder:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.worker_id = api_key


class _ProviderWorkerProxy:
    def __init__(self, base_worker, attempt: ProviderAttempt, provider_config: dict):
        self._base_worker = base_worker
        self.api_provider_name = attempt.provider_id
        self.provider_config = provider_config
        self.model_config = attempt.model_config
        self.model_id = attempt.model_config.get("id") or attempt.model_name
        self.api_key = attempt.api_key
        self.worker_id = f"{getattr(base_worker, 'worker_id', 'worker')}:{attempt.label}"
        self.temperature = (
            attempt.temperature
            if attempt.temperature is not None
            else getattr(base_worker, "temperature", None)
        )
        self.temperature_override_enabled = (
            attempt.temperature_override_enabled
            if attempt.temperature_override_enabled is not None
            else getattr(base_worker, "temperature_override_enabled", True)
        )

    def __getattr__(self, name):
        return getattr(self._base_worker, name)


def _safe_int(value: Any, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _safe_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\n]+", value or "") if item.strip()]


def _post_log(worker, message: str, **extra) -> None:
    post_event = getattr(worker, "_post_event", None)
    payload = {"message": message}
    payload.update(extra)
    if callable(post_event):
        post_event("log_message", payload)


def _task_is_translation(operation_context: dict | None) -> bool:
    if not isinstance(operation_context, dict):
        return False
    task_type = str(operation_context.get("task_type") or operation_context.get("operation_type") or "")
    action = str(operation_context.get("action") or "")
    return task_type in TRANSLATION_TASK_TYPES and action.startswith(TRANSLATION_ACTION_PREFIXES)


def should_orchestrate_api_call(worker, operation_context: dict | None) -> bool:
    if not _task_is_translation(operation_context):
        return False
    if bool(getattr(worker, "_provider_orchestration_disabled", False)):
        return False
    return bool(
        getattr(worker, "parallel_providers_enabled", False)
        or getattr(worker, "multi_pass_enabled", False)
        or getattr(worker, "multi_pass_chapter_translation", False)
    )


def _provider_info(provider_id: str) -> dict:
    if provider_id == "local":
        return api_config.ensure_dynamic_provider_models(provider_id)
    return api_config.api_providers().get(provider_id, {})


def _provider_single_profile_fanout_limited(provider_id: str, provider_info: dict) -> bool:
    try:
        max_instances = api_config.provider_max_instances(provider_id)
    except Exception:
        max_instances = None
    if max_instances is None:
        try:
            max_instances = int(provider_info.get("max_instances"))
        except (TypeError, ValueError, AttributeError):
            max_instances = None
    if max_instances == 1:
        return True

    handler_class = str(provider_info.get("handler_class") or "")
    return bool(
        provider_info.get("stateful")
        or provider_info.get("browser_based")
        or provider_info.get("use_browser")
        or handler_class in {"BrowserApiHandler", "WorkAsciiChatGptApiHandler"}
    )


def _resolve_model(provider_id: str, model_name: str | None, fallback_model_config: dict | None = None) -> tuple[str, dict]:
    provider_info = _provider_info(provider_id)
    models = provider_info.get("models", {}) if isinstance(provider_info, dict) else {}
    requested = str(model_name or "").strip()

    if requested and requested in models:
        model_config = deepcopy(models[requested])
        model_config.setdefault("provider", provider_id)
        model_config.setdefault("id", requested)
        return requested, model_config

    if requested:
        for display_name, model_config in models.items():
            if str(model_config.get("id") or "").strip() == requested:
                resolved = deepcopy(model_config)
                resolved.setdefault("provider", provider_id)
                resolved.setdefault("id", requested)
                return str(display_name), resolved

    if fallback_model_config and provider_id == str(fallback_model_config.get("provider") or provider_id):
        fallback = deepcopy(fallback_model_config)
        fallback.setdefault("provider", provider_id)
        fallback.setdefault("id", requested or fallback.get("id") or "")
        return requested or str(fallback.get("id") or "model"), fallback

    if models:
        display_name, model_config = next(iter(models.items()))
        resolved = deepcopy(model_config)
        resolved.setdefault("provider", provider_id)
        resolved.setdefault("id", display_name)
        return str(display_name), resolved

    return requested or "model", {"id": requested or "model", "provider": provider_id}


def _active_keys_by_provider(worker) -> dict[str, list[str]]:
    raw = getattr(worker, "active_keys_by_provider", None)
    if not isinstance(raw, dict):
        raw = {}
    normalized: dict[str, list[str]] = {}
    for provider_id, keys in raw.items():
        if isinstance(keys, (list, tuple, set)):
            normalized[str(provider_id)] = [str(key).strip() for key in keys if str(key).strip()]

    try:
        saved = worker.settings_manager.load_full_session_settings()
    except Exception:
        saved = None
    saved_active = saved.get("active_keys_by_provider") if isinstance(saved, dict) else None
    if isinstance(saved_active, dict):
        for provider_id, keys in saved_active.items():
            if str(provider_id) in normalized:
                continue
            if isinstance(keys, (list, tuple, set)):
                normalized[str(provider_id)] = [str(key).strip() for key in keys if str(key).strip()]
    return normalized


def _api_key_for_provider(worker, provider_id: str, explicit_key: Any = None, attempt_index: int = 0) -> str:
    explicit = str(explicit_key or "").strip()
    if explicit:
        return explicit

    if not api_config.provider_requires_api_key(provider_id):
        return api_config.provider_placeholder_api_key(provider_id)

    if provider_id == str(getattr(worker, "api_provider_name", "") or ""):
        current_key = str(getattr(worker, "api_key", "") or "").strip()
        if current_key:
            return current_key

    keys_by_provider = _active_keys_by_provider(worker)
    keys = keys_by_provider.get(provider_id) or []
    if keys:
        return keys[attempt_index % len(keys)]

    return ""


def _normalize_provider_specs(worker) -> list[dict[str, Any]]:
    raw = getattr(worker, "parallel_provider_list", None)
    if raw is None:
        raw = getattr(worker, "parallel_providers", None)

    items: list[Any]
    if isinstance(raw, str):
        items = _split_csv(raw)
    elif isinstance(raw, dict):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = []

    specs: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            provider_id, _, model_name = item.partition(":")
            specs.append({"provider": provider_id.strip(), "model": model_name.strip()})
        elif isinstance(item, dict):
            specs.append(dict(item))
    return specs


def _primary_provider_spec(worker) -> dict[str, Any]:
    return {
        "provider": str(getattr(worker, "api_provider_name", "") or ""),
        "model": str(getattr(worker, "model", "") or ""),
        "model_config": deepcopy(getattr(worker, "model_config", {}) or {}),
        "api_key": str(getattr(worker, "api_key", "") or ""),
        "label": "primary",
    }


def _normalize_pass_specs(worker) -> list[dict[str, Any]]:
    raw = getattr(worker, "multi_pass_variants", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None

    if isinstance(raw, list):
        variants = [dict(item) for item in raw if isinstance(item, dict)]
        if variants:
            return variants

    count = _safe_int(
        getattr(worker, "multi_pass_count", getattr(worker, "multi_pass_chapter_count", 3)),
        default=3,
        minimum=1,
        maximum=8,
    )
    base_temperature = _safe_float(getattr(worker, "temperature", None))
    if base_temperature is None:
        base_temperature = 1.0

    raw_temperatures = getattr(worker, "multi_pass_temperatures", None)
    if isinstance(raw_temperatures, str):
        temperatures = [_safe_float(item) for item in _split_csv(raw_temperatures)]
    elif isinstance(raw_temperatures, (list, tuple)):
        temperatures = [_safe_float(item) for item in raw_temperatures]
    else:
        temperatures = []
    temperatures = [temp for temp in temperatures if temp is not None]

    if not temperatures:
        candidates = [
            base_temperature,
            min(2.0, base_temperature + 0.2),
            max(0.0, base_temperature - 0.2),
            min(2.0, base_temperature + 0.4),
            max(0.0, base_temperature - 0.4),
        ]
        temperatures = candidates[:count]

    variants = []
    for index in range(count):
        temp = temperatures[index % len(temperatures)]
        variants.append(
            {
                "label": f"pass-{index + 1}",
                "temperature": temp,
                "temperature_override_enabled": True,
            }
        )
    return variants


def _build_attempts(worker) -> list[ProviderAttempt]:
    parallel_enabled = bool(getattr(worker, "parallel_providers_enabled", False))
    multi_pass_enabled = bool(
        getattr(worker, "multi_pass_enabled", False)
        or getattr(worker, "multi_pass_chapter_translation", False)
    )

    provider_specs = _normalize_provider_specs(worker) if parallel_enabled else []
    include_primary = bool(getattr(worker, "parallel_include_primary", True))
    if include_primary or not provider_specs:
        provider_specs.insert(0, _primary_provider_spec(worker))

    pass_specs = _normalize_pass_specs(worker) if multi_pass_enabled else [{"label": "single"}]
    max_attempts = _safe_int(getattr(worker, "translation_orchestration_max_attempts", 8), 8, minimum=1, maximum=32)

    attempts: list[ProviderAttempt] = []
    seen = set()
    single_profile_attempts = set()
    single_profile_logged = set()
    for provider_index, provider_spec in enumerate(provider_specs):
        provider_id = str(provider_spec.get("provider") or provider_spec.get("provider_id") or "").strip()
        if not provider_id:
            continue

        provider_info = _provider_info(provider_id)
        if not isinstance(provider_info, dict):
            provider_info = {}
        fallback_config = provider_spec.get("model_config")
        if not isinstance(fallback_config, dict):
            fallback_config = None
        model_name, model_config = _resolve_model(
            provider_id,
            provider_spec.get("model") or provider_spec.get("model_name") or provider_spec.get("model_id"),
            fallback_model_config=fallback_config,
        )
        api_key = _api_key_for_provider(worker, provider_id, provider_spec.get("api_key"), provider_index)
        if not api_key:
            _post_log(worker, f"[ORCH] Provider '{provider_id}' skipped: no active API key/session.")
            continue

        single_profile_limited = _provider_single_profile_fanout_limited(provider_id, provider_info)
        profile_key = (
            provider_id,
            str(
                provider_spec.get("profile")
                or provider_spec.get("profile_id")
                or provider_spec.get("browser_profile")
                or api_key
                or "default"
            ),
        )
        for pass_index, pass_spec in enumerate(pass_specs, start=1):
            label = str(provider_spec.get("label") or provider_id)
            pass_label = str(pass_spec.get("label") or f"pass-{pass_index}")
            if len(pass_specs) > 1:
                label = f"{label}:{pass_label}"

            temperature = _safe_float(pass_spec.get("temperature", provider_spec.get("temperature")))
            temp_override = pass_spec.get(
                "temperature_override_enabled",
                provider_spec.get("temperature_override_enabled"),
            )
            if temp_override is not None:
                temp_override = bool(temp_override)

            key = (provider_id, model_config.get("id"), api_key, label, temperature)
            if key in seen:
                continue
            seen.add(key)

            if single_profile_limited and profile_key in single_profile_attempts:
                if profile_key not in single_profile_logged:
                    _post_log(
                        worker,
                        f"[ORCH] Provider '{provider_id}' fan-out collapsed for one stateful/browser profile.",
                    )
                    single_profile_logged.add(profile_key)
                continue

            attempts.append(
                ProviderAttempt(
                    provider_id=provider_id,
                    model_name=model_name,
                    model_config=model_config,
                    api_key=api_key,
                    label=label,
                    temperature=temperature,
                    temperature_override_enabled=temp_override,
                    prompt_prefix=str(pass_spec.get("prompt_prefix") or provider_spec.get("prompt_prefix") or ""),
                    prompt_suffix=str(pass_spec.get("prompt_suffix") or provider_spec.get("prompt_suffix") or ""),
                    pass_index=pass_index,
                )
            )
            if single_profile_limited:
                single_profile_attempts.add(profile_key)
            if len(attempts) >= max_attempts:
                return attempts

    return attempts


def _apply_prompt_overrides(prompt: str, attempt: ProviderAttempt) -> str:
    parts = []
    if attempt.prompt_prefix.strip():
        parts.append(attempt.prompt_prefix.strip())
    parts.append(prompt)
    if attempt.prompt_suffix.strip():
        parts.append(attempt.prompt_suffix.strip())
    return "\n\n".join(parts)


def _debug_context(worker, operation_context: dict | None):
    context_manager = getattr(worker, "debug_operation_context", None)
    if callable(context_manager):
        return context_manager(operation_context or {})
    return nullcontext()


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def _cleanup_handler(handler) -> None:
    cleanup = getattr(handler, "_close_thread_session_internal", None)
    if not callable(cleanup):
        return
    try:
        result = cleanup()
        if inspect.isawaitable(result):
            await result
    except Exception:
        return


async def _run_attempt(worker, attempt: ProviderAttempt, prompt: str, log_prefix: str, call_kwargs: dict) -> ProviderAttemptResult:
    provider_info = _provider_info(attempt.provider_id)
    if not provider_info:
        error = ModelNotFoundError(f"Provider '{attempt.provider_id}' not found")
        return ProviderAttemptResult(attempt=attempt, error=f"{type(error).__name__}: {error}", exception=error)

    handler_class_name = provider_info.get("handler_class")
    started_at = time.perf_counter()
    handler = None
    try:
        handler_class = get_api_handler_class(handler_class_name)
        proxy = _ProviderWorkerProxy(worker, attempt, provider_info)
        handler = handler_class(proxy)
        proxy_settings = getattr(worker, "proxy_settings", None)
        if proxy_settings is None:
            proxy_settings = getattr(worker, "session_settings", {}).get("proxy_settings") if hasattr(worker, "session_settings") else None
        if proxy_settings is None:
            proxy_settings = getattr(worker, "proxy_settings", None)
        if not handler.setup_client(_ApiKeyHolder(attempt.api_key), proxy_settings=proxy_settings):
            raise ValueError(f"Failed to initialize handler {handler_class_name}")

        attempt_prompt = _apply_prompt_overrides(prompt, attempt)
        result = handler.execute_api_call(attempt_prompt, f"{log_prefix} [{attempt.label}]", **call_kwargs)
        text = await _maybe_await(result)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        return ProviderAttemptResult(attempt=attempt, text=str(text or ""), elapsed_ms=elapsed_ms)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        return ProviderAttemptResult(
            attempt=attempt,
            error=f"{type(exc).__name__}: {exc}",
            elapsed_ms=elapsed_ms,
            exception=exc,
        )
    finally:
        if handler is not None:
            await _cleanup_handler(handler)


def _score_result(result: ProviderAttemptResult) -> int:
    text = result.text.strip()
    if not text:
        return -1
    score = len(text)
    lower = text.lower()
    if "```" in text:
        score -= 200
    if "as an ai" in lower or "i cannot" in lower:
        score -= 500
    if "<p" in lower or "<body" in lower:
        score += 100
    return score


def _safe_filename(value: str, fallback: str = "item") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    text = text.strip("._-")
    return text[:120] or fallback


def _variant_output_dir(worker) -> Path | None:
    root = None
    project_manager = getattr(worker, "project_manager", None)
    if project_manager and getattr(project_manager, "project_folder", None):
        root = project_manager.project_folder
    if not root:
        root = getattr(worker, "output_folder", None)
    if not root:
        return None
    return Path(root) / "_translation_variants"


def _save_attempt_results(worker, operation_context: dict | None, results: list[ProviderAttemptResult]) -> None:
    output_dir = _variant_output_dir(worker)
    if output_dir is None:
        return

    task_id = _safe_filename((operation_context or {}).get("task_id"), "task")
    action = _safe_filename((operation_context or {}).get("action"), "translate")
    chapter = _safe_filename((operation_context or {}).get("chapter"), "chapter")
    session_dir = output_dir / f"{task_id}_{action}_{chapter}"
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    manifest = []
    for index, result in enumerate(results, start=1):
        attempt = result.attempt
        stem = _safe_filename(f"{index:02d}_{attempt.label}_{attempt.provider_id}_{attempt.model_config.get('id')}")
        text_path = session_dir / f"{stem}.txt"
        meta_path = session_dir / f"{stem}.json"
        try:
            text_path.write_text(result.text or "", encoding="utf-8")
            meta = {
                "label": attempt.label,
                "provider": attempt.provider_id,
                "model": attempt.model_name,
                "model_id": attempt.model_config.get("id"),
                "elapsed_ms": result.elapsed_ms,
                "ok": result.ok,
                "error": result.error,
                "temperature": attempt.temperature,
                "pass_index": attempt.pass_index,
            }
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            manifest.append({**meta, "text_file": text_path.name, "meta_file": meta_path.name})
        except OSError:
            continue

    try:
        (session_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "operation_context": operation_context or {},
                    "results": manifest,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
    except OSError:
        return


_EXCEPTION_PRIORITY: tuple[tuple[type[Exception], int], ...] = (
    (OperationCancelledError, 100),
    (RateLimitExceededError, 90),
    (LocationBlockedError, 85),
    (ModelNotFoundError, 80),
    (TemporaryRateLimitError, 75),
    (ContentFilterError, 70),
    (PartialGenerationError, 65),
    (ValidationFailedError, 60),
    (NetworkError, 50),
)


def _exception_priority(exc: Exception) -> int:
    for exc_type, priority in _EXCEPTION_PRIORITY:
        if isinstance(exc, exc_type):
            return priority
    return 0


def _dominant_exception(results: list[ProviderAttemptResult]) -> Exception | None:
    grouped: dict[type[Exception], dict[str, Any]] = {}
    for order, result in enumerate(results):
        exc = result.exception
        if exc is None:
            continue
        exc_type = type(exc)
        group = grouped.setdefault(
            exc_type,
            {
                "count": 0,
                "exception": exc,
                "priority": _exception_priority(exc),
                "first_order": order,
            },
        )
        group["count"] += 1

    if not grouped:
        return None

    selected = max(
        grouped.values(),
        key=lambda group: (
            group["count"],
            group["priority"],
            -group["first_order"],
        ),
    )
    return selected["exception"]


def _select_result(strategy: str, results: list[ProviderAttemptResult]) -> ProviderAttemptResult:
    successful = [result for result in results if result.ok]
    if not successful:
        dominant = _dominant_exception(results)
        if dominant is not None:
            raise dominant
        errors = "; ".join(result.error for result in results if result.error)
        raise RuntimeError(errors or "All provider attempts failed")

    if strategy == "best_score":
        return max(successful, key=_score_result)
    return successful[0]


async def _run_attempt_with_index(
    index: int,
    worker,
    attempt: ProviderAttempt,
    prompt: str,
    log_prefix: str,
    call_kwargs: dict,
) -> tuple[int, ProviderAttemptResult]:
    return index, await _run_attempt(worker, attempt, prompt, log_prefix, call_kwargs)


async def _cancel_pending_attempt_tasks(tasks: list[asyncio.Task]) -> int:
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return len(pending)


async def _run_attempts_first_success(
    worker,
    attempts: list[ProviderAttempt],
    prompt: str,
    log_prefix: str,
    call_kwargs: dict,
) -> tuple[list[ProviderAttemptResult], ProviderAttemptResult | None, int]:
    tasks = [
        asyncio.create_task(_run_attempt_with_index(index, worker, attempt, prompt, log_prefix, call_kwargs))
        for index, attempt in enumerate(attempts)
    ]
    results: list[ProviderAttemptResult] = []
    selected: ProviderAttemptResult | None = None
    cancelled_count = 0

    try:
        for completed in asyncio.as_completed(tasks):
            _index, result = await completed
            results.append(result)
            if result.ok:
                selected = result
                break
    except BaseException:
        await _cancel_pending_attempt_tasks(tasks)
        raise

    if selected is not None:
        cancelled_count = await _cancel_pending_attempt_tasks(tasks)

    return results, selected, cancelled_count


def _build_synthesis_prompt(original_prompt: str, results: list[ProviderAttemptResult]) -> str:
    candidate_blocks = []
    for index, result in enumerate([item for item in results if item.ok], start=1):
        attempt = result.attempt
        candidate_blocks.append(
            "\n".join(
                [
                    f"<candidate index=\"{index}\" provider=\"{attempt.provider_id}\" model=\"{attempt.model_config.get('id', '')}\">",
                    result.text.strip(),
                    "</candidate>",
                ]
            )
        )

    return (
        "You are given several candidate translations for the same source task.\n"
        "Create one final, polished translation by preserving all required formatting, HTML tags, markers, JSON shape, and source coverage.\n"
        "Do not add explanations, markdown fences, or comments. Return only the final answer in the same format requested by the original task.\n\n"
        "<original_task_prompt>\n"
        f"{original_prompt}\n"
        "</original_task_prompt>\n\n"
        "<candidate_translations>\n"
        + "\n\n".join(candidate_blocks)
        + "\n</candidate_translations>"
    )


async def execute_orchestrated_api_call(
    worker,
    prompt: str,
    log_prefix: str,
    *,
    task_info,
    operation_context: dict | None,
    call_kwargs: dict,
) -> str:
    attempts = _build_attempts(worker)
    multi_pass_enabled = bool(
        getattr(worker, "multi_pass_enabled", False)
        or getattr(worker, "multi_pass_chapter_translation", False)
    )
    if len(attempts) <= 1 and not multi_pass_enabled:
        # No meaningful fan-out. Let the normal path keep using the warm handler.
        return await worker.api_handler_instance.execute_api_call(prompt, log_prefix, **call_kwargs)

    strategy_setting = (
        getattr(worker, "multi_pass_strategy", None)
        if multi_pass_enabled
        else getattr(worker, "parallel_provider_strategy", None)
    )
    strategy = str(strategy_setting or "merge").strip().lower()
    if strategy not in {"first_success", "best_score", "merge", "synthesis"}:
        strategy = "merge"

    _post_log(
        worker,
        f"[ORCH] Running {len(attempts)} provider/pass attempt(s), strategy={strategy}.",
    )
    cancelled_count = 0
    selected_first_success = None
    with _debug_context(worker, operation_context):
        if strategy == "first_success":
            results, selected_first_success, cancelled_count = await _run_attempts_first_success(
                worker,
                attempts,
                prompt,
                log_prefix,
                call_kwargs,
            )
        else:
            results = await asyncio.gather(
                *[_run_attempt(worker, attempt, prompt, log_prefix, call_kwargs) for attempt in attempts]
            )

    _save_attempt_results(worker, operation_context, results)
    success_count = sum(1 for result in results if result.ok)
    if cancelled_count:
        _post_log(
            worker,
            f"[ORCH] Attempts completed: {success_count}/{len(results)} successful, "
            f"{cancelled_count} pending cancelled.",
        )
    else:
        _post_log(worker, f"[ORCH] Attempts completed: {success_count}/{len(results)} successful.")

    if selected_first_success is not None:
        _post_log(worker, f"[ORCH] Selected result: {selected_first_success.attempt.label}.")
        return selected_first_success.text

    if strategy in {"merge", "synthesis"} and success_count > 1:
        synthesis_prompt = _build_synthesis_prompt(prompt, results)
        synthesis_kwargs = dict(call_kwargs)
        synthesis_kwargs["use_stream"] = False
        synthesis_kwargs["allow_incomplete"] = False
        _post_log(worker, "[ORCH] Synthesizing final chapter from candidate translations.")
        with _debug_context(worker, {**(operation_context or {}), "action": "translation_synthesis"}):
            return await worker.api_handler_instance.execute_api_call(
                synthesis_prompt,
                f"{log_prefix} [synthesis]",
                **synthesis_kwargs,
            )

    selected = _select_result("best_score" if strategy == "best_score" else "first_success", results)
    _post_log(worker, f"[ORCH] Selected result: {selected.attempt.label}.")
    return selected.text
