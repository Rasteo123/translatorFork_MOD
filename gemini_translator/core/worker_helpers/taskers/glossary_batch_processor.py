# -*- coding: utf-8 -*-

import zipfile
import json
import time
import re

_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u30ff\uac00-\ud7af]')

from bs4 import BeautifulSoup

from .base_processor import BaseTaskProcessor
from gemini_translator.api.errors import ValidationFailedError, PartialGenerationError
from gemini_translator.utils.text import clean_html_content, repair_json_string
from gemini_translator.utils.language_tools import SmartGlossaryFilter
from gemini_translator.utils.term_frequency_tools import (
    calculate_term_frequency_payload,
    get_epub_signature,
    get_term_frequency_map,
)
from gemini_translator.api import config as api_config


def limit_glossary_terms_by_frequency(glossary_items, new_terms_limit, frequency_counts):
    glossary_items = list(glossary_items or [])
    try:
        new_terms_limit = int(new_terms_limit)
    except (TypeError, ValueError):
        new_terms_limit = 0

    if new_terms_limit <= 0 or len(glossary_items) <= new_terms_limit:
        return glossary_items, []

    frequency_counts = frequency_counts or {}

    def _count_for(item):
        try:
            return int(frequency_counts.get(item.get("original"), 0) or 0)
        except (TypeError, ValueError):
            return 0

    ranked_terms = sorted(
        enumerate(glossary_items),
        key=lambda indexed_item: (-_count_for(indexed_item[1]), indexed_item[0]),
    )
    limited_terms = [item for _, item in ranked_terms[:new_terms_limit]]
    discarded_terms = [item for _, item in ranked_terms[new_terms_limit:]]
    return limited_terms, discarded_terms


def filter_glossary_items_for_source_text(
    glossary_items,
    source_text,
    *,
    use_jieba_for_glossary_search=True,
):
    if not glossary_items:
        return [], 0
    if not source_text:
        return [], len(glossary_items)

    text_validator = SmartGlossaryFilter()
    ai_glossary_as_dict = {
        item['original']: {'rus': item['rus'], 'note': item['note']}
        for item in glossary_items
        if item.get('original')
    }
    found_terms_dict = text_validator.filter_glossary_for_text(
        full_glossary=ai_glossary_as_dict,
        text=source_text,
        fuzzy_threshold=99,
        use_jieba_for_glossary_search=use_jieba_for_glossary_search,
        find_embedded_subterms=True
    )
    filtered_items = [
        term_data for term_data in glossary_items
        if term_data.get('original') in found_terms_dict
    ]
    return filtered_items, len(glossary_items) - len(filtered_items)


