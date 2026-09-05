"""Build a working QA service and coordinator from real project state."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
import json
from pathlib import Path
import zipfile

from ..utils.epub_json import build_html_document_model, build_translation_payload
from .addition_detector import AdditionDetector
from .alignment import MonotonicAligner
from .capabilities import QaCapabilitySettings
from .coverage_service import (
    CoverageRequest,
    DefaultCandidateFilter,
    DefaultCoverageMetricsCollector,
    SemanticCoverageService,
)
from .embeddings.cache import CachedEmbeddingProvider, EmbeddingCache
from .embeddings.factory import (
    EmbeddingProviderConfig,
    EmbeddingUnavailableError,
    create_embedding_provider,
)
from .glossary_context import GlossaryTerm, glossary_terms_from_project_entries
from .journal import QaJournal, QaJournalError
from .language_rules import (
    LanguageRuleCache,
    LanguageRuleService,
    LanguageRuleUnavailable,
    LanguageToolHttpProvider,
)
from .language_validation import LanguageQualityPipeline
from .llm.answer_cache import QaAnswerCache
from .llm.completion import ExistingHandlerCompletionClient, QaModelSelection
from .russian_nlp import RussianNlpService, SlovnetProvider, load_runtime
from .llm.omission_repairer import OmissionRepairer
from .llm.omission_verifier import OmissionVerifier
from .models import AlignmentConfig, GlossaryRule
from .repair_store import RepairStore
from .repair_validator import RepairValidator
from .service import ChapterQaRequest, TranslationQualityService
from .settings import QaSettings, language_chunk_chars_for
from .structural_repair import StructuralRepairEngine


DEFAULT_EMBEDDING_MODELS = {
    "gemini": "gemini-embedding-001",
    "openai_compatible": "text-embedding-3-small",
}
SOURCE_DOCUMENT_PREFIX = "source::"


class QaAssemblyError(RuntimeError):
    """Raised when translation QA cannot be assembled from the current state."""


class UnavailableEmbeddingProvider:
    """Stand-in provider that puts coverage into its documented limited mode.

    Without embeddings the cascade must still run its statistical and language
    stages and say so in the report, rather than disappearing silently.
    """

    name = "unavailable"

    async def embed(self, request):
        raise EmbeddingUnavailableError(())


@dataclass(frozen=True, slots=True)
class ProjectQaPaths:
    """The three durable locations one project reserves for quality control."""

    journal: Path
    backups: Path
    embedding_cache: Path
    rule_cache: Path
    answer_cache: Path
    cometkiwi_models: Path

    @classmethod
    def for_project(cls, project_manager) -> "ProjectQaPaths":
        cache_dir = Path(project_manager.get_translation_qa_embedding_cache_dir())
        return cls(
            journal=Path(project_manager.get_translation_qa_journal_path()),
            backups=Path(project_manager.get_translation_qa_backup_dir()),
            embedding_cache=cache_dir,
            # The rule cache lives beside the embedding cache: both are
            # disposable and neither holds anything the user would miss.
            rule_cache=cache_dir.with_name(cache_dir.name + "_rules"),
            # Model answers about text nobody changed, beside the other two
            # disposable caches.
            answer_cache=cache_dir.with_name(cache_dir.name + "_answers"),
            # Estimator weights are large and shared between projects only by
            # accident, so they live under the project like everything else.
            cometkiwi_models=cache_dir.with_name("translation_qa_cometkiwi"),
        )


def load_project_glossary_terms(project_folder: Path | str) -> tuple[GlossaryTerm, ...]:
    """Read the project glossary, returning nothing when it is absent or broken."""
    path = Path(project_folder) / "project_glossary.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    if not isinstance(payload, list):
        return ()
    return glossary_terms_from_project_entries(payload)


def build_embedding_provider(
    qa_settings: QaSettings,
    session_factory: Callable[[], object],
    api_keys_by_provider: Mapping[str, str],
    cache_dir: Path | None = None,
    preprocessing_identity: str | None = None,
    key_health=None,
):
    """Create the configured embedding provider, or raise if none is usable.

    An explicitly chosen key and address in the settings always win; the running
    session's own key is only a fallback, so a book can be checked with one
    provider while being translated by another.

    A cache directory is only used together with the segmentation identity the
    units were produced under: a cached vector must never survive a change of
    segmentation.
    """
    if qa_settings.embedding_provider == "local_onnx":
        provider = create_embedding_provider(
            EmbeddingProviderConfig(
                kind="local_onnx",
                model_dir=str(local_embedding_model_root()),
                intra_op_threads=qa_settings.slovnet_cpu_threads,
            ),
            session_factory,
        )
        return _cached(provider, qa_settings, cache_dir, preprocessing_identity)

    chosen = qa_settings.embedding_api_key
    gemini_keys = (
        (chosen,)
        if chosen
        else _keys(api_keys_by_provider.get("google"))
        or _keys(api_keys_by_provider.get("gemini"))
    )
    openai_keys = (chosen,) if chosen else _keys(api_keys_by_provider.get("openai"))
    openai_base = qa_settings.embedding_base_url or str(
        api_keys_by_provider.get("openai_base_url") or ""
    )
    if qa_settings.embedding_provider == "gemini":
        openai_keys = ()
    elif qa_settings.embedding_provider == "openai_compatible":
        gemini_keys = ()
    candidates: list[EmbeddingProviderConfig] = []
    if qa_settings.embedding_provider in {"auto", "gemini"} and gemini_keys:
        # One provider with several keys and a shared cache. Temporary limits
        # wait on the current key before considering a reserve key.
        candidates.append(
            EmbeddingProviderConfig(
                kind="gemini",
                api_key=gemini_keys[0],
                api_keys=tuple(gemini_keys),
                model=qa_settings.embedding_model or DEFAULT_EMBEDDING_MODELS["gemini"],
                key_health=key_health,
            )
        )
    if (
        qa_settings.embedding_provider in {"auto", "openai_compatible"}
        and openai_base
        and openai_keys
    ):
        candidates.append(
            EmbeddingProviderConfig(
                kind="openai_compatible",
                api_key=openai_keys[0],
                base_url=openai_base,
                model=(
                    qa_settings.embedding_model
                    or DEFAULT_EMBEDDING_MODELS["openai_compatible"]
                ),
            )
        )
    if not candidates:
        raise EmbeddingUnavailableError(())
    config = (
        candidates[0]
        if len(candidates) == 1
        else EmbeddingProviderConfig(kind="auto", providers=tuple(candidates))
    )
    return _cached(
        create_embedding_provider(config, session_factory),
        qa_settings,
        cache_dir,
        preprocessing_identity,
    )


def _keys(value) -> tuple[str, ...]:
    """Accept one key or an ordered list of them, dropping anything empty."""
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(
            item.strip() for item in value if isinstance(item, str) and item.strip()
        )
    return ()


def _cached(provider, qa_settings, cache_dir, preprocessing_identity):
    """Wrap one provider in the project cache, keyed by the segmentation used."""
    if cache_dir is None:
        return provider
    identity = preprocessing_identity or _extractor(
        qa_settings.effective_capabilities()
    ).preprocessing_identity
    return CachedEmbeddingProvider(
        provider, EmbeddingCache(cache_dir), preprocessing_identity=identity
    )


def local_embedding_model_root() -> Path:
    """Return the shared directory the optional local embedding model lives in."""
    from ..utils.settings import default_settings_dir

    return Path(default_settings_dir()) / "embeddings" / "multilingual-e5"


def local_embedding_model_state() -> tuple[bool, Path]:
    """Report whether the local model is present, and where it is looked for."""
    root = local_embedding_model_root()
    installed = (root / "model.onnx").is_file() and (root / "tokenizer.json").is_file()
    return installed, root


def build_translation_quality_service(
    *,
    project_manager,
    qa_settings: QaSettings,
    handler_factory: Callable[[QaModelSelection], object],
    embedding_provider,
    session_id: str,
    target_language: str = "ru",
    rule_session_factory=None,
    event_sink=None,
) -> TranslationQualityService:
    """Assemble the full cascade around one project's durable state."""
    paths = ProjectQaPaths.for_project(project_manager)
    # The decision history is read first: a damaged journal must stop the setup
    # before anything else is built around it.
    journal = _load_journal(paths.journal, project_manager)
    capabilities = qa_settings.effective_capabilities()
    client = ExistingHandlerCompletionClient(handler_factory, event_sink)
    coverage = SemanticCoverageService(
        extractor=_extractor(capabilities),
        provider=embedding_provider,
        aligner=MonotonicAligner(AlignmentConfig()),
        candidate_filter=DefaultCandidateFilter(),
        metrics_collector=DefaultCoverageMetricsCollector(),
    )
    return TranslationQualityService(
        analysis_identity=_extractor(capabilities).preprocessing_identity,
        russian_nlp=build_russian_nlp_service(qa_settings, capabilities),
        language_rules=build_language_rule_service(
            qa_settings,
            capabilities,
            session_factory=rule_session_factory,
            cache_dir=paths.rule_cache,
            preprocessing_version=_extractor(capabilities).preprocessing_identity,
            target_language=target_language,
        ),
        coverage=coverage,
        verifier=OmissionVerifier(client),
        repairer=OmissionRepairer(client),
        repair_engine=StructuralRepairEngine(target_language, capabilities),
        repair_validator=RepairValidator(client),
        store=RepairStore(paths.backups, session_id=session_id),
        journal=journal,
        journal_path=paths.journal,
        request_counter=client,
        additions=AdditionDetector(client),
        language=LanguageQualityPipeline(
            client, diagnosis_cache=QaAnswerCache(paths.answer_cache)
        ),
    )


