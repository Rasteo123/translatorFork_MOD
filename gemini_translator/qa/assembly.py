"""Build a working QA service and coordinator from real project state."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
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
from .language_validation import LanguageQualityPipeline
from .llm.completion import ExistingHandlerCompletionClient, QaModelSelection
from .llm.omission_repairer import OmissionRepairer
from .llm.omission_verifier import OmissionVerifier
from .models import AlignmentConfig, GlossaryRule
from .repair_store import RepairStore
from .repair_validator import RepairValidator
from .service import ChapterQaRequest, TranslationQualityService
from .settings import QaSettings
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

    @classmethod
    def for_project(cls, project_manager) -> "ProjectQaPaths":
        return cls(
            journal=Path(project_manager.get_translation_qa_journal_path()),
            backups=Path(project_manager.get_translation_qa_backup_dir()),
            embedding_cache=Path(
                project_manager.get_translation_qa_embedding_cache_dir()
            ),
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
):
    """Create the configured embedding provider, or raise if none is usable.

    An explicitly chosen key and address in the settings always win; the running
    session's own key is only a fallback, so a book can be checked with one
    provider while being translated by another.

    A cache directory is only used together with the segmentation identity the
    units were produced under: a cached vector must never survive a change of
    segmentation.
    """
    chosen_key = qa_settings.embedding_api_key
    gemini_key = chosen_key or str(
        api_keys_by_provider.get("google") or api_keys_by_provider.get("gemini") or ""
    )
    openai_key = chosen_key or str(api_keys_by_provider.get("openai") or "")
    openai_base = qa_settings.embedding_base_url or str(
        api_keys_by_provider.get("openai_base_url") or ""
    )
    if qa_settings.embedding_provider == "gemini":
        openai_key = ""
    elif qa_settings.embedding_provider == "openai_compatible":
        gemini_key = ""
    candidates: list[EmbeddingProviderConfig] = []
    if qa_settings.embedding_provider in {"auto", "gemini"} and gemini_key:
        candidates.append(
            EmbeddingProviderConfig(
                kind="gemini",
                api_key=gemini_key,
                model=qa_settings.embedding_model or DEFAULT_EMBEDDING_MODELS["gemini"],
            )
        )
    if qa_settings.embedding_provider in {"auto", "openai_compatible"} and openai_key and openai_base:
        candidates.append(
            EmbeddingProviderConfig(
                kind="openai_compatible",
                api_key=openai_key,
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
    provider = create_embedding_provider(config, session_factory)
    if cache_dir is None:
        return provider
    identity = preprocessing_identity or _extractor(
        qa_settings.effective_capabilities()
    ).preprocessing_identity
    return CachedEmbeddingProvider(
        provider, EmbeddingCache(cache_dir), preprocessing_identity=identity
    )


def build_translation_quality_service(
    *,
    project_manager,
    qa_settings: QaSettings,
    handler_factory: Callable[[QaModelSelection], object],
    embedding_provider,
    session_id: str,
    target_language: str = "ru",
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
        coverage=coverage,
        verifier=OmissionVerifier(client),
        repairer=OmissionRepairer(client),
        repair_engine=StructuralRepairEngine(target_language, capabilities),
        repair_validator=RepairValidator(client),
        store=RepairStore(paths.backups, session_id=session_id),
        journal=journal,
        journal_path=paths.journal,
        additions=AdditionDetector(client),
        language=LanguageQualityPipeline(client),
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
    try:
        embedding_provider = build_embedding_provider(
            qa_settings,
            session_factory,
            api_keys_by_provider,
            paths.embedding_cache,
            _extractor(qa_settings.effective_capabilities()).preprocessing_identity,
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
        log=log,
    )
    app.qa_coordinator = coordinator
    return coordinator


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
        return settings_manager.get_qa_settings().to_options()
    except Exception:  # noqa: BLE001 - unreadable settings fall back to defaults
        return QaSettings().to_options()


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


def embedding_keys_for_session(provider: str, api_key: str) -> dict[str, str]:
    """Map the session's own key onto the embedding providers that accept it."""
    normalized = str(provider or "").strip().lower()
    if normalized in {"gemini", "google"} and api_key:
        return {"google": api_key}
    return {}


def aiohttp_session_factory():
    """Return a callable producing one HTTP session per embedding request."""

    def factory():
        import aiohttp

        return aiohttp.ClientSession(trust_env=True)

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
