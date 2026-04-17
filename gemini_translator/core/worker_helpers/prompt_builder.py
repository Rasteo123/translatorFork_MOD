import zipfile
import re
import json

from bs4 import BeautifulSoup, NavigableString

# --- Импорты из нашего проекта ---
from gemini_translator.api import config as api_config

from gemini_translator.utils.text import (
    prettify_html_for_ai, 
    safe_format,
    process_body_tag
)
from gemini_translator.utils.epub_json import (
    build_batch_translation_payload,
    build_transport_payload,
    build_translation_payload,
)
from gemini_translator.utils.glossary_tools import GlossaryAggregator
from gemini_translator.utils.helpers import TokenCounter

class PromptBuilder:
    BOUNDARY_MARKER = "<!-- {chapter_id} -->"
    MEDIA_PLACEHOLDER = "<!-- MEDIA_{index} -->"
    JSON_CONTRACT = """
### TRANSPORT OVERRIDE: STRUCTURED JSON
Any earlier instruction about returning raw HTML is overridden by this contract.
- The source fragment is encoded as compact JSON, not as literal HTML markup.
- Translate only string values in `x` and string items inside `a`.
- Preserve every key, tag marker, array length, nesting level and document order exactly.
- Do not change `t`, `h`, `k`, `o`, `m`, `doc`, `v` or any array structure.
- Return ONLY one valid JSON object, without markdown fences and without explanations.
""".strip()
    JSON_BATCH_CONTRACT = """
### TRANSPORT OVERRIDE: STRUCTURED JSON BATCH
Any earlier instruction about raw HTML or chapter boundary markers is overridden by this contract.
- The payload contains `documents`, each document is one compact JSON chapter fragment.
- Preserve document order and every nested array/object structure exactly.
- Translate only `x` and string items inside `a`.
- Return ONLY one valid JSON object with the same top-level `documents` array.
""".strip()

    def __init__(self, custom_prompt, context_manager, use_system_instruction):
        self.custom_prompt = custom_prompt
        self.context_manager = context_manager
        self.use_system_instruction = use_system_instruction
        self.system_instruction = None
        self.media_map = {}

    def _replace_media_with_placeholders(self, html_content, return_maps=False):
        if not html_content:
            return (({}, {}), "") if return_maps else ""

        media_map, link_map = {}, {}
        soup = BeautifulSoup(html_content, 'html.parser')

        def analyze_position(tag):
            """Определяет позицию тега внутри родителя: START, END или MIDDLE."""
            parent = tag.parent
            if not parent: return 'MIDDLE'
            
            # Проверяем соседей, игнорируя пустые строки
            prev_sib = tag.previous_sibling
            while prev_sib and isinstance(prev_sib, NavigableString) and not prev_sib.strip():
                prev_sib = prev_sib.previous_sibling
            
            next_sib = tag.next_sibling
            while next_sib and isinstance(next_sib, NavigableString) and not next_sib.strip():
                next_sib = next_sib.next_sibling

            if not prev_sib and next_sib: return 'START'
            if prev_sib and not next_sib: return 'END'
            if not prev_sib and not next_sib: return 'SOLO' # Единственный ребенок
            return 'MIDDLE'

        # --- 1. МЕДИА (Картинки) ---
        media_tags = soup.find_all(['img', 'svg', 'picture'])
        for i, media_tag in enumerate(media_tags):
            placeholder = self.MEDIA_PLACEHOLDER.format(index=i)
            
            parent_name = media_tag.parent.name if media_tag.parent else 'body'
            rel_pos = analyze_position(media_tag)

            media_map[placeholder] = {
                'tag_str': str(media_tag),
                'parent_tag': parent_name,
                'rel_pos': rel_pos
            }
            
            if not return_maps:
                media_tag.replace_with(BeautifulSoup(placeholder, 'html.parser'))
        
        # --- 2. ССЫЛКИ ---
        link_tags = soup.find_all('a')
        placeholder_counter = 0
        
        for link_tag in link_tags:
            placeholder_id = f"link_{placeholder_counter}"
            
            # Эвристика экономии токенов: пропускаем мелкие ссылки
            original_attrs_len = len(str(link_tag)) - len(link_tag.decode_contents()) - 5
            new_attr_len = len(f'id="{placeholder_id}"')
            if (original_attrs_len - new_attr_len) < 10:
                continue

            placeholder_counter += 1
            
            parent_name = link_tag.parent.name if link_tag.parent else 'body'
            rel_pos = analyze_position(link_tag)

            link_map[placeholder_id] = {
                'tag_str': str(link_tag), 
                'text': link_tag.get_text(),
                'parent_tag': parent_name, # <-- Записываем тип родителя (h1, p...)
                'rel_pos': rel_pos         # <-- Записываем позицию (START, END...)
            }

            if not return_maps:
                link_tag.attrs = {}
                link_tag['id'] = placeholder_id
        
        if return_maps:
            return media_map, link_map
        else:
            self.media_map = media_map
            self.link_map = link_map
            return str(soup)

    def prepare_for_glossary_generation(self, combined_text: str, settings: dict, task_manager) -> tuple:
        """
        Готовит промпт для генерации глоссария.
        Динамически собирает примеры ({examples}) в зависимости от языка текста.
        """
        prompt_template = settings.get('glossary_generation_prompt', api_config.default_glossary_prompt())
        include_notes = settings.get('send_notes_in_sequence', True)
        is_supplement = settings.get('glossary_merge_mode', 'supplement') == 'supplement'

        log_info = {}
        
        # --- 1. БЛОК ПОЛУЧЕНИЯ ДАННЫХ (SQL-Optimized) ---
        full_context_glossary = []
        try:
            merge_mode = settings.get('glossary_merge_mode', 'supplement')
            db_terms = task_manager.fetch_and_clean_glossary(mode=merge_mode)
            initial_glossary = settings.get('initial_glossary_list', [])
            
            if initial_glossary:
                
                aggregator = GlossaryAggregator(initial_glossary, merge_mode)
                full_context_glossary = aggregator.merge(db_terms)
            else:
                full_context_glossary = db_terms
                
            if db_terms:
                log_info['total_in_db'] = len(db_terms)
        except Exception as e:
            print(f"[PROMPT BUILDER ERROR] Не удалось получить контекст из БД: {e}")
            full_context_glossary = settings.get('initial_glossary_list', [])
    
        # --- 2. БЛОК ФОРМАТИРОВАНИЯ {glossary} ---
        formatted_data = None
        glossary_content = "" 

        if full_context_glossary and combined_text:
            formatted_data, filtered_terms_count = self.context_manager.format_glossary_for_ai_generation(
                full_context_glossary=full_context_glossary,
                text_content=combined_text,
                include_notes=include_notes,
                settings=settings
            )
            log_info['used_for_context'] = filtered_terms_count
            
            if formatted_data:
                if include_notes:
                    glossary_content = f"```json\n{formatted_data}\n```"
                else:
                    glossary_content = formatted_data
        
        # --- 3. БЛОК ФОРМАТИРОВАНИЯ {mode} ---
        DEFAULT_HEADER = "" # (сокращено для краткости кода)
        
        # Загружаем сырые данные из конфига
        prompts_config = api_config.internal_prompts()
        
        mode_instruction_raw = []
        if include_notes:
            if is_supplement:
                mode_instruction_raw = prompts_config.get('glossary_context_simple', DEFAULT_HEADER)
            else:
                mode_instruction_raw = prompts_config.get('glossary_context_full_update_mode', DEFAULT_HEADER)
        else:
            mode_instruction_raw = prompts_config.get('glossary_context_simple', DEFAULT_HEADER)

        mode_content = "\n".join(mode_instruction_raw) if isinstance(mode_instruction_raw, list) else str(mode_instruction_raw)
        
        # --- 4. БЛОК ДИНАМИЧЕСКИХ ПРИМЕРОВ {examples} ---
        # Загружаем базу примеров
        examples_db = prompts_config.get('glossary_output_examples', {})
        
        # 1. Всегда берем базу
        selected_examples_list = list(examples_db.get('base', [])) 
        
        # Детекция языка (берем срез текста)
        sample_text = combined_text[:5000]
        
        # Флаг: нашли ли мы какой-то специфический язык?
        found_specific_lang = False
        
        # Китайский (CJK Unified Ideographs)
        if re.search(r'[\u4e00-\u9fff]', sample_text):
            selected_examples_list.extend(examples_db.get('zh', []))
            found_specific_lang = True
            
        # Корейский (Hangul Syllables)
        if re.search(r'[\uac00-\ud7af]', sample_text):
            selected_examples_list.extend(examples_db.get('ko', []))
            found_specific_lang = True
            
        # Японский (Hiragana / Katakana)
        if re.search(r'[\u3040-\u309f\u30a0-\u30ff]', sample_text):
            selected_examples_list.extend(examples_db.get('jp', []))
            found_specific_lang = True

        # Если не нашли ни китайского, ни корейского, ни японского — добавляем en
        if not found_specific_lang:
            selected_examples_list.extend(examples_db.get('en', []))

        # Собираем строку JSON
        examples_content = "```json\n{\n" + ",\n".join(selected_examples_list) + "\n}\n```"

        # --- 5. СБОРКА ФИНАЛЬНОГО ПРОМПТА ---
        prompt = safe_format(
            prompt_template, 
            mode=mode_content,      
            glossary=glossary_content, 
            text=combined_text,
            examples=examples_content # <-- Вставляем динамические примеры
        )

        # --- Системная инструкция ---
        if self.use_system_instruction:
            self.system_instruction = settings.get('system_instruction')
        else:
            self.system_instruction = None

        debug_report = "Промпт для пакетной генерации (Smart Examples v1)."
        return prompt, self.system_instruction, debug_report, log_info, full_context_glossary
    
    
    def prepare_batch_for_api(self, epub_path, chapter_list, system_instruction_text):
        original_contents = {}
        # Собираем весь СЫРОЙ текст пакета в одну строку
        full_raw_text_for_glossary_filter = ""
        with zipfile.ZipFile(epub_path, "r") as epub_zip:
            for chapter_path in chapter_list:
                content = epub_zip.read(chapter_path).decode("utf-8", "ignore")
                original_contents[chapter_path] = content
                full_raw_text_for_glossary_filter += content + "\n"

        # --- Этап 1: Фильтруем глоссарий ОДИН РАЗ на основе всего СЫРОГО текста ---
        glossary_string = self.context_manager.format_glossary_for_prompt(
            text_content=full_raw_text_for_glossary_filter,
            current_chapters_list=chapter_list # <--- ПЕРЕДАЕМ
        )

        # --- Этап 2: В цикле СЕГМЕНТИРУЕМ каждую главу отдельно ---
        combined_content_for_ai = []
        for i, chapter_path in enumerate(chapter_list):
            original_chapter_content = original_contents[chapter_path]

            # Сегментируем ОДНУ главу
            segmented_chapter_content = self.context_manager.prepare_html_for_translation(original_chapter_content)
            
            # Извлекаем <body> из СЕГМЕНТИРОВАННОЙ главы
            content_to_add = process_body_tag(segmented_chapter_content, return_parts=False, body_content_only=False)
            
            content_with_placeholders = self._replace_media_with_placeholders(content_to_add)
            prettified_content = "\n" + prettify_html_for_ai(content_with_placeholders) + "\n"
            
            combined_content_for_ai.append(self.BOUNDARY_MARKER.format(chapter_id=i))
            combined_content_for_ai.append(prettified_content)
        
        combined_content_for_ai.append(self.BOUNDARY_MARKER.format(chapter_id=len(chapter_list)))
        
        # --- Этап 3: Собираем финальный промпт из СЕГМЕНТИРОВАННЫХ частей и ОТФИЛЬТРОВАННОГО глоссария ---
        full_text_for_api = "\n".join(combined_content_for_ai)
        
        # Загружаем шаблон и форматируем его
        # ДЕФОЛТНАЯ СТРОКА НА СЛУЧАЙ ОШИБКИ КОНФИГА
        DEFAULT_BATCH_INSTR = (
            "\n\n### ИНСТРУКЦИЯ ПО ПАКЕТНОЙ ОБРАБОТКЕ\n"
            "Требуется обработать каждый документ ниже и **ПОЛНОСТЬЮ И В ТОЧНОСТИ СОХРАНИТЬ** эти разделительные маркеры с их номерами в итоговом ответе.\n\n"
            "*   Начало первого документа:\n```html\n{full_text_for_api}\n```\n*   Конец последнего документа.\n\n"
        )
        
        template = api_config.internal_prompts().get('batch_instruction', DEFAULT_BATCH_INSTR)

        final_text_for_api = safe_format(template, full_text_for_api=full_text_for_api)

        user_prompt, _, debug_report = self._build_with_placeholders(final_text_for_api, glossary_string, system_instruction_text, batch_mode=True)
        return user_prompt, self.system_instruction, debug_report, original_contents

    def prepare_json_for_api(self, document_model, raw_source_text, system_instruction_text, current_chapters_list=None):
        glossary_string = self.context_manager.format_glossary_for_prompt(
            text_content=raw_source_text,
            current_chapters_list=current_chapters_list
        )
        source_payload = build_translation_payload(document_model)
        transport_payload = build_transport_payload(source_payload)
        payload_text = json.dumps(transport_payload, ensure_ascii=False, indent=2)
        base_text_for_api = f"\n```json\n{payload_text}\n```\n"
        user_prompt, _, debug_report = self._build_with_placeholders(
            base_text_for_api,
            glossary_string,
            system_instruction_text
        )
        debug_report = f"{debug_report}\nTRANSPORT: JSON"
        return f"{user_prompt}\n\n{self.JSON_CONTRACT}", self.system_instruction, debug_report, source_payload

    def prepare_json_batch_for_api(self, documents_payload, raw_source_text, system_instruction_text, current_chapters_list=None):
        glossary_string = self.context_manager.format_glossary_for_prompt(
            text_content=raw_source_text,
            current_chapters_list=current_chapters_list
        )
        payload = build_batch_translation_payload(documents_payload)
        payload_text = json.dumps(payload, ensure_ascii=False, indent=2)
        base_text_for_api = f"\n```json\n{payload_text}\n```\n"
        user_prompt, _, debug_report = self._build_with_placeholders(
            base_text_for_api,
            glossary_string,
            system_instruction_text,
            batch_mode=True
        )
        debug_report = f"{debug_report}\nTRANSPORT: JSON_BATCH"
        return f"{user_prompt}\n\n{self.JSON_BATCH_CONTRACT}", self.system_instruction, debug_report, payload
        
    def prepare_for_api(self, text_content, system_instruction_text, completion_data=None, current_chapters_list=None):
        """
        Готовит промпт. Версия 3.0: Корректно поддерживает режим завершения,
        сохраняя оригинальный промпт.
        """
        # --- НАЧАЛО НОВОЙ ЛОГИКИ: КОНТЕКСТ ДЛЯ ГЛОССАРИЯ ---
        # В режиме завершения глоссарий всегда фильтруется по ПОЛНОМУ ОРИГИНАЛУ,
        # который мы передаем в completion_data.
        # В обычном режиме `text_content` и есть оригинал.
        original_text_for_glossary = completion_data['original_content'] if completion_data else text_content
        glossary_string = self.context_manager.format_glossary_for_prompt(
            text_content=original_text_for_glossary,
            current_chapters_list=current_chapters_list # <--- ПЕРЕДАЕМ
        )
        # --- КОНЕЦ НОВОЙ ЛОГИКИ ---

        # Готовим основной текст для перевода, как и раньше
        segmented_text_for_ai = self.context_manager.prepare_html_for_translation(text_content)
        text_content_with_placeholders = self._replace_media_with_placeholders(segmented_text_for_ai)
        prettified_content = prettify_html_for_ai(text_content_with_placeholders)

        # --- Собираем ОСНОВНУЮ часть промпта ---
        # Эта часть будет одинаковой и для первого, и для второго запуска
        base_text_for_api = f"\n```html\n{prettified_content}\n```\n"
        user_prompt_base, _, _ = self._build_with_placeholders(base_text_for_api, glossary_string, system_instruction_text)

        # --- Если это задача на завершение, ДОБАВЛЯЕМ "ХВОСТ" ---
        if completion_data:
            partial_translation = completion_data.get('partial_translation', '')
            
            DEFAULT_COMPLETION_INSTR = (
                "\n---\n"
                "### ЗАДАЧА: ДОПЕРЕВОД ПРЕРВАННОГО ОТВЕТА ###\n"
                "Верни только недостающую часть перевода.\n"
                "Не повторяй уже переведенный фрагмент и не начинай заново с начала чанка.\n"
                "Продолжай с первого незавершенного безопасного места, сохраняя HTML-структуру, теги и служебные маркеры.\n"
                "Если последний фрагмент оборван внутри предложения или блока, начни с ближайшего естественного продолжения, не дублируя хвост.\n"
                "\n--- УЖЕ ПЕРЕВЕДЕНО:\n"
                "```html\n"
                "{partial_translation}\n"
                "```\n"
                "---\n"
                "Верни только продолжение, без пояснений и без повтора уже готового текста.\n"
            )
            
            # Загружаем шаблон целиком (вместе с заголовком 'ПРЕРВАННЫЙ ОТВЕТ' и плейсхолдером)
            completion_template = api_config.internal_prompts().get('completion_instruction', DEFAULT_COMPLETION_INSTR)
            
            # Форматируем шаблон, вставляя частичный перевод
            completion_block = safe_format(completion_template, partial_translation=partial_translation)

            final_user_prompt = user_prompt_base + completion_block
            
            # Системная инструкция уже встроена в user_prompt_base через _build_with_placeholders
            return final_user_prompt, self.system_instruction, "Промпт для завершения перевода"
        else:
            # Если это обычная задача, возвращаем базовый промпт
            return user_prompt_base, self.system_instruction, "Стандартный промпт"
        
    def _build_with_placeholders(self, text_for_api, glossary_string, system_instruction_text, batch_mode=False):
        """
        Собирает промпт. Версия с прямой поддержкой системных инструкций.
        """
        # --- НОВАЯ УНИВЕРСАЛЬНАЯ ЛОГИКА ---
        # 1. Определяем, какой будет системная инструкция
        self.system_instruction = system_instruction_text if self.use_system_instruction else None

        # 1.1 СБОРКА ДИНАМИЧЕСКИХ ПРИМЕРОВ ФОРМАТИРОВАНИЯ
        # Загружаем базу примеров
        prompts_config = api_config.internal_prompts()
        examples_db = prompts_config.get('translation_output_examples', {})
        
        # Очищаем HTML перед анализом (используем get_text, как указано в требовании)
        # Берем срез, чтобы не анализировать слишком большие объемы
        sample_text = BeautifulSoup(text_for_api, 'html.parser')
        sample_text = sample_text.get_text()[:4000]
        
        # Считаем вес каждого языка (количество символов)
        # Английский теперь тоже участвует в конкуренции
        lang_counts = {
            'zh': len(re.findall(r'[\u4e00-\u9fff]', sample_text)), # Chinese
            'ko': len(re.findall(r'[\uac00-\ud7af]', sample_text)), # Korean
            'jp': len(re.findall(r'[\u3040-\u309f\u30a0-\u30ff]', sample_text)), # Japanese
            'en': len(re.findall(r'[a-zA-Z]', sample_text))        # English
        }
        
        # Находим язык с максимальным вхождением
        most_frequent_lang = max(lang_counts, key=lang_counts.get)
        
        # Если символов языков > 0, используем победителя, иначе по умолчанию 'en'
        target_key = most_frequent_lang if lang_counts[most_frequent_lang] > 0 else 'en'
        
        # Строгая цепочка фолбека: Целевой язык -> en -> base -> пустой список
        if target_key in examples_db:
            selected_examples_list = list(examples_db[target_key])
        elif 'en' in examples_db:
            selected_examples_list = list(examples_db['en'])
        elif 'base' in examples_db:
            selected_examples_list = list(examples_db['base'])
        else:
            selected_examples_list = []
            
        examples_content = "\n".join(selected_examples_list) if isinstance(selected_examples_list, list) else str(selected_examples_list)

        # 2. Формируем пользовательский промпт, который теперь НИЧЕГО не знает о системных инструкциях.
        #    Он просто заполняет шаблон основного промпта.
        user_prompt = safe_format(
            self.custom_prompt,
            text=text_for_api,
            glossary=glossary_string if glossary_string and "Глоссарий пуст" not in glossary_string else "",
            format_examples=examples_content # <-- Вставляем динамические примеры
        )
        
        # 3. Собираем отчет (без изменений)
        
        token_estimator = TokenCounter()
        report_lines = ["--- ОТЧЕТ О СТРУКТУРЕ ПРОМПТА (v3 - Dynamic Examples) ---"]
        if self.system_instruction:
            report_lines.append("РЕЖИМ: Системные инструкции")
            est_sys_tokens = token_estimator.estimate_tokens(self.system_instruction)
            report_lines.append(f"Системная инструкция: ~{est_sys_tokens:,} токенов")
        else:
            report_lines.append("РЕЖИМ: Классический (весь промпт в одном запросе)")
    
        est_user_tokens = token_estimator.estimate_tokens(user_prompt)
        report_lines.append(f"Пользовательский контент: ~{est_user_tokens:,} токенов")
        report_lines.append(f"ИТОГО НА ВХОД: ~{(est_sys_tokens if self.system_instruction else 0) + est_user_tokens:,} токенов")
        
        debug_report = "\n".join(report_lines)
        return user_prompt, self.system_instruction, debug_report