def build_language_rule_service(
    qa_settings: QaSettings,
    capabilities: QaCapabilitySettings,
    *,
    session_factory=None,
    cache_dir: Path | None = None,
    preprocessing_version: str = "",
    target_language: str = "ru",
) -> LanguageRuleService | None:
    """Build the rule service only when the user both enabled and configured it."""
    if not capabilities.language_tool_enabled:
        return None
    if session_factory is None:
        session_factory = aiohttp_session_factory()
    try:
        provider = LanguageToolHttpProvider(
            endpoint=qa_settings.language_tool_endpoint,
            session_factory=session_factory,
        )
    except LanguageRuleUnavailable:
        # An enabled capability with an unusable address must still produce a
        # service, so the chapter result explains why there were no hints.
        provider = None
    return LanguageRuleService(
        provider=provider,
        cache=LanguageRuleCache(cache_dir) if cache_dir is not None else None,
        language=target_language,
        disabled_rule_ids=qa_settings.language_tool_disabled_rules,
        preprocessing_version=preprocessing_version,
    )


def slovnet_model_root() -> Path:
    """Return the shared directory the optional Russian models live in."""
    from ..utils.settings import default_settings_dir

    return Path(default_settings_dir()) / "slovnet"


def build_russian_nlp_service(
    qa_settings: QaSettings,
    capabilities: QaCapabilitySettings,
    *,
    model_root: Path | None = None,
) -> RussianNlpService | None:
    """Build the local NLP service only for a capability that is switched on.

    The runtime is loaded lazily on the first chapter, so an enabled but
    uninstalled analyzer costs one warning and nothing else.
    """

    if not capabilities.slovnet_enabled:
        return None
    root = model_root if model_root is not None else slovnet_model_root()
    return RussianNlpService(
        provider_factory=lambda: SlovnetProvider(
            load_runtime(root, cpu_threads=qa_settings.slovnet_cpu_threads),
            cpu_threads=qa_settings.slovnet_cpu_threads,
            batch_size=qa_settings.slovnet_batch_size,
        )
    )


