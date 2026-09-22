# gemini_translator/core/worker_helpers/content_filter_fallback.py
"""Reactive fallback when the main model returns a content block.

On a content block (ContentFilterError, or PartialGenerationError with a
SAFETY/PROHIBITED_CONTENT/CONTENT_FILTER reason) the blocked chunk/chapter is re-sent to a
user-chosen fallback provider+model, drawing from the pool of green (non-
exhausted) keys for that provider. A content block from the fallback is
terminal (task goes to error, as before). Transient errors rotate through
the green pool within the same budget the main model uses.
"""

from gemini_translator.api.errors import (
    ContentFilterError,
    NetworkError,
    PartialGenerationError,
    RateLimitExceededError,
    TemporaryRateLimitError,
)

# Content-block reasons reported by handlers via PartialGenerationError.reason:
# Gemini's own finish reasons, and CONTENT_FILTER from OpenAI-compatible
# handlers (``finish_reason: "content_filter"``, e.g. OmniRoute).
SAFETY_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "CONTENT_FILTER"}

# Transient errors during a fallback attempt get the same overall budget as the
# main model. Mirrors gemini_translator.core.worker_helpers.error_analyzer
# .ErrorAnalyzer.TOTAL_ATTEMPTS_LIMIT (kept as a local constant to avoid an
# import cycle through the worker stack).
TRANSIENT_RETRY_BUDGET = 4

_TRANSIENT_TYPES = (NetworkError, TemporaryRateLimitError, RateLimitExceededError)


class NoFallbackKeysError(Exception):
    """Raised when the chosen fallback provider has no green (active) keys."""


def is_content_block_exception(exc) -> bool:
    if isinstance(exc, ContentFilterError):
        return True
    if isinstance(exc, PartialGenerationError):
        return str(getattr(exc, "reason", "") or "").upper() in SAFETY_REASONS
    return False


def is_transient_exception(exc) -> bool:
    return isinstance(exc, _TRANSIENT_TYPES)


def fallback_decision(exc) -> str:
    """Return 'block' | 'transient' | 'fatal' for a fallback-attempt error."""
    if is_content_block_exception(exc):
        return "block"
    if is_transient_exception(exc):
        return "transient"
    return "fatal"


def green_keys_for_provider(settings_manager, provider_id, model_id) -> list:
    """All green (non-limit-active) keys for provider_id, in stored order."""
    if settings_manager is None or not provider_id:
        return []
    try:
        statuses = settings_manager.load_key_statuses()
    except Exception:
        return []
    pool = []
    for key_info in statuses or []:
        if str(key_info.get("provider")) != str(provider_id):
            continue
        key = str(key_info.get("key") or "").strip()
        if not key:
            continue
        try:
            blocked = settings_manager.is_key_limit_active(key_info, model_id)
        except Exception:
            blocked = False
        if not blocked:
            pool.append(key)
    return pool


def run_sync_fallback_loop(*, pool, call_for_key, log=None):
    """Run a synchronous fallback over a green-key pool.

    call_for_key(api_key) -> str, may raise. Content block -> reraise (terminal);
    transient -> rotate within TRANSIENT_RETRY_BUDGET; anything else -> reraise.
    """
    if not pool:
        raise NoFallbackKeysError("Fallback pool is empty")
    last_exc = None
    for index in range(TRANSIENT_RETRY_BUDGET):
        api_key = pool[index % len(pool)]
        try:
            return call_for_key(api_key)
        except Exception as exc:  # noqa: BLE001 - classified below
            decision = fallback_decision(exc)
            if decision == "block" or decision == "fatal":
                raise
            last_exc = exc
            if callable(log):
                log(f"🛡️🔁 Временная ошибка резерва ({type(exc).__name__}), ротация ключа…")
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Fallback produced no result.")


from gemini_translator.core.worker_helpers.provider_orchestrator import (  # noqa: E402
    ProviderAttempt,
    _resolve_model,
    _run_attempt,
)


def fallback_enabled(worker) -> bool:
    return bool(getattr(worker, "content_filter_fallback_enabled", False))


def _post(worker, message: str) -> None:
    fn = getattr(worker, "_post_event", None)
    if callable(fn):
        fn("log_message", {"message": message})


def _fallback_temperature(worker):
    if not bool(getattr(worker, "content_filter_fallback_temperature_override", True)):
        return None
    try:
        return float(getattr(worker, "content_filter_fallback_temperature", None))
    except (TypeError, ValueError):
        return None


async def run_content_filter_fallback(
    worker, prompt, log_prefix, *, task_info, operation_context, call_kwargs
):
    """Re-run a content-blocked prompt against the configured fallback provider.

    Returns the fallback translation text. Raises the content-block exception if
    the fallback is also blocked, NoFallbackKeysError if the provider has no
    green keys, or the last transient exception after the budget is exhausted.
    """
    provider_id = str(getattr(worker, "content_filter_fallback_provider", "") or "").strip()
    model_name = str(getattr(worker, "content_filter_fallback_model", "") or "").strip()
    if not provider_id:
        raise NoFallbackKeysError("Резервный провайдер не выбран.")

    resolved_name, model_config = _resolve_model(provider_id, model_name)
    model_id = model_config.get("id") or resolved_name
    pool = green_keys_for_provider(getattr(worker, "settings_manager", None), provider_id, model_id)
    if not pool:
        raise NoFallbackKeysError(f"Нет зелёных ключей для провайдера '{provider_id}'.")

    attempt_kwargs = dict(
        provider_id=provider_id,
        model_name=resolved_name,
        model_config=model_config,
        label="content-filter-fallback",
        temperature=_fallback_temperature(worker),
        temperature_override_enabled=bool(
            getattr(worker, "content_filter_fallback_temperature_override", True)
        ),
        thinking_enabled=bool(getattr(worker, "content_filter_fallback_thinking_enabled", False)),
        thinking_budget=getattr(worker, "content_filter_fallback_thinking_budget", None),
        thinking_level=getattr(worker, "content_filter_fallback_thinking_level", None),
    )

    _post(
        worker,
        f"🛡️➡️ Контент заблокирован. Резерв: {provider_id}/{model_id} "
        f"({len(pool)} зелёных ключей).",
    )

    last_exc = None
    for index in range(TRANSIENT_RETRY_BUDGET):
        api_key = pool[index % len(pool)]
        attempt = ProviderAttempt(api_key=api_key, **attempt_kwargs)
        result = await _run_attempt(worker, attempt, prompt, log_prefix, call_kwargs)
        if result.ok:
            _post(worker, f"🛡️✅ Резерв перевёл заблокированный фрагмент ({provider_id}/{model_id}).")
            return result.text
        exc = result.exception or RuntimeError(result.error or "fallback failed")
        decision = fallback_decision(exc)
        if decision == "block":
            _post(worker, "🛡️❌ Резерв тоже вернул блокировку — задача уходит в ошибку.")
            raise exc
        if decision == "fatal":
            raise exc
        last_exc = exc
        _post(worker, f"🛡️🔁 Временная ошибка резерва ({type(exc).__name__}), ротация ключа…")
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Резерв не дал результата.")