class GlossaryBatchProcessor(BaseTaskProcessor):
    def _get_book_frequency_counts(self, epub_path, glossary_items):
        original_terms = [
            str(item.get("original") or "").strip()
            for item in glossary_items or []
            if str(item.get("original") or "").strip()
        ]
        if not original_terms:
            return {}

        try:
            signature = json.dumps(
                get_epub_signature(epub_path),
                ensure_ascii=False,
                sort_keys=True,
            )
            cache = getattr(self.worker, "_new_term_frequency_cache", None)
            if not isinstance(cache, dict) or cache.get("signature") != signature:
                cache = {"signature": signature, "counts": {}}
                setattr(self.worker, "_new_term_frequency_cache", cache)

            cached_counts = cache.setdefault("counts", {})
            missing_terms = [term for term in original_terms if term not in cached_counts]
            if missing_terms:
                payload = calculate_term_frequency_payload(
                    epub_path,
                    [{"original": term} for term in missing_terms],
                )
                frequency_map = get_term_frequency_map(payload)
                for term in missing_terms:
                    cached_counts[term] = int(
                        frequency_map.get(term, {}).get("count", 0) or 0
                    )

            return {term: int(cached_counts.get(term, 0) or 0) for term in original_terms}
        except Exception as exc:
            self.worker._post_event(
                'log_message',
                {'message': f"⚠️ [Глоссарий] Частотный отбор по книге недоступен: {exc}. Использую порядок ответа AI."}
            )
            return {}

    async def execute(self, task_info, use_stream=False):
        task_id, task_payload = task_info

        try:
            task_payload[0]
            epub_path_or_object = task_payload[1]
            chapter_paths = task_payload[2]
            task_payload[3:]
        except IndexError:
            raise ValueError(f"Некорректный формат задачи glossary_batch_task: {task_payload}")

        log_prefix = f"Глоссарий для пакета из {len(chapter_paths)} глав"

        try:
            processed_parts = []
            with open(epub_path_or_object, 'rb') as epub_file, \
                    zipfile.ZipFile(epub_file, "r") as zf:
                for chapter_path in chapter_paths:
                    raw_html = zf.read(chapter_path).decode("utf-8", "ignore")
                    soup = BeautifulSoup(raw_html, 'html.parser')
                    body_tag = soup.body
                    if body_tag:
                        processed_parts.append(body_tag.get_text(separator='\n', strip=True))
                    else:
                        processed_parts.append(raw_html)

            full_text_for_api = "\n\n-----\n\n".join(processed_parts) if len(processed_parts) > 1 else (processed_parts[0] if processed_parts else "")

            if not full_text_for_api.strip():
                return task_info, True, 'SUCCESS', "Пакет для задачи пуст"
        except Exception as e:
            raise RuntimeError(f"Не удалось извлечь и обработать данные для задачи глоссария: {e}")

        settings_for_prompt = {
            'glossary_generation_prompt': getattr(self.worker, 'glossary_generation_prompt', api_config.default_glossary_prompt()),
            'send_notes_in_sequence': getattr(self.worker, 'send_notes_in_sequence', True),
            'glossary_merge_mode': getattr(self.worker, 'glossary_merge_mode', 'supplement'),
            'initial_glossary_list': getattr(self.worker, 'initial_glossary_list', []),
            'system_instruction': getattr(self.worker, 'system_instruction', None)
        }

        user_prompt, _, _, log_info, full_context_glossary = self.worker.prompt_builder.prepare_for_glossary_generation(
            full_text_for_api,
            settings=settings_for_prompt,
            task_manager=self.worker.task_manager
        )

        if log_info:
            task_name = self.worker.task_manager._get_task_display_name(task_payload)
            filtered_count = log_info.get('used_for_context', 0)
            total_count = log_info.get('total_in_db', 0)
            log_msg = f"[CONTEXT] Для задачи '{task_name}' в промпт добавлено {filtered_count} релевантных терминов из {total_count}."
            self.worker._post_event('log_message', {'message': log_msg})

        raw_response = ""
        json_text = ""
        try:
            operation_context = self._build_operation_context(
                task_info,
                action='generate_glossary',
                chapters=chapter_paths,
                task_type='glossary_batch_task',
            )
            raw_response = await self._execute_api_call(
                user_prompt,
                log_prefix,
                task_info=task_info,
                operation_context=operation_context,
                allow_incomplete=True,
                use_stream=use_stream
            )
            json_text = clean_html_content(raw_response)
        except PartialGenerationError as e:
            if e.is_empty_token_limit:
                raise
            self.worker._post_event('log_message', {'message': "[WARN] Ответ JSON оборван. Запускаю протокол 'Феникс'..."})
            partial_clean = clean_html_content(e.partial_text)
            repaired_json = repair_json_string(partial_clean)
            if repaired_json:
                self.worker._post_event('log_message', {'message': "[SUCCESS] 'Феникс' восстановил валидную часть JSON!"})
                json_text = repaired_json
            else:
                self._raise_validation_error(
                    f"Не удалось получить JSON из частичного ответа API. Исходная ошибка: {e}",
                    getattr(e, 'partial_text', '') or raw_response
                )

        try:
            parsed_glossary_dict = None
            try:
                parsed_glossary_dict = json.loads(json_text)
            except json.JSONDecodeError as e:
                repaired_json_text = repair_json_string(json_text)
                if repaired_json_text:
                    try:
                        parsed_glossary_dict = json.loads(repaired_json_text)
                    except json.JSONDecodeError:
                        pass
                if parsed_glossary_dict is None:
                    self._raise_validation_error(
                        f"Не удалось восстановить JSON. Ошибка: {e}",
                        raw_response or json_text
                    )

            if not isinstance(parsed_glossary_dict, dict):
                self._raise_validation_error(
                    "Ответ от AI не является словарем JSON.",
                    raw_response or json_text
                )

            pre_validated_glossary_list = []
            force_accept = getattr(self.worker, "force_accept", False)

            for original, value in parsed_glossary_dict.items():
                if not isinstance(original, str) or not isinstance(value, dict):
                    continue
                raw_rus = value.get("rus") or value.get("translation") or ""
                if not raw_rus:
                    continue
                rus = str(raw_rus).replace('—', '–')
                note = str(value.get("note", "")).replace('—', '–')
                if not force_accept and not re.search(r'[а-яА-ЯёЁ]', rus) and not re.search(r'[а-яА-ЯёЁ]', note):
                    continue
                if not force_accept and _CJK_RE.search(rus):
                    continue
                pre_validated_glossary_list.append({"original": original, "rus": rus, "note": note})

            if not pre_validated_glossary_list:
                return task_info, True, 'SUCCESS', "AI не вернул валидных терминов."

            truly_validated_glossary_list = []
            if not force_accept:
                truly_validated_glossary_list, discarded_count = filter_glossary_items_for_source_text(
                    pre_validated_glossary_list,
                    full_text_for_api,
                    use_jieba_for_glossary_search=self.worker.context_manager.use_jieba_for_glossary,
                )
                if discarded_count > 0:
                    self.worker._post_event('log_message', {'message': f"🔎 [Глоссарий] Отфильтровано {discarded_count} терминов (нет в тексте)."})
            else:
                truly_validated_glossary_list = pre_validated_glossary_list

            new_terms_limit = getattr(self.worker, 'new_terms_limit', 0)
            merge_mode = settings_for_prompt.get('glossary_merge_mode', 'supplement')
            num_updated_override = None
            if truly_validated_glossary_list and new_terms_limit and new_terms_limit > 0:
                existing_originals_set = {term.get('original') for term in full_context_glossary if term.get('original')}
                updated_terms, new_terms = [], []
                for term in truly_validated_glossary_list:
                    if term.get('original') in existing_originals_set:
                        updated_terms.append(term)
                    else:
                        new_terms.append(term)
                frequency_counts = {}
                if len(new_terms) > new_terms_limit:
                    frequency_counts = self._get_book_frequency_counts(epub_path_or_object, new_terms)
                limited_new_terms, discarded_new_terms = limit_glossary_terms_by_frequency(
                    new_terms,
                    new_terms_limit,
                    frequency_counts,
                )
                if discarded_new_terms:
                    self.worker._post_event('log_message', {'message': f"📖 [Глоссарий] Лимит в {new_terms_limit} новых терминов. Отброшено наименее частотных по книге: {len(discarded_new_terms)}."})
                if merge_mode == 'supplement':
                    num_updated_override = len(updated_terms)
                    updated_terms = []
                truly_validated_glossary_list = updated_terms + limited_new_terms

            if truly_validated_glossary_list:
                stats = self.worker.task_manager.save_glossary_batch(
                    task_id=str(task_id),
                    timestamp=time.time(),
                    chapters_json=json.dumps(task_payload[2] if len(task_payload) > 2 else []),
                    glossary_list=truly_validated_glossary_list
                )
                wid_short = self.worker.worker_id[-4:]
                final_msg = ""
                if merge_mode == 'update':
                    final_msg = f"✅ …{wid_short}: Обработано {stats['total']}. Обновлено: {stats['updated']}, Новых: {stats['new']}."
                elif merge_mode == 'supplement':
                    updated_count = num_updated_override if num_updated_override is not None else stats['updated']
                    if stats['new'] > 0:
                        final_msg = f"✅ …{wid_short}: Найдено {stats['new']} новых терминов (дубликатов: {updated_count})."
                    else:
                        final_msg = f"✅ …{wid_short}: Новых терминов не найдено (все {updated_count} уже были в базе)."
                else:
                    final_msg = f"✅ …{wid_short}: Записано {stats['total']} терминов (уникальных: {stats['new']})."
                self.worker._post_event('log_message', {'message': final_msg})

            return task_info, True, 'glossary_success', "Задача глоссария успешно завершена."

        except (json.JSONDecodeError, ValueError) as e:
            self._raise_validation_error(
                f"Не удалось распарсить или обработать JSON: {e}",
                raw_response or json_text
            )