def build_chapter_qa_request(
    event,
    *,
    project_manager,
    qa_settings: QaSettings,
    model: QaModelSelection,
    session_id: str,
    source_language_resolver: Callable[[str], str] | None = None,
    target_language: str = "ru",
) -> ChapterQaRequest | None:
    """Turn one saved-chapter event into a full QA request, or skip the chapter.

    Missing files, an unreadable EPUB, or an empty chapter are ordinary states
    during a translation run, not failures: they simply produce no request.
    """

    translated_path = Path(event.translated_path)
    source_html = _read_source_chapter(event)
    if source_html is None:
        return None
    try:
        translated_html = translated_path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not source_html.strip() or not translated_html.strip():
        return None

    source_document_id = f"{SOURCE_DOCUMENT_PREFIX}{event.chapter_id}"
    source_payload = build_translation_payload(
        build_html_document_model(source_html, document_id=source_document_id),
        document_id=source_document_id,
    )
    target_payload = build_translation_payload(
        build_html_document_model(translated_html, document_id=event.chapter_id),
        document_id=event.chapter_id,
    )
    source_language = event.source_language
    if source_language in {"", "auto"} and source_language_resolver is not None:
        source_language = source_language_resolver(source_html) or "en"
    elif source_language in {"", "auto"}:
        source_language = "en"

    glossary = load_project_glossary_terms(project_manager.project_folder)
    embedding_model = qa_settings.embedding_model or DEFAULT_EMBEDDING_MODELS["gemini"]
    return ChapterQaRequest(
        chapter_id=event.chapter_id,
        coverage_request=CoverageRequest(
            chapter_id=event.chapter_id,
            source_payload=source_payload,
            target_payload=target_payload,
            source_language=source_language,
            target_language=event.target_language or target_language,
            embedding_model=embedding_model,
            glossary=tuple(
                GlossaryRule(term.original, term.policy) for term in glossary
            ),
        ),
        translated_path=translated_path,
        model=model,
        session_id=session_id,
        glossary=glossary,
        source_text_by_block=_source_text_by_block(source_payload),
    )


