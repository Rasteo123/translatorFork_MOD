from gemini_translator.api.errors import (
    ContentFilterError,
    PartialGenerationError,
    ValidationFailedError,
)
from gemini_translator.core.worker_helpers.content_filter_fallback import (
    fallback_enabled,
    is_content_block_exception,
    run_content_filter_fallback,
)
from gemini_translator.core.worker_helpers.provider_orchestrator import (
    execute_orchestrated_api_call,
    should_orchestrate_api_call,
)


def _notify_translation_ready(worker, task_info, saved_records):
    """Report saved chapters to quality control when the worker supports it.

    Translation must never depend on QA being present or healthy, so a worker
    without the hook, an empty batch, or a failing coordinator all mean the same
    thing here: nothing happens.
    """

    notify = getattr(worker, 'notify_translation_ready', None)
    if not callable(notify) or not task_info or not saved_records:
        return
    try:
        notify(task_info, saved_records)
    except Exception:
        return


class BaseTaskProcessor:
    """
    Базовый класс для всех обработчиков задач.
    Определяет интерфейс и предоставляет доступ к воркеру.
    """
    def __init__(self, worker):
        self.worker = worker
        self.project_manager = getattr(worker, 'project_manager', None)
        self.task_manager = getattr(worker, 'task_manager', None)

    def _build_operation_context(
        self,
        task_info,
        *,
        action: str | None = None,
        chapter: str | None = None,
        chapters=None,
        task_type: str | None = None,
        chunk_index: int | None = None,
        chunk_total: int | None = None,
        is_retry: bool = False,
        project_path: str | None = None,
    ) -> dict:
        task_id, task_payload = task_info
        resolved_task_type = task_type or (task_payload[0] if task_payload else None)
        resolved_project_path = project_path
        if not resolved_project_path:
            if self.project_manager and getattr(self.project_manager, 'project_folder', None):
                resolved_project_path = self.project_manager.project_folder
            else:
                resolved_project_path = getattr(self.worker, 'output_folder', None) or getattr(self.worker, 'file_path', None)

        if chapters is None:
            chapters = [chapter] if chapter else []
        elif isinstance(chapters, (str, bytes)):
            chapters = [chapters.decode('utf-8', errors='ignore') if isinstance(chapters, bytes) else chapters]
        else:
            chapters = [str(item) for item in chapters if item]

        if chapter is None and chapters:
            chapter = str(chapters[0])

        return {
            'task_id': str(task_id),
            'task_type': resolved_task_type,
            'operation_type': resolved_task_type,
            'action': action or 'api_call',
            'chapter': str(chapter) if chapter else None,
            'chapters': chapters,
            'chunk_index': chunk_index,
            'chunk_total': chunk_total,
            'is_retry': bool(is_retry),
            'project_path': resolved_project_path,
        }

    async def _execute_api_call(self, prompt, log_prefix, *, task_info, operation_context: dict | None = None, **kwargs):
        context_payload = operation_context or self._build_operation_context(task_info)
        try:
            if should_orchestrate_api_call(self.worker, context_payload):
                return await execute_orchestrated_api_call(
                    self.worker,
                    prompt,
                    log_prefix,
                    task_info=task_info,
                    operation_context=context_payload,
                    call_kwargs=kwargs,
                )
            with self.worker.debug_operation_context(context_payload):
                return await self.worker.api_handler_instance.execute_api_call(
                    prompt,
                    log_prefix,
                    **kwargs,
                )
        except (ContentFilterError, PartialGenerationError) as exc:
            if fallback_enabled(self.worker) and is_content_block_exception(exc):
                return await run_content_filter_fallback(
                    self.worker,
                    prompt,
                    log_prefix,
                    task_info=task_info,
                    operation_context=context_payload,
                    call_kwargs=kwargs,
                )
            raise

    def _raise_validation_error(self, message, raw_package_text=""):
        error = ValidationFailedError(message)
        error.raw_package_text = raw_package_text or ""
        raise error

    def _should_use_json_epub_pipeline(self) -> bool:
        """
        JSON EPUB pipeline is opt-in.
        Default processing path remains legacy HTML unless the worker
        explicitly enables the alternative transport.
        """
        return bool(getattr(self.worker, 'use_json_epub_pipeline', False))

    def _prepare_success_details(self, details_text, *, preview_limit=0):
        if not isinstance(details_text, str):
            return None

        normalized_text = details_text.strip()
        if not normalized_text:
            return None

        if bool(getattr(self.worker, 'store_success_details', False)):
            return normalized_text

        if preview_limit <= 0:
            return None

        if len(normalized_text) <= preview_limit:
            return normalized_text

        truncated_chars = len(normalized_text) - preview_limit
        return (
            normalized_text[:preview_limit].rstrip()
            + f"\n\n[details truncated: {truncated_chars} chars omitted]"
        )

    def _build_success_payload(self, *, translated_content=None, details_text=None, details_title=None, preview_limit=0):
        payload = {}
        if translated_content is not None:
            payload['translated_content'] = translated_content

        prepared_details = self._prepare_success_details(details_text, preview_limit=preview_limit)
        if prepared_details:
            payload['success_details'] = prepared_details
            payload['success_details_title'] = details_title or "Полученный пакет"

        return payload

    async def execute(self, task_info, use_stream=True):
        """
        Основной метод, который должен быть переопределен в дочерних классах.
        """
        raise NotImplementedError("Метод execute должен быть реализован в подклассе.")
