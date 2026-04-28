# -*- coding: utf-8 -*-

import os
import zipfile
from collections import Counter

from .base_processor import BaseTaskProcessor
from .epub_single_file_processor import EpubSingleFileProcessor
from gemini_translator.api.errors import PartialGenerationError, SuccessSignal, ValidationFailedError
from gemini_translator.utils.epub_json import (
    build_html_document_model,
    build_translation_payload,
    estimate_translation_noise,
)
from gemini_translator.utils.translated_paths import build_translated_output_path
from gemini_translator.utils.text import clean_html_content, prettify_html


class EpubBatchProcessor(BaseTaskProcessor):
    SAVE_CHAPTERS_KEY = "save_chapters"

    def _get_filter_repack_save_chapter_set(self, task_payload, chapter_list):
        if not task_payload or len(task_payload) <= 3 or not isinstance(task_payload[3], dict):
            return None

        raw_save_chapters = task_payload[3].get(self.SAVE_CHAPTERS_KEY)
        if raw_save_chapters is None:
            return None

        chapter_set = {str(chapter) for chapter in chapter_list}
        return {
            str(chapter)
            for chapter in raw_save_chapters
            if chapter and str(chapter) in chapter_set
        }

    def _filter_report_by_save_targets(self, report, save_chapter_set):
        if save_chapter_set is None:
            return report

        return {
            "successful": [
                item
                for item in report.get("successful", [])
                if str(item.get("original_path")) in save_chapter_set
            ],
            "failed": [
                item
                for item in report.get("failed", [])
                if item and str(item[0]) in save_chapter_set
            ],
        }

    async def _execute_json_batch_pipeline(self, task_info, epub_path, chapter_list, batch_log_prefix, use_stream):
        source_documents = []
        documents_payload = []
        raw_source_text = ""

        with zipfile.ZipFile(epub_path, "r") as epub_zip:
            for chapter_path in chapter_list:
                chapter_content = epub_zip.read(chapter_path).decode("utf-8", "ignore")
                document_model = build_html_document_model(chapter_content, document_id=chapter_path)
                payload = build_translation_payload(document_model)
                documents_payload.append(payload)
                source_documents.append({
                    "chapter_path": chapter_path,
                    "document_model": document_model,
                    "payload": payload,
                    "raw_content": chapter_content,
                })
                raw_source_text += chapter_content + "\n"

        user_prompt, _, _, _ = self.worker.prompt_builder.prepare_json_batch_for_api(
            documents_payload=documents_payload,
            raw_source_text=raw_source_text,
            system_instruction_text=self.worker.system_instruction,
            current_chapters_list=list(chapter_list),
        )
        operation_context = self._build_operation_context(
            task_info,
            action="translate_batch_json",
            chapters=chapter_list,
            task_type="epub_batch",
        )
        raw_response = await self._execute_api_call(
            prompt=user_prompt,
            log_prefix=f"{batch_log_prefix} [JSON]",
            task_info=task_info,
            operation_context=operation_context,
            use_stream=use_stream,
            allow_incomplete=False,
        )
        report = self.worker.response_parser.parse_json_batch_response(raw_response, source_documents)

        total_html_noise = sum(
            estimate_translation_noise(source_info["raw_content"], payload)["html_markup_chars"]
            for source_info, payload in zip(source_documents, documents_payload)
        )
        total_json_noise = sum(
            estimate_translation_noise(source_info["raw_content"], payload)["json_overhead_chars"]
            for source_info, payload in zip(source_documents, documents_payload)
        )
        self.worker._post_event("log_message", {
            "message": (
                f"[JSON EPUB BATCH] Chapters: {len(chapter_list)}; "
                f"HTML noise={total_html_noise}, JSON overhead={total_json_noise}."
            )
        })
        return raw_response, report

    def _save_successful_chapters(self, successful_chapters_data, file_suffix, log_prefix, save_chapter_set=None):
        successful_paths = []
        save_failed_paths = []
        registrations_to_make = []

        for success_data in successful_chapters_data:
            original_path = success_data.get("original_path")
            if save_chapter_set is not None and str(original_path) not in save_chapter_set:
                continue
            try:
                final_html = success_data["final_html"]
                out_path = build_translated_output_path(
                    self.worker.output_folder,
                    original_path,
                    file_suffix,
                )
                os.makedirs(os.path.dirname(out_path), exist_ok=True)

                if getattr(self.worker, "use_prettify", False):
                    final_html = prettify_html(final_html)

                with open(out_path, "w", encoding="utf-8") as output_file:
                    output_file.write(final_html)

                relative_path = os.path.relpath(out_path, self.worker.output_folder)
                registrations_to_make.append((original_path, file_suffix, relative_path))
                successful_paths.append(original_path)
            except Exception as exc:
                self.worker._post_event("log_message", {
                    "message": f"[{log_prefix}] Save error for '{original_path}': {exc}"
                })
                if original_path and original_path not in save_failed_paths:
                    save_failed_paths.append(original_path)

        if self.worker.project_manager and registrations_to_make:
            try:
                self.worker.project_manager.register_multiple_translations(registrations_to_make)
            except Exception as exc:
                self.worker._post_event("log_message", {
                    "message": f"[{log_prefix}] Batch registration error: {exc}"
                })

        return successful_paths, save_failed_paths

    def _replace_batch_results(self, task_id, epub_path, successful_paths, failed_paths, raw_response):
        self.worker.task_manager.replace_batch_with_results(
            original_batch_task_id=str(task_id),
            epub_path=epub_path,
            successful_chapters=successful_paths,
            failed_chapters=failed_paths,
            success_details_map=None,
        )

    async def execute(self, task_info, use_stream=False):
        task_id, task_payload = task_info

        try:
            epub_path = task_payload[1]
            chapter_list = task_payload[2]
        except IndexError:
            raise ValueError(f"Invalid epub_batch task payload: {task_payload}")

        if isinstance(chapter_list, bytes):
            chapter_list = (os.fsdecode(chapter_list),)
        elif isinstance(chapter_list, (str, os.PathLike)):
            chapter_list = (os.fspath(chapter_list),)
        elif isinstance(chapter_list, list):
            chapter_list = tuple(chapter_list)
        elif not isinstance(chapter_list, tuple):
            try:
                chapter_list = tuple(chapter_list)
            except TypeError:
                chapter_list = (chapter_list,)

        if not chapter_list:
            raise ValueError(f"Empty epub_batch task: {task_payload}")

        save_chapter_set = self._get_filter_repack_save_chapter_set(task_payload, chapter_list)
        if save_chapter_set is not None and not save_chapter_set:
            raise ValueError("Filter repack batch has no save targets inside its chapter list.")

        if len(chapter_list) == 1:
            single_payload = ("epub", epub_path, chapter_list[0])
            try:
                self.worker.task_manager.update_task(
                    task_id,
                    new_payload=single_payload,
                    current_worker_id=self.worker.worker_id,
                )
            except Exception as exc:
                self.worker._post_event("log_message", {
                    "message": f"[BATCH FALLBACK WARN] Could not convert 1-item batch: {exc}"
                })

            self.worker._post_event("log_message", {
                "message": f"[BATCH FALLBACK] Converted single-item batch to epub task '{os.path.basename(str(chapter_list[0]))}'."
            })
            single_processor = EpubSingleFileProcessor(self.worker)
            return await single_processor.execute((task_id, single_payload), use_stream=use_stream)

        batch_log_prefix = f"Batch of {len(chapter_list)} chapters"
        if self._should_use_json_epub_pipeline():
            try:
                raw_response, report = await self._execute_json_batch_pipeline(
                    task_info=task_info,
                    epub_path=epub_path,
                    chapter_list=chapter_list,
                    batch_log_prefix=batch_log_prefix,
                    use_stream=use_stream,
                )
            except (ValidationFailedError, PartialGenerationError, ValueError) as json_error:
                self.worker._post_event("log_message", {
                    "message": f"[JSON EPUB BATCH] Fallback to legacy HTML batch: {json_error}"
                })
            else:
                report = self._filter_report_by_save_targets(report, save_chapter_set)
                successful_chapters_data = report.get("successful", [])
                failed_chapters_details = report.get("failed", [])
                failed_chapters_paths = [item[0] for item in failed_chapters_details]

                successful_paths, save_failed_paths = self._save_successful_chapters(
                    successful_chapters_data,
                    self.worker.provider_config["file_suffix"],
                    "JSON EPUB BATCH",
                    save_chapter_set=save_chapter_set,
                )
                for path in save_failed_paths:
                    if path not in failed_chapters_paths:
                        failed_chapters_paths.append(path)

                self._replace_batch_results(
                    task_id=task_id,
                    epub_path=epub_path,
                    successful_paths=successful_paths,
                    failed_paths=failed_chapters_paths,
                    raw_response=raw_response,
                )

                total_count = len(save_chapter_set) if save_chapter_set is not None else len(chapter_list)
                failed_count = len(failed_chapters_paths)
                if failed_count == 0:
                    self.worker._post_event("log_message", {
                        "message": f"[JSON EPUB BATCH] Completed: {total_count}/{total_count}."
                    })
                    raise SuccessSignal("Batch completed.")

                detailed_errors = [
                    f"[{os.path.basename(path)}]: {reason}"
                    for path, reason in failed_chapters_details
                ]
                exception_msg = (
                    f"Batch failed for {failed_count}/{total_count} chapters. "
                    f"Details: {'; '.join(detailed_errors)}"
                )
                self._raise_validation_error(exception_msg, raw_response)

        user_prompt, _, _, original_contents = self.worker.prompt_builder.prepare_batch_for_api(
            epub_path,
            chapter_list,
            self.worker.system_instruction,
        )
        raw_response = ""
        finish_reason_exc = None

        try:
            operation_context = self._build_operation_context(
                task_info,
                action="translate_batch",
                chapters=chapter_list,
                task_type="epub_batch",
            )
            raw_response = await self._execute_api_call(
                prompt=user_prompt,
                log_prefix=batch_log_prefix,
                task_info=task_info,
                operation_context=operation_context,
                use_stream=use_stream,
                allow_incomplete=use_stream,
            )
        except PartialGenerationError as exc:
            raw_response = exc.partial_text
            finish_reason_exc = exc
            self.worker._post_event("log_message", {
                "message": f"[BATCH WARN] Partial response ({exc.reason}). Trying to recover completed chapters."
            })

        cleaned_response = clean_html_content(raw_response, is_html=False)
        report = self.worker.response_parser.unpack_and_validate_batch(
            cleaned_response,
            chapter_list,
            original_contents,
        )

        report = self._filter_report_by_save_targets(report, save_chapter_set)
        successful_chapters_data = report.get("successful", [])
        failed_chapters_details = report.get("failed", [])
        failed_chapters_paths = [item[0] for item in failed_chapters_details]

        successful_paths, save_failed_paths = self._save_successful_chapters(
            successful_chapters_data,
            self.worker.provider_config["file_suffix"],
            "BATCH",
            save_chapter_set=save_chapter_set,
        )
        for path in save_failed_paths:
            if path not in failed_chapters_paths:
                failed_chapters_paths.append(path)

        self._replace_batch_results(
            task_id=task_id,
            epub_path=epub_path,
            successful_paths=successful_paths,
            failed_paths=failed_chapters_paths,
            raw_response=raw_response,
        )

        total_count = len(save_chapter_set) if save_chapter_set is not None else len(chapter_list)
        failed_count = len(failed_chapters_paths)
        success_count = len(successful_paths)

        if failed_count == 0:
            self.worker._post_event("log_message", {
                "message": f"[BATCH DONE] Completed: {success_count}/{total_count}."
            })
            raise SuccessSignal("Batch completed.")

        reasons = [item[1] for item in failed_chapters_details]
        reason_counts = Counter(reasons)
        short_summary = ", ".join(
            f"{count}x {reason.split(':')[0]}"
            for reason, count in reason_counts.items()
        )

        log_header = (
            f"[BATCH PARTIAL] {success_count} ok, {failed_count} fail."
            if success_count > 0
            else f"[BATCH FAILED] {failed_count} fail."
        )
        batch_details_lines = [
            f"Batch: {total_count} chapters",
            f"Successful: {success_count}",
            f"Failed: {failed_count}",
            f"Summary: {short_summary}",
            "",
            "Chapter errors:",
            *[f"- {os.path.basename(path)}: {reason}" for path, reason in failed_chapters_details],
        ]
        raw_details_source = raw_response or cleaned_response
        if isinstance(raw_details_source, str) and raw_details_source.strip():
            batch_details_lines.extend(["", "Raw batch response:", raw_details_source])

        self.worker._post_event("log_message", {
            "message": f"{log_header} Summary: {short_summary}",
            "details_title": f"Batch failure details ({failed_count}/{total_count})",
            "details_text": "\n".join(batch_details_lines).strip(),
        })

        detailed_errors = [
            f"[{os.path.basename(path)}]: {reason}"
            for path, reason in failed_chapters_details
        ]
        full_error_details = "; ".join(detailed_errors)

        if finish_reason_exc:
            original_message = str(finish_reason_exc)
            enriched_message = f"{original_message}. Chapter failures: {full_error_details}"
            if isinstance(finish_reason_exc, PartialGenerationError):
                self._raise_validation_error(enriched_message, raw_response or cleaned_response)
            raise type(finish_reason_exc)(enriched_message)

        exception_msg = f"Batch failed for {failed_count}/{total_count} chapters. Details: {full_error_details}"
        self._raise_validation_error(exception_msg, raw_response or cleaned_response)