def attach_chapter_qa_coordinator(
    app,
    *,
    project_manager,
    settings_manager,
    handler_factory: Callable[[QaModelSelection], object],
    session_id: str,
    api_keys_by_provider: Mapping[str, str],
    session_factory: Callable[[], object],
    translation_provider: str,
    translation_model: str,
    epub_path: str = "",
    source_language_resolver: Callable[[str], str] | None = None,
    log=None,
):
    """Build and attach the coordinator the workers report saved chapters to.

    Returns ``None`` when quality control is switched off or cannot be built.
    Translation must run normally in both cases, so every failure here is
    reported and swallowed rather than raised into a session start.
    """

    from ..core.chapter_qa_coordinator import ChapterQaCoordinator

    detach_chapter_qa_coordinator(app)
    try:
        qa_settings = settings_manager.get_qa_settings()
    except Exception:  # noqa: BLE001 - unreadable settings mean default QA
        qa_settings = QaSettings()
    if not (
        qa_settings.check_completeness_after_chapter
        or qa_settings.check_language_after_chapter
    ):
        return None

    paths = ProjectQaPaths.for_project(project_manager)
    keys_for_embeddings = embedding_key_pool(
        settings_manager, qa_settings, api_keys_by_provider
    )
    if qa_settings.embedding_key_provider and not keys_for_embeddings:
        _report(
            log,
            "[QA] У провайдера "
            f"'{qa_settings.embedding_key_provider}' нет свободных ключей для "
            "эмбеддингов: смысловое сравнение отключено, языковая проверка "
            "продолжает работать.",
        )
    try:
        embedding_provider = build_embedding_provider(
            qa_settings,
            session_factory,
            keys_for_embeddings,
            paths.embedding_cache,
            _extractor(qa_settings.effective_capabilities()).preprocessing_identity,
            key_health=SettingsEmbeddingKeyHealth(
                settings_manager,
                embedding_model_for(
                    qa_settings,
                    embedding_key_namespace(qa_settings.embedding_key_provider),
                ),
                log,
            ),
        )
    except Exception as error:  # noqa: BLE001 - QA without embeddings is limited, not fatal
        _report(
            log,
            f"[QA] Семантическая проверка недоступна, остаётся языковая: {error}",
        )
        embedding_provider = UnavailableEmbeddingProvider()

    provider, model_name = qa_settings.correction_model_for(
        translation_provider, translation_model
    )
    if not provider or not model_name:
        _report(log, "[QA] Не выбрана модель для проверки качества.")
        return None
    model = QaModelSelection(provider, model_name)

    try:
        service = build_translation_quality_service(
            project_manager=project_manager,
            qa_settings=qa_settings,
            handler_factory=handler_factory,
            embedding_provider=embedding_provider,
            session_id=session_id,
        )
    except QaAssemblyError as error:
        _report(log, f"[QA] {error}")
        return None
    except Exception as error:  # noqa: BLE001 - a broken QA setup never stops translation
        _report(log, f"[QA] Не удалось собрать проверку качества: {error}")
        return None

    task_manager = getattr(app, "task_manager", None)
    coordinator = ChapterQaCoordinator(
        service=service,
        task_manager=task_manager,
        request_builder=lambda event: build_chapter_qa_request(
            event,
            project_manager=project_manager,
            qa_settings=qa_settings,
            model=model,
            session_id=session_id,
            source_language_resolver=source_language_resolver,
        ),
        options_provider=lambda: _current_options(settings_manager),
        book_events_provider=lambda: build_manual_events(
            project_manager=project_manager, epub_path=epub_path
        ),
        journal_provider=lambda: _load_journal(paths.journal, project_manager),
        pending_tasks_provider=lambda: _pending_qa_tasks(task_manager),
        analysis_identity=_extractor(
            qa_settings.effective_capabilities()
        ).preprocessing_identity,
        quality_estimator=build_quality_estimator(qa_settings, paths),
        max_concurrency=qa_settings.batch_concurrency,
        log=log,
    )
    app.qa_coordinator = coordinator
    return coordinator


# What a check must be told about the model besides which one it is.  Only
# these: a manual check keeps its own low temperature on purpose.
_SESSION_FIELDS = ("thinking_enabled", "thinking_level", "thinking_budget")


def manual_session_settings(settings_manager, proxy_settings=None) -> dict:
    """Ask the model the same way a session would, from outside a session.

    Inside a session QA is handed the session's own settings; started from the
    quality window it used to be handed the proxy and nothing else, so the
    handler fell back to the minimum thinking level named in the model config.
    Reported from a live book: the service answered «Thinking level MINIMAL is
    not supported for this model» and all 634 chapters went unchecked, while the
    same model checked chapters happily during translation.  The same check must
    not depend on where it was started from.
    """
    settings: dict[str, object] = {"proxy_settings": proxy_settings}
    try:
        saved = settings_manager.load_settings() or {}
    except Exception:  # noqa: BLE001 - a check without them still runs
        return settings
    if not isinstance(saved, Mapping):
        return settings
    for field in _SESSION_FIELDS:
        value = saved.get(field)
        # A missing budget is not a budget of None: the handler would try to
        # compare it with the model's minimum and raise.
        if value is not None:
            settings[field] = value
    return settings


def resolve_manual_qa_model(settings_manager, qa_settings: QaSettings) -> tuple[str, str]:
    """Choose the provider and model a manual check should use outside a session.

    A session hands QA the model it is translating with.  A book translated
    yesterday has no session, and the check still needs somewhere to ask: the
    explicit QA model wins, and otherwise the last model the user actually
    translated with is the closest honest guess.  An empty answer means the
    caller must say so rather than start a pass that cannot run.
    """
    provider = str(qa_settings.correction_provider or "").strip()
    model = str(qa_settings.correction_model or "").strip()
    if qa_settings.correction_model_mode == "custom" and provider and model:
        return provider, model
    try:
        from ..api import config as api_config

        providers = api_config.api_providers_view()
        saved = str(
            (settings_manager.get_last_settings() or {}).get("model") or ""
        ).strip()
        if saved:
            for provider_id, provider_config in providers.items():
                models = (provider_config or {}).get("models") or {}
                entry = models.get(saved)
                if entry is not None:
                    return str(provider_id), str(entry.get("id") or saved)
                for name, config in models.items():
                    if str((config or {}).get("id") or name) == saved:
                        return str(provider_id), saved
        # The saved model can simply have been retired — providers rename and
        # drop models, and a book translated a year ago names one that no longer
        # exists.  A provider the user has a working key for is a better answer
        # than refusing to check the book at all.
        return _provider_with_a_key(settings_manager, providers)
    except Exception:  # noqa: BLE001 - an unreadable registry is simply no answer
        return "", ""


def _provider_with_a_key(settings_manager, providers) -> tuple[str, str]:
    """The provider the user most evidently works with, and its first model.

    Most keys is a better guess than first key: a list that opens with one
    leftover key for a service tried once should not decide what checks the
    book.  Ties keep the registry's own order, so the answer is stable.
    """
    try:
        statuses = settings_manager.load_key_statuses() or ()
    except Exception:  # noqa: BLE001 - unreadable statuses mean no answer
        return "", ""
    counts: dict[str, int] = {}
    for key_info in statuses:
        provider_id = str(key_info.get("provider") or "").strip()
        if provider_id and (providers.get(provider_id) or {}).get("models"):
            counts[provider_id] = counts.get(provider_id, 0) + 1
    if not counts:
        return "", ""
    order = list(providers)
    best = max(counts, key=lambda item: (counts[item], -order.index(item)))
    models = (providers.get(best) or {}).get("models") or {}
    for name, config in models.items():
        return best, str((config or {}).get("id") or name)
    return "", ""


def green_keys(settings_manager, provider_id: str, model_id: str) -> tuple[str, ...]:
    """Every healthy key of a provider, in the order the settings list them.

    A manual pass over a book has no session pool to borrow, and one key runs
    out of its daily allowance a few chapters in; the whole list is what lets
    the pass rotate the way the translation does.
    """
    if settings_manager is None or not provider_id:
        return ()
    try:
        statuses = settings_manager.load_key_statuses() or ()
    except Exception:  # noqa: BLE001 - unreadable statuses mean no key
        return ()
    keys: list[str] = []
    for key_info in statuses:
        if str(key_info.get("provider") or "") != str(provider_id):
            continue
        key = str(key_info.get("key") or "").strip()
        if not key or key in keys:
            continue
        try:
            if settings_manager.is_key_limit_active(key_info, model_id):
                continue
        except Exception:  # noqa: BLE001 - an unreadable status is not a red key
            pass
        keys.append(key)
    return tuple(keys)


def first_green_key(settings_manager, provider_id: str, model_id: str) -> str:
    """One healthy key of a provider, or an empty string when it has none."""
    keys = green_keys(settings_manager, provider_id, model_id)
    return keys[0] if keys else ""


def build_quality_estimator(qa_settings: QaSettings, paths: "ProjectQaPaths"):
    """Build the optional quality estimator, or return None when it is off.

    ``None`` is the normal case: the estimator is an opt-in extra that needs a
    runner, weights, and an accepted licence before it may run at all.
    """
    capabilities = qa_settings.effective_capabilities()
    if not capabilities.cometkiwi_enabled:
        return None
    try:
        from .estimators.cometkiwi_client import (
            CometKiwiEstimator,
            CometKiwiRunnerConfig,
        )

        config = CometKiwiRunnerConfig(
            runner_path=qa_settings.cometkiwi_runner_path,
            model_dir=str(paths.cometkiwi_models / qa_settings.cometkiwi_model)
            if qa_settings.cometkiwi_model
            else "",
            model=qa_settings.cometkiwi_model,
            device=qa_settings.cometkiwi_device,
        )
        return CometKiwiEstimator(
            config,
            enabled=True,
            license_accepted=qa_settings.cometkiwi_license_accepted,
        )
    except Exception:  # noqa: BLE001 - an optional extra never stops a session
        return None


def detach_chapter_qa_coordinator(app) -> None:
    """Stop and forget the coordinator attached to one application, if any."""
    coordinator = getattr(app, "qa_coordinator", None)
    if coordinator is None:
        return
    try:
        coordinator.shutdown()
    except Exception:  # noqa: BLE001 - shutdown must never raise into a session
        pass
    app.qa_coordinator = None


def _pending_qa_tasks(task_manager):
    """List the queued tasks whose quality check never finished, with chapters."""
    if task_manager is None or not hasattr(task_manager, "get_qa_pending_tasks"):
        return ()
    return tuple(
        (task_id, chapter_ids_from_payload(payload))
        for task_id, payload in task_manager.get_qa_pending_tasks()
    )


def _current_options(settings_manager):
    """Read the user's switches again so a mid-session change applies next."""
    try:
        qa_settings = settings_manager.get_qa_settings()
    except Exception:  # noqa: BLE001 - unreadable settings fall back to defaults
        qa_settings = QaSettings()
    options = qa_settings.to_options()
    if qa_settings.language_chunk_chars:
        return options
    # An automatic size is the project's own translation limit: whatever amount
    # of text this book is translated in, it is also checked in.  That number
    # lives in the translation settings, which only this side can read.
    try:
        saved = settings_manager.load_settings()
    except Exception:  # noqa: BLE001 - a size nobody can read is not fatal
        saved = None
    return replace(options, language_chunk_chars=language_chunk_chars_for(saved))


def _report(log, message: str) -> None:
    if callable(log):
        try:
            log(message)
        except Exception:  # noqa: BLE001 - logging must never raise
            return


def build_manual_events(
    *,
    project_manager,
    epub_path: str,
    task_id: str = "manual",
    chapter_ids=None,
    target_language: str = "ru",
):
    """List the chapters a manual pass can check, in book order.

    A chapter qualifies only when the project map records a translation and that
    file is actually on disk; everything else is silently left out, because a
    manual pass must not invent work it cannot do.
    """

    from ..core.chapter_qa_coordinator import TranslationReadyEvent

    wanted = set(chapter_ids) if chapter_ids else None
    project_folder = Path(getattr(project_manager, "project_folder", "") or "")
    translations = getattr(project_manager, "data", {}) or {}
    events = []
    for original_path in sorted(translations):
        if wanted is not None and original_path not in wanted:
            continue
        versions = translations.get(original_path) or {}
        if not isinstance(versions, Mapping):
            continue
        for _suffix, relative_path in sorted(versions.items()):
            translated = project_folder / str(relative_path)
            if not translated.is_file():
                continue
            events.append(
                TranslationReadyEvent(
                    task_id=task_id,
                    chapter_id=str(original_path),
                    source_path=str(original_path),
                    translated_path=str(translated),
                    source_language="auto",
                    target_language=target_language,
                    epub_path=str(epub_path or ""),
                )
            )
            break
    return tuple(events)


def chapter_ids_from_payload(payload) -> tuple[str, ...]:
    """Recover the chapters of one queued task without persisting them twice."""
    if not isinstance(payload, (list, tuple)) or len(payload) < 3:
        return ()
    chapters = payload[2]
    if isinstance(chapters, str):
        return (chapters,) if chapters.strip() else ()
    if isinstance(chapters, (list, tuple)):
        return tuple(
            str(item).strip()
            for item in chapters
            if isinstance(item, str) and item.strip()
        )
    return ()


def detect_source_language(html: str) -> str:
    """Name the source language of one chapter well enough for QA profiles."""
    from ..utils.language_tools import LanguageDetector

    sample = str(html or "")[:20000]
    if LanguageDetector.contains_japanese(sample):
        return "ja"
    if LanguageDetector.contains_korean(sample):
        return "ko"
    if LanguageDetector.contains_chinese(sample):
        return "zh"
    return "en"


# Which embedding backend a translation provider's keys can actually be used
# with.  Everything absent from here has no embedding endpoint of its own.
EMBEDDING_KEY_NAMESPACES = {
    "gemini": "google",
    "google": "google",
    "openai": "openai",
    "openai_compatible": "openai",
    "openrouter": "openai",
    "nvidia": "openai",
    "deepseek": "openai",
    "omniroute": "openai",
    "local": "openai",
}


def embedding_key_namespace(provider: str) -> str:
    """Name the embedding backend a provider's keys belong to, or an empty string."""
    return EMBEDDING_KEY_NAMESPACES.get(str(provider or "").strip().lower(), "")


def embedding_keys_for_session(provider: str, api_key) -> dict[str, object]:
    """Map the session's own keys onto the embedding providers that accept them.

    All of the session's keys, not just the first: one key that hits its limit
    should hand the batch to the next one exactly as translation does.
    """
    namespace = embedding_key_namespace(provider)
    keys = _keys(api_key)
    if namespace == "google" and keys:
        return {"google": keys}
    return {}


def embedding_model_for(qa_settings: QaSettings, namespace: str = "") -> str:
    """The model whose limits an embedding key is judged against."""
    if qa_settings.embedding_model:
        return qa_settings.embedding_model
    if namespace == "openai":
        return DEFAULT_EMBEDDING_MODELS["openai_compatible"]
    return DEFAULT_EMBEDDING_MODELS["gemini"]


def green_embedding_keys(
    settings_manager, provider_id: str, model_id: str
) -> tuple[str, ...]:
    """Every key of one provider that is not rate limited for this model.

    Key limits are already tracked per model, so a key that ran out of
    translation quota is still green for embeddings and the other way round.
    """
    if settings_manager is None or not provider_id:
        return ()
    try:
        statuses = settings_manager.load_key_statuses() or ()
    except Exception:  # noqa: BLE001 - unreadable statuses mean no pool, not a crash
        return ()
    pool: list[str] = []
    for key_info in statuses:
        if str(key_info.get("provider") or "") != str(provider_id):
            continue
        key = str(key_info.get("key") or "").strip()
        if not key:
            continue
        try:
            blocked = settings_manager.is_key_limit_active(key_info, model_id)
        except Exception:  # noqa: BLE001 - an unreadable status is not a red key
            blocked = False
        if not blocked:
            pool.append(key)
    return tuple(dict.fromkeys(pool))


class SettingsEmbeddingKeyHealth:
    """Track embedding quota per key against the embedding model, not the translation one.

    The application already stores key limits per model, so writing the
    embedding model's id here is exactly what makes a key go red for semantic
    checking while it keeps translating chapters.
    """

    def __init__(self, settings_manager, model_id: str, log=None) -> None:
        self._settings_manager = settings_manager
        self._model_id = str(model_id or "").strip()
        self._log = log

    def is_active(self, api_key: str) -> bool:
        manager = self._settings_manager
        if manager is None or not self._model_id or not api_key:
            return True
        try:
            key_info = manager.get_key_info(api_key)
            if not key_info:
                return True
            return not manager.is_key_limit_active(key_info, self._model_id)
        except Exception:  # noqa: BLE001 - unreadable status is not a red key
            return True

    def mark_exhausted(self, api_key: str, reason: str = "") -> None:
        manager = self._settings_manager
        if manager is None or not self._model_id or not api_key:
            return
        try:
            manager.mark_key_as_exhausted(api_key, self._model_id)
        except Exception:  # noqa: BLE001 - bookkeeping never fails a check
            return
        _report(
            self._log,
            f"[QA] Ключ …{api_key[-4:]} исчерпан для эмбеддингов "
            f"({self._model_id}{': ' + reason if reason else ''}); "
            "перевод этот лимит не затрагивает.",
        )


def embedding_key_pool(
    settings_manager,
    qa_settings: QaSettings,
    session_keys: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Decide which keys embeddings may use, and say nothing when none may.

    An explicitly chosen key wins.  Otherwise a chosen provider contributes all
    of its keys that are still healthy *for the embedding model* — which is what
    makes this survive a session whose fallback moved translation to a provider
    with no embeddings at all.  The session's own key is only the last resort.
    """
    if qa_settings.embedding_api_key:
        return dict(session_keys or {})
    provider_id = str(qa_settings.embedding_key_provider or "").strip()
    if not provider_id:
        return dict(session_keys or {})
    namespace = embedding_key_namespace(provider_id)
    if not namespace:
        return {}
    pool = green_embedding_keys(
        settings_manager, provider_id, embedding_model_for(qa_settings, namespace)
    )
    if not pool:
        return {}
    merged: dict[str, object] = dict(session_keys or {})
    merged[namespace] = pool
    return merged


def proxy_url_from_settings(proxy_settings) -> str:
    """Build the proxy URL the translation workers would use, or nothing.

    The empty string is the honest default: settings that are absent, disabled,
    or incomplete mean a direct connection, exactly as the workers treat them.
    """
    if not isinstance(proxy_settings, Mapping) or not proxy_settings.get("enabled"):
        return ""
    host = str(proxy_settings.get("host") or "").strip()
    port = str(proxy_settings.get("port") or "").strip()
    if not host or not port:
        return ""
    kind = str(proxy_settings.get("type") or "SOCKS5").strip().lower()
    user = str(proxy_settings.get("user") or "").strip()
    password = str(proxy_settings.get("pass") or "").strip()
    auth = f"{user}:{password}@" if user and password else ""
    return f"{kind}://{auth}{host}:{port}"


def aiohttp_session_factory(proxy_settings=None):
    """Return a callable producing one HTTP session per outbound QA request.

    The session trusts the same certificate bundle as the translation handlers,
    and — this is the part that broke in the field — the same proxy: a session
    translating through the application's SOCKS tunnel used to run its QA
    embeddings directly, so a geo-blocked network silently degraded every
    chapter to limited mode while the translation itself kept working.
    """
    proxy_url = proxy_url_from_settings(proxy_settings)

    def factory():
        import aiohttp

        from ..api.base import create_ssl_context

        connector = None
        if proxy_url:
            try:
                from aiohttp_socks import ProxyConnector

                connector = ProxyConnector.from_url(
                    proxy_url, rdns=True, ssl=create_ssl_context()
                )
            except Exception:  # noqa: BLE001 - a broken proxy stack falls back to direct
                connector = None
        if connector is None:
            connector = aiohttp.TCPConnector(ssl=create_ssl_context())
        return aiohttp.ClientSession(trust_env=True, connector=connector)

    return factory


def _read_source_chapter(event) -> str | None:
    epub_path = str(getattr(event, "epub_path", "") or "")
    chapter_path = str(event.source_path or "")
    if epub_path and chapter_path:
        try:
            with zipfile.ZipFile(epub_path, "r") as archive:
                return archive.read(chapter_path).decode("utf-8", "ignore")
        except (OSError, KeyError, zipfile.BadZipFile):
            return None
    try:
        return Path(chapter_path).read_text(encoding="utf-8")
    except OSError:
        return None


def _source_text_by_block(source_payload: Mapping[str, object]) -> dict[str, str]:
    from .semantic_units import flatten_visible_text

    blocks = source_payload.get("blocks")
    if not isinstance(blocks, list):
        return {}
    return {
        str(block["id"]): flatten_visible_text(block["inlines"])[0]
        for block in blocks
        if isinstance(block, Mapping) and block.get("id")
    }


def _extractor(capabilities: QaCapabilitySettings):
    from .semantic_units import SemanticUnitExtractor

    return SemanticUnitExtractor(capabilities)


def _load_journal(path: Path, project_manager) -> QaJournal:
    book_id = str(getattr(project_manager, "project_folder", "book") or "book")
    if not path.exists():
        return QaJournal.empty(book_id=book_id)
    try:
        return QaJournal.load(path)
    except QaJournalError as error:
        # A corrupted journal is never silently replaced: QA runs in memory and
        # the user keeps the file to inspect.
        raise QaAssemblyError(f"QA journal is unusable: {error}") from error
